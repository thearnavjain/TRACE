"""Application-level candidate selection and comparison assembly."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from app.models.historical import CarDataSample, DriverMetadata, LapRecord, SessionMetadata
from app.schemas.comparison import (
    ComparisonResponse,
    DataProvenanceSummary,
    DriverComparison,
    LapSummary,
    LapTimeComparison,
    SessionSummary,
    TelemetryPoint,
)
from app.schemas.discovery import DriverLapOptions, LapOptionsResponse
from app.services.historical_contract import HistoricalDataProvider
from app.services.openf1_historical import lap_delta_seconds, select_timed_non_pit_out_candidate
from app.services.lap_selection import SelectionMode, choose_lap, make_lap_options
from app.services.telemetry_insights import largest_nearby_speed_difference, summarize_speed


class ComparisonError(Exception):
    def __init__(self, status_code: int, code: str, message: str, retry_after: str | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


SELECTION_POLICY = (
    "Fastest lap with a finite total time, all three finite sector times, and an explicit "
    "is_pit_out_lap=false. This identifies a candidate only; OpenF1 does not establish "
    "official validity or personal-best status."
)
MANUAL_SELECTION_POLICY = (
    "Manually selected provider lap with a finite lap time, all three finite sector times, "
    "an explicit is_pit_out_lap=false indicator, and a timezone-aware start timestamp for "
    "telemetry retrieval. This does not establish official validity or personal-best status."
)


def _event_matches(session: SessionMetadata, event: str) -> bool:
    wanted = event.strip().casefold()
    return wanted in {
        value.strip().casefold()
        for value in (session.location, session.circuit_name, session.country_name)
        if value
    }


def _provider_error(exc: Exception) -> ComparisonError:
    from app.services.openf1_historical import (
        HistoricalDataHTTPError,
        HistoricalDataRequestError,
        HistoricalDataResponseError,
    )
    if isinstance(exc, HistoricalDataHTTPError):
        if exc.status_code == 429:
            return ComparisonError(429, "provider_rate_limited", "OpenF1 rate limit reached. Wait before requesting another comparison.", exc.retry_after)
        if exc.status_code == 401:
            return ComparisonError(
                502,
                "provider_access_restricted",
                (
                    f"OpenF1 rejected the historical request to {exc.endpoint} with HTTP 401. "
                    "Historical access is normally unauthenticated, but OpenF1 may restrict "
                    "historical queries during an active live-session window. Try again after "
                    "the session window; TRACE has no OpenF1 credentials configured."
                ),
            )
        return ComparisonError(502, "provider_http_error", f"OpenF1 returned HTTP {exc.status_code} from {exc.endpoint}.")
    if isinstance(exc, HistoricalDataRequestError):
        return ComparisonError(502, "provider_request_failed", f"OpenF1 request failed ({exc.error_type}).")
    if isinstance(exc, HistoricalDataResponseError):
        return ComparisonError(502, "provider_response_invalid", str(exc))
    return ComparisonError(502, "provider_unavailable", "OpenF1 historical data is temporarily unavailable.")


def _session_drivers(
    provider: HistoricalDataProvider,
    session: SessionMetadata,
    identifiers: tuple[str, str],
) -> list[DriverMetadata]:
    if session.session_key is None:
        raise ComparisonError(502, "session_key_missing", "OpenF1 session metadata did not include a session key.")
    rows = provider.get_drivers(session.session_key)
    by_identifier = {
        (driver.acronym.strip().casefold() if driver.acronym and driver.acronym.strip() else str(driver.driver_number)): driver
        for driver in rows
        if driver.driver_number is not None
        and driver.session_key is not None
        and str(driver.session_key) == str(session.session_key)
    }
    result: list[DriverMetadata] = []
    for identifier in identifiers:
        driver = by_identifier.get(identifier.casefold())
        if driver is None:
            raise ComparisonError(404, "driver_not_found", f"Driver {identifier.upper()} was not present in the selected session.")
        result.append(driver)
    return result


def get_lap_options(
    provider: HistoricalDataProvider,
    *, session_key: str | int, driver_a: str, driver_b: str,
) -> LapOptionsResponse:
    """Return timing-only lap choices for the currently selected two drivers."""
    if driver_a.casefold() == driver_b.casefold():
        raise ComparisonError(422, "drivers_must_differ", "Choose two different driver identifiers.")
    try:
        session = provider.get_session(session_key)
        if session is None or str(session.session_key) != str(session_key):
            raise ComparisonError(404, "session_not_found", "No session with that key was found in OpenF1 metadata.")
        drivers = _session_drivers(provider, session, (driver_a, driver_b))
        laps = provider.get_laps(session.session_key)
    except ComparisonError:
        raise
    except Exception as exc:
        raise _provider_error(exc) from exc

    driver_options: list[DriverLapOptions] = []
    for identifier, driver in zip((driver_a, driver_b), drivers, strict=True):
        driver_laps = [
            lap for lap in laps
            if lap.driver_number == driver.driver_number
            and lap.session_key is not None
            and str(lap.session_key) == str(session.session_key)
        ]
        automatic = select_timed_non_pit_out_candidate(driver_laps)
        driver_options.append(DriverLapOptions(
            identifier=identifier,
            acronym=driver.acronym,
            driver_number=driver.driver_number,
            automatic_candidate_lap_number=automatic.lap_number if automatic else None,
            laps=make_lap_options(driver_laps),
        ))
    return LapOptionsResponse(
        session_key=session.session_key,
        selection_policy=SELECTION_POLICY,
        drivers=driver_options,
    )


def _sample_points(samples: Sequence[CarDataSample]) -> tuple[list[TelemetryPoint], list[str]]:
    parsed: list[tuple[datetime, CarDataSample]] = []
    warnings: list[str] = []
    for sample in samples:
        if not sample.raw_date:
            warnings.append("A car-data sample had no timestamp and was omitted from the time chart.")
            continue
        try:
            stamp = datetime.fromisoformat(sample.raw_date.replace("Z", "+00:00"))
        except ValueError:
            warnings.append("A car-data sample had an unparseable timestamp and was omitted from the time chart.")
            continue
        if stamp.tzinfo is None:
            warnings.append("A car-data sample had a timestamp without a timezone and was omitted from the time chart.")
            continue
        parsed.append((stamp.astimezone(timezone.utc), sample))

    parsed.sort(key=lambda item: item[0])
    if not parsed:
        return [], warnings
    origin = parsed[0][0]
    points = [
        TelemetryPoint(
            timestamp=stamp,
            elapsed_seconds=(stamp - origin).total_seconds(),
            speed_kph=sample.speed_kph,
            throttle_percent=sample.throttle_percent,
            brake_percent=sample.brake_percent,
            gear=sample.gear,
            drs_status=sample.drs,
            rpm=sample.rpm,
        )
        for stamp, sample in parsed
    ]
    if len({point.timestamp for point in points}) != len(points):
        warnings.append("Duplicate car-data timestamps are present; they are retained in the response.")
    if any(point.speed_kph is None for point in points):
        warnings.append("Some car-data samples have no usable speed measurement.")
    return points, warnings


def _driver_result(
    provider: HistoricalDataProvider,
    session: SessionMetadata,
    driver: DriverMetadata,
    candidate: LapRecord,
    selection_mode: SelectionMode,
) -> tuple[DriverComparison, list[str]]:
    if candidate.raw_date_start is None or candidate.lap_duration_seconds is None:
        raise ComparisonError(422, "lap_window_unavailable", f"{driver.acronym} candidate lap has no usable time window.")
    if candidate.lap_number is None or candidate.driver_number is None:
        raise ComparisonError(422, "lap_identifier_unavailable", f"{driver.acronym} candidate lap has no usable identifier.")
    try:
        start = datetime.fromisoformat(candidate.raw_date_start.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ComparisonError(422, "lap_window_unavailable", f"{driver.acronym} candidate lap start time cannot be parsed.") from exc
    if start.tzinfo is None:
        raise ComparisonError(422, "lap_window_unavailable", f"{driver.acronym} candidate lap start time has no timezone.")
    end = start + timedelta(seconds=candidate.lap_duration_seconds)
    samples = provider.get_car_data(
        session.session_key, driver.driver_number,
        date_start=start, date_end=end,
    )
    if not samples:
        raise ComparisonError(502, "telemetry_unavailable", f"OpenF1 returned no car-data samples for {driver.acronym}'s selected lap.")
    points, warnings = _sample_points(samples)
    if not points:
        raise ComparisonError(502, "telemetry_unusable", f"OpenF1 car-data timestamps could not be used for {driver.acronym}'s selected lap.")
    if not any(point.speed_kph is not None for point in points):
        raise ComparisonError(502, "speed_unavailable", f"OpenF1 returned no usable speed values for {driver.acronym}'s selected lap.")
    return DriverComparison(
        acronym=driver.acronym or str(driver.driver_number),
        full_name=driver.full_name,
        team_name=driver.team_name,
        driver_number=driver.driver_number,
        lap=LapSummary(
            lap_number=candidate.lap_number,
            lap_time_seconds=candidate.lap_duration_seconds,
            sector_1_seconds=candidate.sector_1_seconds,
            sector_2_seconds=candidate.sector_2_seconds,
            sector_3_seconds=candidate.sector_3_seconds,
            selection_mode=selection_mode,
            selection_policy=SELECTION_POLICY if selection_mode == "automatic" else MANUAL_SELECTION_POLICY,
        ),
        telemetry=points,
        speed_summary=summarize_speed(points),
    ), warnings


def create_comparison(
    provider: HistoricalDataProvider,
    *, driver_a: str, driver_b: str, year: int | None = None,
    event: str | None = None, session_name: str | None = None,
    session_key: str | int | None = None,
    lap_a: int | None = None,
    lap_b: int | None = None,
    fetched_at: datetime | None = None,
) -> ComparisonResponse:
    if driver_a.casefold() == driver_b.casefold():
        raise ComparisonError(422, "drivers_must_differ", "Choose two different driver identifiers.")
    try:
        if session_key is not None:
            session = provider.get_session(session_key)
            if session is None:
                raise ComparisonError(404, "session_not_found", "No session with that key was found in OpenF1 metadata.")
        else:
            if year is None or event is None or session_name is None:
                raise ComparisonError(422, "session_selection_required", "Provide session_key or the legacy year, event and session parameters.")
            sessions = provider.find_sessions(year=year, session_name=session_name)
            matches = [item for item in sessions if item.year == year and item.session_name == session_name and _event_matches(item, event)]
            if not matches:
                raise ComparisonError(404, "session_not_found", "No matching session was found in OpenF1 metadata.")
            if len(matches) != 1:
                raise ComparisonError(422, "session_ambiguous", "The event and session selection matches multiple sessions; use a session key.")
            session = matches[0]
        if session_key is not None and session.session_key != session_key and str(session.session_key) != str(session_key):
            raise ComparisonError(404, "session_not_found", "OpenF1 metadata did not confirm the requested session key.")
        if session.year is None and year is None:
            raise ComparisonError(502, "session_year_missing", "OpenF1 session metadata did not include a year.")
        if session.session_key is None:
            raise ComparisonError(502, "session_key_missing", "OpenF1 session metadata did not include a session key.")
        driver_meta = _session_drivers(provider, session, (driver_a, driver_b))
        laps = provider.get_laps(session.session_key)
        selected: list[LapRecord] = []
        selection_modes: list[SelectionMode] = []
        for driver, requested_lap in zip(driver_meta, (lap_a, lap_b), strict=True):
            driver_laps = [
                lap for lap in laps
                if lap.driver_number == driver.driver_number
                and lap.session_key is not None
                and str(lap.session_key) == str(session.session_key)
            ]
            candidate, mode, status, code, message = choose_lap(driver_laps, requested_lap)
            if candidate is None:
                raise ComparisonError(status, code, f"{driver.acronym or driver.driver_number}: {message}")
            selected.append(candidate)
            selection_modes.append(mode)
        if selected[0].lap_duration_seconds is None or selected[1].lap_duration_seconds is None:
            raise ComparisonError(422, "lap_time_unavailable", "A selected candidate lap has no lap time.")
        delta = lap_delta_seconds(selected[0], selected[1])
        if delta is None:
            raise ComparisonError(422, "lap_time_unavailable", "A selected candidate lap time is non-finite or unavailable.")
        result_a, warnings_a = _driver_result(provider, session, driver_meta[0], selected[0], selection_modes[0])
        result_b, warnings_b = _driver_result(provider, session, driver_meta[1], selected[1], selection_modes[1])
        speed_insight = largest_nearby_speed_difference(
            result_a.telemetry,
            result_b.telemetry,
            driver_a=result_a.acronym,
            driver_b=result_b.acronym,
        )
    except ComparisonError:
        raise
    except Exception as exc:
        raise _provider_error(exc) from exc

    event_label = session.location or session.circuit_name or session.country_name or event
    return ComparisonResponse(
        session=SessionSummary(
            session_key=session.session_key,
            year=session.year if session.year is not None else year,
            session_name=session.session_name or session_name or "Session",
            event=event_label or "Event unavailable",
            location=session.location,
            circuit_name=session.circuit_name,
            date_start=session.raw_date_start,
        ),
        driver_a=result_a,
        driver_b=result_b,
        lap_time_comparison=LapTimeComparison(
            delta_seconds=delta,
            faster_driver="equal" if delta == 0 else result_a.acronym if delta > 0 else result_b.acronym,
        ),
        speed_difference_insight=speed_insight,
        provenance=DataProvenanceSummary(fetched_at=fetched_at or datetime.now(timezone.utc)),
        warnings=[
            "Selected laps are candidates; OpenF1 does not establish official lap validity or personal-best status.",
            "Distance-based comparison is unavailable; chart coordinates are elapsed time from each trace's first returned sample.",
            "Speed differences use nearest speed-valid samples within a bounded elapsed-time tolerance; readings are not guaranteed to represent the same circuit position.",
            *warnings_a,
            *warnings_b,
        ],
    )
