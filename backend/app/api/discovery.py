"""Provider-backed metadata for year, event, session and driver selectors."""

from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.compare import get_provider
from app.models.historical import DriverMetadata, MeetingMetadata, SessionMetadata
from app.schemas.discovery import (
    DriverOption,
    DriversResponse,
    MeetingOption,
    SessionOption,
    SessionsResponse,
)
from app.services.openf1_historical import (
    HistoricalDataHTTPError,
    HistoricalDataRequestError,
    HistoricalDataResponseError,
    OpenF1HistoricalData,
)

router = APIRouter()


def _provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HistoricalDataHTTPError):
        if exc.status_code == 429:
            return HTTPException(
                429,
                detail={"code": "provider_rate_limited", "message": "OpenF1 rate limit reached. Wait before requesting metadata again."},
                headers={"Retry-After": exc.retry_after} if exc.retry_after else None,
            )
        if exc.status_code == 401:
            return HTTPException(
                502,
                detail={
                    "code": "provider_access_restricted",
                    "message": (
                        f"OpenF1 rejected the historical request to {exc.endpoint} with HTTP 401. "
                        "Historical access is normally unauthenticated, but OpenF1 may restrict "
                        "historical queries during an active live-session window. Try again after "
                        "the session window; TRACE has no OpenF1 credentials configured."
                    ),
                },
            )
        return HTTPException(502, detail={"code": "provider_http_error", "message": f"OpenF1 returned HTTP {exc.status_code} from {exc.endpoint}."})
    if isinstance(exc, HistoricalDataRequestError):
        return HTTPException(502, detail={"code": "provider_request_failed", "message": f"OpenF1 request failed ({exc.error_type})."})
    if isinstance(exc, HistoricalDataResponseError):
        return HTTPException(502, detail={"code": "provider_response_invalid", "message": str(exc)})
    return HTTPException(502, detail={"code": "provider_unavailable", "message": "OpenF1 metadata is temporarily unavailable."})


def _clean_label(*parts: str | None, fallback: str) -> str:
    return " · ".join(part.strip() for part in parts if part and part.strip()) or fallback


@router.get("/api/sessions", response_model=SessionsResponse, summary="List available meetings and sessions for a year")
def list_sessions(
    year: Annotated[int, Query(ge=2023, le=datetime.now(timezone.utc).year)],
    response: Response,
    provider: OpenF1HistoricalData = Depends(get_provider),
) -> SessionsResponse:
    try:
        meetings = provider.get_meetings(year)
        sessions = provider.find_sessions(year=year)
    except Exception as exc:
        raise _provider_error(exc) from exc

    by_key: dict[str | int | None, MeetingMetadata] = {m.meeting_key: m for m in meetings if m.meeting_key is not None}
    grouped: dict[str | int | None, list[SessionMetadata]] = defaultdict(list)
    for session in sessions:
        if session.year == year and session.session_key is not None:
            grouped[session.meeting_key].append(session)

    results: list[MeetingOption] = []
    for key, session_rows in grouped.items():
        meeting = by_key.get(key)
        fallback = next((s.location or s.circuit_name or s.country_name for s in session_rows if s.location or s.circuit_name or s.country_name), f"Meeting {key}" if key is not None else "Unlabelled meeting")
        results.append(MeetingOption(
            meeting_key=key,
            meeting_name=(meeting.meeting_name if meeting else None) or fallback,
            meeting_official_name=meeting.meeting_official_name if meeting else None,
            country_name=(meeting.country_name if meeting else None) or next((s.country_name for s in session_rows if s.country_name), None),
            location=(meeting.location if meeting else None) or next((s.location for s in session_rows if s.location), None),
            circuit_name=(meeting.circuit_name if meeting else None) or next((s.circuit_name for s in session_rows if s.circuit_name), None),
            date_start=(meeting.raw_date_start if meeting else None) or next((s.raw_date_start for s in session_rows if s.raw_date_start), None),
            sessions=[SessionOption(
                session_key=s.session_key, meeting_key=s.meeting_key, year=year,
                session_name=s.session_name or s.session_type or "Session",
                session_type=s.session_type, date_start=s.raw_date_start,
            ) for s in sorted(session_rows, key=lambda row: (row.raw_date_start or "", str(row.session_key)))],
        ))
    results.sort(key=lambda meeting: (meeting.date_start or "", str(meeting.meeting_key)))
    response.headers["Cache-Control"] = "no-store"
    return SessionsResponse(year=year, meetings=results)


def _driver_identifier(driver: DriverMetadata) -> str | None:
    if driver.acronym and driver.acronym.strip():
        return driver.acronym.strip().upper()
    return str(driver.driver_number) if driver.driver_number is not None else None


@router.get("/api/drivers", response_model=DriversResponse, summary="List drivers present in a historical session")
def list_drivers(
    session_key: Annotated[int, Query(gt=0)],
    response: Response,
    provider: OpenF1HistoricalData = Depends(get_provider),
) -> DriversResponse:
    try:
        rows = provider.get_drivers(session_key)
    except Exception as exc:
        raise _provider_error(exc) from exc
    unique: dict[str, DriverMetadata] = {}
    for driver in rows:
        if driver.session_key is None or str(driver.session_key) != str(session_key):
            continue
        identifier = _driver_identifier(driver)
        if identifier:
            unique.setdefault(identifier.casefold(), driver)
    drivers = [DriverOption(
        identifier=_driver_identifier(driver) or "",
        driver_number=driver.driver_number,
        acronym=driver.acronym,
        full_name=driver.full_name,
        team_name=driver.team_name,
    ) for driver in unique.values()]
    drivers.sort(key=lambda driver: (driver.acronym is None, driver.acronym or driver.full_name or driver.identifier))
    response.headers["Cache-Control"] = "no-store"
    return DriversResponse(session_key=session_key, drivers=drivers)
