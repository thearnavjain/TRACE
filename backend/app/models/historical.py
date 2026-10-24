"""Provider-neutral models for historical session data.

Provider timestamps are retained as raw ISO-8601 strings. Optional fields use
``None`` when a provider omits a value or it cannot be normalized safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class DataProvenance:
    provider: str
    endpoint: str
    source_identifier: str | int | None = None
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class MeetingMetadata:
    meeting_key: str | int | None
    year: int | None
    meeting_name: str | None
    meeting_official_name: str | None
    country_name: str | None
    location: str | None
    circuit_name: str | None
    raw_date_start: str | None
    provenance: DataProvenance
    warnings: tuple[str, ...] = ()
    raw_record: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class SessionMetadata:
    session_key: str | int | None
    meeting_key: str | int | None
    year: int | None
    session_name: str | None
    session_type: str | None
    country_name: str | None
    location: str | None
    circuit_name: str | None
    raw_date_start: str | None
    raw_date_end: str | None
    provenance: DataProvenance
    warnings: tuple[str, ...] = ()
    raw_record: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class DriverMetadata:
    driver_number: int | None
    acronym: str | None
    full_name: str | None
    team_name: str | None
    session_key: str | int | None
    meeting_key: str | int | None
    provenance: DataProvenance
    warnings: tuple[str, ...] = ()
    raw_record: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class LapQuality:
    """Only records indicators actually supplied by the source."""

    is_pit_out_lap: bool | None = None
    validity_status: bool | None = None
    personal_best: bool | None = None
    source_indicators: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LapRecord:
    driver_number: int | None
    session_key: str | int | None
    meeting_key: str | int | None
    lap_number: int | None
    raw_date_start: str | None
    lap_duration_seconds: float | None
    sector_1_seconds: float | None
    sector_2_seconds: float | None
    sector_3_seconds: float | None
    quality: LapQuality
    source_identifiers: Mapping[str, str | int | None]
    provenance: DataProvenance
    warnings: tuple[str, ...] = ()
    raw_record: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class CarDataSample:
    raw_date: str | None
    session_key: str | int | None
    meeting_key: str | int | None
    driver_number: int | None
    speed_kph: float | None
    throttle_percent: float | None
    brake_percent: float | None
    gear: int | None
    drs: int | None
    rpm: int | None
    provenance: DataProvenance
    warnings: tuple[str, ...] = ()
    raw_record: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class LocationSample:
    raw_date: str | None
    session_key: str | int | None
    meeting_key: str | int | None
    driver_number: int | None
    x: float | None
    y: float | None
    z: float | None
    coordinate_unit: str | None
    provenance: DataProvenance
    warnings: tuple[str, ...] = ()
    raw_record: Mapping[str, Any] = field(default_factory=dict, repr=False)
