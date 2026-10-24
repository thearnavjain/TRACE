"""Stable public response for a historical two-driver comparison."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SessionSummary(BaseModel):
    session_key: str | int
    year: int
    session_name: str
    event: str
    location: str | None
    circuit_name: str | None
    date_start: str | None


class LapSummary(BaseModel):
    lap_number: int
    lap_time_seconds: float
    sector_1_seconds: float
    sector_2_seconds: float
    sector_3_seconds: float
    validity: Literal["unknown"] = "unknown"
    selection_mode: Literal["automatic", "manual"] = "automatic"
    selection_policy: str


class TelemetryPoint(BaseModel):
    timestamp: datetime
    elapsed_seconds: float
    speed_kph: float | None
    throttle_percent: float | None
    brake_percent: float | None
    gear: int | None
    drs_status: int | None
    rpm: int | None


class SpeedSummary(BaseModel):
    minimum_kph: float | None
    maximum_kph: float | None
    mean_kph: float | None
    valid_sample_count: int


class DriverComparison(BaseModel):
    model_config = ConfigDict(title="Driver candidate lap and timestamped telemetry")

    acronym: str
    full_name: str | None
    team_name: str | None
    driver_number: int
    lap: LapSummary
    telemetry: list[TelemetryPoint]
    speed_summary: SpeedSummary


class LapTimeComparison(BaseModel):
    convention: Literal["driver_b_minus_driver_a"] = "driver_b_minus_driver_a"
    delta_seconds: float
    faster_driver: str | Literal["equal"]


class SpeedDifferenceInsight(BaseModel):
    available: bool
    unavailable_reason: str | None = None
    comparison_tolerance_seconds: float
    shared_elapsed_range_seconds: tuple[float, float] | None = None
    driver_a_speed_kph: float | None = None
    driver_b_speed_kph: float | None = None
    driver_b_minus_driver_a_kph: float | None = None
    absolute_difference_kph: float | None = None
    driver_a_elapsed_seconds: float | None = None
    driver_b_elapsed_seconds: float | None = None
    time_separation_seconds: float | None = None
    time_separation_basis: Literal["elapsed_seconds"] = "elapsed_seconds"
    earlier_in_elapsed_time_driver: str | None = None
    later_in_elapsed_time_driver: str | None = None
    driver_a_timestamp: datetime | None = None
    driver_b_timestamp: datetime | None = None


class DataProvenanceSummary(BaseModel):
    provider: Literal["openf1"] = "openf1"
    fetched_at: datetime
    source_freshness: None = None


class ComparisonResponse(BaseModel):
    session: SessionSummary
    driver_a: DriverComparison
    driver_b: DriverComparison
    lap_time_comparison: LapTimeComparison
    speed_difference_insight: SpeedDifferenceInsight
    provenance: DataProvenanceSummary
    warnings: list[str]

\n
