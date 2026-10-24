"""OpenF1 adapter for unauthenticated historical session data.

This adapter returns normalized provider-neutral records. It deliberately does
not decide which laps are officially valid or derive track distance.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from email.utils import parsedate_to_datetime
from time import monotonic, sleep, time
from typing import Any, Callable
from urllib.parse import quote

import httpx

from app.models.historical import (
    CarDataSample,
    DataProvenance,
    DriverMetadata,
    LapQuality,
    LapRecord,
    LocationSample,
    MeetingMetadata,
    SessionMetadata,
)


OPENF1_BASE_URL = "https://api.openf1.org/v1"


class HistoricalDataError(RuntimeError):
    """Base error for explicit provider request and response failures."""


class HistoricalDataHTTPError(HistoricalDataError):
    def __init__(
        self,
        endpoint: str,
        status_code: int,
        retry_after: str | None = None,
        attempts: int = 1,
    ) -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        self.retry_after = retry_after
        self.attempts = attempts
        super().__init__(f"OpenF1 endpoint {endpoint} returned HTTP {status_code}")


class HistoricalDataRequestError(HistoricalDataError):
    def __init__(self, endpoint: str, error_type: str) -> None:
        self.endpoint = endpoint
        self.error_type = error_type
        super().__init__(f"OpenF1 request to {endpoint} failed ({error_type})")


class HistoricalDataResponseError(HistoricalDataError):
    pass


def _optional_number(value: Any, field_name: str, warnings: list[str]) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        warnings.append(f"{field_name} was not a numeric measurement")
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        warnings.append(f"{field_name} could not be parsed as a number")
        return None
    if not math.isfinite(result):
        warnings.append(f"{field_name} was non-finite")
        return None
    return result


def _optional_integer(value: Any, field_name: str, warnings: list[str]) -> int | None:
    number = _optional_number(value, field_name, warnings)
    if number is None:
        return None
    if not number.is_integer():
        warnings.append(f"{field_name} was not an integer")
        return None
    return int(number)


def _identifier(value: Any) -> str | int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return value


def _date_filter(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else value


def _time_filtered_url(
    endpoint: str,
    session_key: str | int,
    driver_number: int,
    date_start: datetime | str,
    date_end: datetime | str,
) -> str:
    """Build OpenF1's operator-suffix query syntax (no '=' after > or <)."""
    start = quote(_date_filter(date_start), safe="-T:.")
    end = quote(_date_filter(date_end), safe="-T:.")
    session = quote(str(session_key), safe="")
    return (
        f"{endpoint}?session_key={session}&driver_number={driver_number}"
        f"&date%3E{start}&date%3C{end}"
    )


