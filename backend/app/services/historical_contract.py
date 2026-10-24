"""Structural provider-neutral contract for historical F1 data adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from app.models.historical import (
    CarDataSample,
    DriverMetadata,
    LapRecord,
    LocationSample,
    MeetingMetadata,
    SessionMetadata,
)


class HistoricalDataProvider(Protocol):
    def find_sessions(
        self, *, year: int, session_name: str | None = None,
        country_name: str | None = None, meeting_key: str | int | None = None,
        session_key: str | int | None = None,
    ) -> Sequence[SessionMetadata]: ...

    def get_meetings(self, year: int) -> Sequence[MeetingMetadata]: ...

    def get_session(self, session_key: str | int) -> SessionMetadata | None: ...

    def get_drivers(self, session_key: str | int) -> Sequence[DriverMetadata]: ...

    def get_laps(
        self, session_key: str | int, driver_number: int | None = None
    ) -> Sequence[LapRecord]: ...

    def get_car_data(
        self,
        session_key: str | int,
        driver_number: int,
        *,
        date_start: datetime | str,
        date_end: datetime | str,
    ) -> Sequence[CarDataSample]: ...

    def get_location(
        self,
        session_key: str | int,
        driver_number: int,
        *,
        date_start: datetime | str,
        date_end: datetime | str,
    ) -> Sequence[LocationSample]: ...
