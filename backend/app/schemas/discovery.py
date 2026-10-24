"""Typed discovery responses for metadata-driven historical selectors."""

from pydantic import BaseModel


class SessionOption(BaseModel):
    session_key: str | int
    meeting_key: str | int | None
    year: int
    session_name: str
    session_type: str | None
    date_start: str | None


class MeetingOption(BaseModel):
    meeting_key: str | int | None
    meeting_name: str
    meeting_official_name: str | None
    country_name: str | None
    location: str | None
    circuit_name: str | None
    date_start: str | None
    sessions: list[SessionOption]


class SessionsResponse(BaseModel):
    year: int
    source: str = "openf1"
    meetings: list[MeetingOption]


class DriverOption(BaseModel):
    identifier: str
    driver_number: int | None
    acronym: str | None
    full_name: str | None
    team_name: str | None


class DriversResponse(BaseModel):
    session_key: str | int
    source: str = "openf1"
    drivers: list[DriverOption]


class LapOption(BaseModel):
    lap_number: int | None
    lap_time_seconds: float | None
    sector_1_seconds: float | None
    sector_2_seconds: float | None
    sector_3_seconds: float | None
    is_pit_out_lap: bool | None
    selectable: bool
    unavailable_reason: str | None


class DriverLapOptions(BaseModel):
    identifier: str
    acronym: str | None
    driver_number: int
    automatic_candidate_lap_number: int | None
    laps: list[LapOption]


class LapOptionsResponse(BaseModel):
    session_key: str | int
    selection_policy: str
    validity_note: str = "OpenF1 does not establish official lap validity or personal-best status."
    drivers: list[DriverLapOptions]