def _raw_timestamp(value: Any, field_name: str, warnings: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        warnings.append(f"{field_name} was not a timestamp string; raw value preserved")
        return str(value)
    return value


def _record_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def normalize_session(record: Mapping[str, Any]) -> SessionMetadata:
    warnings: list[str] = []
    session_key = _identifier(record.get("session_key"))
    if session_key is None:
        warnings.append("session_key is missing or invalid")
    year = _optional_integer(record.get("year"), "year", warnings)
    return SessionMetadata(
        session_key=session_key,
        meeting_key=_identifier(record.get("meeting_key")),
        year=year,
        session_name=_as_string(record.get("session_name")),
        session_type=_as_string(record.get("session_type")),
        country_name=_as_string(record.get("country_name")),
        location=_as_string(record.get("location")),
        circuit_name=_as_string(record.get("circuit_short_name")),
        raw_date_start=_raw_timestamp(record.get("date_start"), "date_start", warnings),
        raw_date_end=_raw_timestamp(record.get("date_end"), "date_end", warnings),
        provenance=DataProvenance("openf1", "/sessions", session_key),
        warnings=tuple(warnings),
        raw_record=dict(record),
    )


def normalize_meeting(record: Mapping[str, Any]) -> MeetingMetadata:
    warnings: list[str] = []
    key = _identifier(record.get("meeting_key"))
    if key is None:
        warnings.append("meeting_key is missing or invalid")
    return MeetingMetadata(
        meeting_key=key,
        year=_optional_integer(record.get("year"), "year", warnings),
        meeting_name=_as_string(record.get("meeting_name")),
        meeting_official_name=_as_string(record.get("meeting_official_name")),
        country_name=_as_string(record.get("country_name")),
        location=_as_string(record.get("location")),
        circuit_name=_as_string(record.get("circuit_short_name")),
        raw_date_start=_raw_timestamp(record.get("date_start"), "date_start", warnings),
        provenance=DataProvenance("openf1", "/meetings", key),
        warnings=tuple(warnings),
        raw_record=dict(record),
    )


def normalize_driver(record: Mapping[str, Any]) -> DriverMetadata:
    warnings: list[str] = []
    return DriverMetadata(
        driver_number=_optional_integer(record.get("driver_number"), "driver_number", warnings),
        acronym=_as_string(record.get("name_acronym")),
        full_name=_as_string(record.get("full_name")),
        team_name=_as_string(record.get("team_name")),
        session_key=_identifier(record.get("session_key")),
        meeting_key=_identifier(record.get("meeting_key")),
        provenance=DataProvenance(
            "openf1", "/drivers", _identifier(record.get("driver_number"))
        ),
        warnings=tuple(warnings),
        raw_record=dict(record),
    )


def normalize_lap(record: Mapping[str, Any]) -> LapRecord:
    warnings: list[str] = []
    lap_key = _identifier(record.get("lap_number"))
    validity_fields = {"is_accurate", "is_personal_best", "is_deleted"}
    supplied = {
        name: record[name]
        for name in ("is_pit_out_lap", *validity_fields)
        if name in record
    }
    if not validity_fields.intersection(record):
        warnings.append("Official lap validity/personal-best status is not provided")
    pit_value = record.get("is_pit_out_lap")
    if pit_value is not None and not isinstance(pit_value, bool):
        warnings.append("is_pit_out_lap was not a boolean")
        pit_value = None

    return LapRecord(
        driver_number=_optional_integer(record.get("driver_number"), "driver_number", warnings),
        session_key=_identifier(record.get("session_key")),
        meeting_key=_identifier(record.get("meeting_key")),
        lap_number=_optional_integer(record.get("lap_number"), "lap_number", warnings),
        raw_date_start=_raw_timestamp(record.get("date_start"), "date_start", warnings),
        lap_duration_seconds=_optional_number(record.get("lap_duration"), "lap_duration", warnings),
        sector_1_seconds=_optional_number(record.get("duration_sector_1"), "duration_sector_1", warnings),
        sector_2_seconds=_optional_number(record.get("duration_sector_2"), "duration_sector_2", warnings),
        sector_3_seconds=_optional_number(record.get("duration_sector_3"), "duration_sector_3", warnings),
        quality=LapQuality(
            is_pit_out_lap=pit_value,
            validity_status=None,
            personal_best=None,
            source_indicators=supplied,
        ),
        source_identifiers={
            "session_key": _identifier(record.get("session_key")),
            "meeting_key": _identifier(record.get("meeting_key")),
            "driver_number": _identifier(record.get("driver_number")),
            "lap_number": lap_key,
        },
        provenance=DataProvenance("openf1", "/laps", lap_key),
        warnings=tuple(warnings),
        raw_record=dict(record),
    )


def normalize_car_data(record: Mapping[str, Any]) -> CarDataSample:
    warnings: list[str] = []
    return CarDataSample(
        raw_date=_raw_timestamp(record.get("date"), "date", warnings),
        session_key=_identifier(record.get("session_key")),
        meeting_key=_identifier(record.get("meeting_key")),
        driver_number=_optional_integer(record.get("driver_number"), "driver_number", warnings),
        speed_kph=_optional_number(record.get("speed"), "speed", warnings),
        throttle_percent=_optional_number(record.get("throttle"), "throttle", warnings),
        brake_percent=_optional_number(record.get("brake"), "brake", warnings),
        gear=_optional_integer(record.get("n_gear"), "n_gear", warnings),
        drs=_optional_integer(record.get("drs"), "drs", warnings),
        rpm=_optional_integer(record.get("rpm"), "rpm", warnings),
        provenance=DataProvenance("openf1", "/car_data", _identifier(record.get("driver_number"))),
        warnings=tuple(warnings),
        raw_record=dict(record),
    )


def normalize_location(record: Mapping[str, Any]) -> LocationSample:
    warnings: list[str] = []
    return LocationSample(
        raw_date=_raw_timestamp(record.get("date"), "date", warnings),
        session_key=_identifier(record.get("session_key")),
        meeting_key=_identifier(record.get("meeting_key")),
        driver_number=_optional_integer(record.get("driver_number"), "driver_number", warnings),
        x=_optional_number(record.get("x"), "x", warnings),
        y=_optional_number(record.get("y"), "y", warnings),
        z=_optional_number(record.get("z"), "z", warnings),
        coordinate_unit=None,
        provenance=DataProvenance("openf1", "/location", _identifier(record.get("driver_number"))),
        warnings=tuple(warnings),
        raw_record=dict(record),
    )


def select_timed_non_pit_out_candidate(laps: Sequence[LapRecord]) -> LapRecord | None:
    """Choose a transparent candidate; this does not establish official validity."""
    candidates = [
        lap
        for lap in laps
        if lap.lap_duration_seconds is not None
        and math.isfinite(lap.lap_duration_seconds)
        and lap.sector_1_seconds is not None
        and math.isfinite(lap.sector_1_seconds)
        and lap.sector_2_seconds is not None
        and math.isfinite(lap.sector_2_seconds)
        and lap.sector_3_seconds is not None
        and math.isfinite(lap.sector_3_seconds)
        and lap.quality.is_pit_out_lap is False
    ]
    return min(candidates, key=lambda lap: lap.lap_duration_seconds, default=None)


def lap_delta_seconds(lap_a: LapRecord | None, lap_b: LapRecord | None) -> float | None:
    """Return B minus A seconds; positive means lap A was faster."""
    if lap_a is None or lap_b is None:
        return None
    a, b = lap_a.lap_duration_seconds, lap_b.lap_duration_seconds
    if a is None or b is None or not math.isfinite(a) or not math.isfinite(b):
        return None
    return b - a


def _as_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


class OpenF1HistoricalData:
    """Synchronous OpenF1 client paced below documented free-tier request limits."""

    def __init__(
        self,
        *,
        base_url: str = OPENF1_BASE_URL,
        timeout: httpx.Timeout | float = httpx.Timeout(20.0, connect=5.0),
        transport: httpx.BaseTransport | None = None,
        min_request_interval: float = 2.1,
        max_429_retries: int = 1,
        max_retry_after_seconds: float = 60.0,
        _monotonic: Callable[[], float] = monotonic,
        _sleep: Callable[[float], None] = sleep,
        _wall_time: Callable[[], float] = time,
    ) -> None:
        if min_request_interval < 0:
            raise ValueError("min_request_interval cannot be negative")
        if max_429_retries not in (0, 1):
            raise ValueError("max_429_retries must be 0 or 1")
        if max_retry_after_seconds < 0:
            raise ValueError("max_retry_after_seconds cannot be negative")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )
        self._min_request_interval = min_request_interval
        self._max_429_retries = max_429_retries
        self._max_retry_after_seconds = max_retry_after_seconds
        self._monotonic = _monotonic
        self._sleep = _sleep
        self._wall_time = _wall_time
        self._last_request_started: float | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenF1HistoricalData:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _get_records(
        self, endpoint: str, params: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        endpoint_path = endpoint.split("?", 1)[0]
        response: httpx.Response | None = None
        for attempt in range(self._max_429_retries + 1):
            self._pace_request()
            try:
                response = self._client.get(endpoint, params=params)
            except httpx.HTTPError as exc:
                raise HistoricalDataRequestError(endpoint_path, type(exc).__name__) from exc
            if response.status_code != 429 or attempt >= self._max_429_retries:
                break
            retry_after = response.headers.get("Retry-After")
            delay = self._retry_delay(retry_after)
            if delay is None or delay > self._max_retry_after_seconds:
                break
            self._sleep(delay)

        assert response is not None
        if not response.is_success:
            raise HistoricalDataHTTPError(
                endpoint_path,
                response.status_code,
                retry_after=response.headers.get("Retry-After"),
                attempts=attempt + 1,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise HistoricalDataResponseError(
                f"OpenF1 endpoint {endpoint_path} returned invalid JSON"
            ) from exc
        if not isinstance(body, list) or any(not isinstance(row, dict) for row in body):
            raise HistoricalDataResponseError(
                f"OpenF1 endpoint {endpoint_path} returned an unexpected JSON shape"
            )
        return body

    def _pace_request(self) -> None:
        now = self._monotonic()
        if self._last_request_started is not None:
            remaining = self._min_request_interval - (now - self._last_request_started)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_started = self._monotonic()

    def _retry_delay(self, value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                return None
            seconds = retry_at.timestamp() - self._wall_time()
        if not math.isfinite(seconds):
            return None
        return max(0.0, seconds)

    def find_sessions(
        self, *, year: int, session_name: str | None = None,
        country_name: str | None = None, meeting_key: str | int | None = None,
        session_key: str | int | None = None,
    ) -> list[SessionMetadata]:
        params: dict[str, Any] = {"year": year}
        if session_name is not None:
            params["session_name"] = session_name
        if country_name is not None:
            params["country_name"] = country_name
        if meeting_key is not None:
            params["meeting_key"] = meeting_key
        if session_key is not None:
            params["session_key"] = session_key
        rows = self._get_records(
            "/sessions", params,
        )
        return [normalize_session(row) for row in rows]

    def get_meetings(self, year: int) -> list[MeetingMetadata]:
        return [
            normalize_meeting(row)
            for row in self._get_records("/meetings", {"year": year})
        ]

    def get_session(self, session_key: str | int) -> SessionMetadata | None:
        rows = self._get_records("/sessions", {"session_key": session_key})
        if not rows:
            return None
        return normalize_session(rows[0])

    def get_drivers(self, session_key: str | int) -> list[DriverMetadata]:
        return [
            normalize_driver(row)
            for row in self._get_records("/drivers", {"session_key": session_key})
        ]

    def get_laps(
        self, session_key: str | int, driver_number: int | None = None
    ) -> list[LapRecord]:
        params: dict[str, Any] = {"session_key": session_key}
        if driver_number is not None:
            params["driver_number"] = driver_number
        return [normalize_lap(row) for row in self._get_records("/laps", params)]

    def get_car_data(
        self,
        session_key: str | int,
        driver_number: int,
        *,
        date_start: datetime | str,
        date_end: datetime | str,
    ) -> list[CarDataSample]:
        return [
            normalize_car_data(row)
            for row in self._get_records(
                _time_filtered_url(
                    "/car_data", session_key, driver_number, date_start, date_end
                )
            )
        ]

    def get_location(
        self,
        session_key: str | int,
        driver_number: int,
        *,
        date_start: datetime | str,
        date_end: datetime | str,
    ) -> list[LocationSample]:
        return [
            normalize_location(row)
            for row in self._get_records(
                _time_filtered_url(
                    "/location", session_key, driver_number, date_start, date_end
                )
            )
        ]
