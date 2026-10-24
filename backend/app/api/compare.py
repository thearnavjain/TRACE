from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.schemas.comparison import ComparisonResponse
from app.schemas.discovery import LapOptionsResponse
from app.services.comparison import ComparisonError, create_comparison, get_lap_options
from app.services.openf1_historical import OpenF1HistoricalData

router = APIRouter()


def get_provider() -> Iterator[OpenF1HistoricalData]:
    with OpenF1HistoricalData() as provider:
        yield provider


@router.get(
    "/api/laps",
    response_model=LapOptionsResponse,
    summary="List timing-only candidate laps for the selected drivers",
    description=(
        "Returns provider lap timing metadata only; it does not fetch telemetry. "
        "Selectable manual laps satisfy TRACE's timed, complete-sector, explicitly "
        "non-pit-out policy and have a timezone-aware start timestamp."
    ),
)
def list_lap_options(
    response: Response,
    session_key: Annotated[int, Query(gt=0)],
    driver_a: Annotated[str, Query(pattern=r"^[A-Za-z0-9]{1,3}$")],
    driver_b: Annotated[str, Query(pattern=r"^[A-Za-z0-9]{1,3}$")],
    provider: OpenF1HistoricalData = Depends(get_provider),
) -> LapOptionsResponse:
    try:
        result = get_lap_options(
            provider,
            session_key=session_key,
            driver_a=driver_a.upper(),
            driver_b=driver_b.upper(),
        )
    except ComparisonError as exc:
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
            headers=headers,
        ) from exc
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "/api/compare",
    response_model=ComparisonResponse,
    summary="Compare two candidate laps from a historical session",
    description=(
        "Resolves a session from OpenF1 metadata, selects each driver's fastest timed, "
        "three-sector, explicitly non-pit-out lap, then retrieves timestamped car data. "
        "lap_a and lap_b optionally select explicit lap numbers while omitted values retain "
        "automatic selection. Candidate metadata does not establish official validity or "
        "personal-best status. Legacy event matching uses location, circuit short name, or country."
    ),
)
def compare(
    response: Response,
    driver_a: Annotated[str, Query(pattern=r"^[A-Za-z0-9]{1,3}$")],
    driver_b: Annotated[str, Query(pattern=r"^[A-Za-z0-9]{1,3}$")],
    session_key: Annotated[int | None, Query(gt=0)] = None,
    year: Annotated[int | None, Query(ge=2023, le=datetime.now(timezone.utc).year)] = None,
    event: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
    session: Annotated[str | None, Query(min_length=3, max_length=32)] = None,
    lap_a: Annotated[int | None, Query(gt=0)] = None,
    lap_b: Annotated[int | None, Query(gt=0)] = None,
    provider: OpenF1HistoricalData = Depends(get_provider),
) -> ComparisonResponse:
    try:
        result = create_comparison(
            provider,
            session_key=session_key,
            lap_a=lap_a,
            lap_b=lap_b,
            year=year,
            event=event.strip() if event else None,
            session_name=session.strip() if session else None,
            driver_a=driver_a.upper(),
            driver_b=driver_b.upper(),
        )
    except ComparisonError as exc:
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
            headers=headers,
        ) from exc
    if result.provenance.fetched_at:
        response.headers["Cache-Control"] = "no-store"
    return result

\n
