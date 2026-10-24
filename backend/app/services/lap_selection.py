"""Candidate lap option and explicit selection rules shared by API workflows."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from math import isfinite
from typing import Literal, Sequence

from app.models.historical import LapRecord
from app.schemas.discovery import LapOption
from app.services.openf1_historical import select_timed_non_pit_out_candidate


SelectionMode = Literal["automatic", "manual"]


def lap_option_reason(lap: LapRecord, *, duplicate_number: bool = False) -> str | None:
    """Explain why a lap cannot be manually compared under TRACE's current policy."""
    if lap.lap_number is None:
        return "Lap number is unavailable."
    if duplicate_number:
        return "This lap number occurs more than once in the provider response."
    if not _finite(lap.lap_duration_seconds):
        return "Recorded lap time is unavailable or invalid."
    missing_sectors = [
        label for label, value in (
            ("sector 1", lap.sector_1_seconds),
            ("sector 2", lap.sector_2_seconds),
            ("sector 3", lap.sector_3_seconds),
        ) if not _finite(value)
    ]
    if missing_sectors:
        return f"Missing or invalid {', '.join(missing_sectors)} time."
    if lap.quality.is_pit_out_lap is None:
        return "Pit-out status is unknown; TRACE requires an explicit non-pit-out indicator."
    if lap.quality.is_pit_out_lap:
        return "Pit-out laps are excluded by TRACE's candidate policy."
    if not lap.raw_date_start:
        return "Lap start timestamp is unavailable for telemetry retrieval."
    try:
        start = datetime.fromisoformat(lap.raw_date_start.replace("Z", "+00:00"))
    except ValueError:
        return "Lap start timestamp cannot be parsed for telemetry retrieval."
    if start.tzinfo is None:
        return "Lap start timestamp has no timezone for telemetry retrieval."
    return None


def _finite(value: float | None) -> bool:
    return value is not None and isfinite(value)


def make_lap_options(laps: Sequence[LapRecord]) -> list[LapOption]:
    counts = Counter(lap.lap_number for lap in laps if lap.lap_number is not None)
    options = [
        LapOption(
            lap_number=lap.lap_number,
            lap_time_seconds=lap.lap_duration_seconds,
            sector_1_seconds=lap.sector_1_seconds,
            sector_2_seconds=lap.sector_2_seconds,
            sector_3_seconds=lap.sector_3_seconds,
            is_pit_out_lap=lap.quality.is_pit_out_lap,
            selectable=(reason := lap_option_reason(
                lap,
                duplicate_number=lap.lap_number is not None and counts[lap.lap_number] > 1,
            )) is None,
            unavailable_reason=reason,
        )
        for lap in laps
    ]
    return sorted(options, key=lambda option: option.lap_number if option.lap_number is not None else float("inf"))


def choose_lap(
    laps: Sequence[LapRecord], requested_lap_number: int | None
) -> tuple[LapRecord | None, SelectionMode, int, str, str | None]:
    """Return lap, mode, error status/code/message when a choice cannot be made."""
    if requested_lap_number is None:
        selected = select_timed_non_pit_out_candidate(laps)
        if selected is None:
            return None, "automatic", 404, "candidate_lap_not_found", "No lap met TRACE's automatic candidate-selection policy."
        return selected, "automatic", 200, "", None

    matching = [lap for lap in laps if lap.lap_number == requested_lap_number]
    if not matching:
        return None, "manual", 404, "lap_not_found", f"Lap {requested_lap_number} was not returned for this driver in the selected session."
    if len(matching) > 1:
        return None, "manual", 422, "lap_ambiguous", f"Lap {requested_lap_number} occurs more than once in provider metadata."
    selected = matching[0]
    reason = lap_option_reason(selected)
    if reason:
        return None, "manual", 422, "lap_not_selectable", f"Lap {requested_lap_number} cannot be compared: {reason}"
    return selected, "manual", 200, "", None
