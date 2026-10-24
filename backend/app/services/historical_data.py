"""Small FastF1 adapter for loading a session and inspecting driver laps."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import fastf1
import numpy as np
import pandas as pd
from fastf1 import Cache
from fastf1.core import Lap, Session


def repository_root() -> Path:
    """Find the checkout containing this module instead of relying on cwd."""
    module_path = Path(__file__).resolve()
    for candidate in module_path.parents:
        if (candidate / ".git").exists() and (candidate / "README.md").is_file():
            return candidate
    raise RuntimeError(f"Could not locate TRACE repository above {module_path}")


def configure_fastf1_cache(root: Path | None = None) -> Path:
    """Set FastF1's cache to the repository's ignored data/cache directory."""
    resolved_root = (root or repository_root()).resolve()
    cache_dir = (resolved_root / "data" / "cache").resolve()
    expected_parent = (resolved_root / "data").resolve()

    # Check the exact target before creating it or allowing FastF1 to use it.
    if cache_dir.parent != expected_parent or not cache_dir.is_relative_to(resolved_root):
        raise RuntimeError(f"Unsafe FastF1 cache path: {cache_dir}")
    if cache_dir != expected_parent / "cache":
        raise RuntimeError(f"Unexpected FastF1 cache path: {cache_dir}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    Cache.enable_cache(str(cache_dir))
    return cache_dir


def load_session(year: int, event: str | int, session_type: str) -> Session:
    """Load a historical session with telemetry but without unused feeds."""
    configure_fastf1_cache()
    session = fastf1.get_session(year, event, session_type)
    session.load(laps=True, telemetry=True, weather=False, messages=False)
    return session


def select_fastest_accurate_lap(session: Session, driver_code: str) -> dict[str, Any]:
    """Return the driver's counted fastest accurate lap and metadata.

    FastF1's ``pick_accurate`` requires ``IsAccurate``; ``pick_fastest``
    defaults to personal-best laps, excluding lap times not counted by timing.
    """
    all_laps = session.laps
    driver_laps = all_laps.pick_drivers(driver_code)
    result: dict[str, Any] = {
        "driver_code": driver_code,
        "exists_in_session": not driver_laps.empty,
        "suitable_laps_available": False,
        "session_lap_count": int(len(driver_laps)),
        "accurate_lap_count": 0,
        "selected_lap": None,
        "telemetry": None,
    }
    if driver_laps.empty:
        return result

    accurate_laps = driver_laps.pick_accurate()
    result["accurate_lap_count"] = int(len(accurate_laps))
    if accurate_laps.empty:
        return result

    lap = accurate_laps.pick_fastest()
    if lap is None:
        result["selection_note"] = (
            "FastF1 found accurate timing rows but no personal-best lap; "
            "no fallback to an uncounted lap was made."
        )
        return result

    result["selected_lap"] = lap_metadata(lap)
    result["suitable_laps_available"] = True
    try:
        # Car data preserves the original car-data samples. get_telemetry()
        # merges position/car channels and can interpolate; we avoid that.
        car_data = lap.get_car_data().add_distance()
        result["telemetry"] = summarize_telemetry(car_data)
        result["telemetry"]["extraction_method"] = "lap.get_car_data().add_distance()"
        result["telemetry"]["interpolation_note"] = (
            "Car-data samples are retained without resampling or position-data merging. "
            "Distance is computed by FastF1 from this lap's speed/time samples."
        )
    except Exception as exc:  # preserve lap result and report telemetry extraction failure
        result["telemetry"] = {
            "extracted": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return result


def lap_metadata(lap: Lap) -> dict[str, Any]:
    """Return selected, useful lap fields with all times normalized to seconds."""
    fields = (
        "Driver",
        "DriverNumber",
        "LapNumber",
        "LapTime",
        "Sector1Time",
        "Sector2Time",
        "Sector3Time",
        "Compound",
        "TyreLife",
        "FreshTyre",
        "Stint",
        "TrackStatus",
        "IsAccurate",
        "IsPersonalBest",
    )
    metadata: dict[str, Any] = {}
    for field in fields:
        if field not in lap.index:
            continue
        value = lap[field]
        if field.endswith("Time"):
            metadata[f"{field}Seconds"] = _seconds_or_none(value)
        else:
            metadata[field] = _json_value(value)
    return metadata


def summarize_telemetry(frame: pd.DataFrame) -> dict[str, Any]:
    """Summarize original samples and computed distance without filling gaps."""
    summary: dict[str, Any] = {
        "extracted": True,
        "columns": [str(column) for column in frame.columns],
        "sample_count": int(len(frame)),
        "missing_values_by_column": {
            str(column): int(frame[column].isna().sum()) for column in frame.columns
        },
    }
    if "Source" in frame:
        summary["source_sample_counts"] = {
            str(source): int(count)
            for source, count in frame["Source"].value_counts(dropna=False).items()
        }

    for channel in ("Speed", "Distance"):
        if channel in frame:
            summary[channel.lower()] = _numeric_summary(frame[channel])

    if "Distance" in frame:
        distance = pd.to_numeric(frame["Distance"], errors="coerce")
        finite_distance = distance[np.isfinite(distance)]
        steps = finite_distance.diff().dropna()
        summary["distance_quality"] = {
            "duplicate_sample_count": int(finite_distance.duplicated().sum()),
            "non_increasing_step_count": int((steps <= 0).sum()),
            "negative_step_count": int((steps < 0).sum()),
            "step_metres": _numeric_summary(steps),
        }
    else:
        summary["distance_quality"] = {"issue": "Distance column unavailable"}

    for channel in ("Speed", "Distance"):
        if channel not in frame:
            summary.setdefault("issues", []).append(f"{channel} column unavailable")
    return summary


def compare_distance_sampling(
    telemetry_a: dict[str, Any] | None, telemetry_b: dict[str, Any] | None
) -> dict[str, Any]:
    """Compare distance ranges and sample spacing, never aligning by row index."""
    if not telemetry_a or not telemetry_b:
        return {"comparable": False, "reason": "Telemetry missing for one or both drivers"}
    distance_a = telemetry_a.get("distance", {})
    distance_b = telemetry_b.get("distance", {})
    bounds_a = _bounds(distance_a)
    bounds_b = _bounds(distance_b)
    if bounds_a is None or bounds_b is None:
        return {"comparable": False, "reason": "Finite distance coverage unavailable"}

    overlap_start = max(bounds_a[0], bounds_b[0])
    overlap_end = min(bounds_a[1], bounds_b[1])
    overlap = max(0.0, overlap_end - overlap_start)
    span_a = bounds_a[1] - bounds_a[0]
    span_b = bounds_b[1] - bounds_b[0]
    step_a = telemetry_a.get("distance_quality", {}).get("step_metres", {}).get("median")
    step_b = telemetry_b.get("distance_quality", {}).get("step_metres", {}).get("median")

    return {
        "comparable": overlap > 0,
        "alignment_method": "Compare distance ranges and sampling statistics; no row-index alignment or interpolation",
        "driver_a_distance_range_metres": bounds_a,
        "driver_b_distance_range_metres": bounds_b,
        "overlap_metres": overlap,
        "overlap_fraction_of_a": overlap / span_a if span_a > 0 else None,
        "overlap_fraction_of_b": overlap / span_b if span_b > 0 else None,
        "sample_count_a": telemetry_a.get("sample_count"),
        "sample_count_b": telemetry_b.get("sample_count"),
        "median_distance_step_metres_a": step_a,
        "median_distance_step_metres_b": step_b,
        "sampling_note": "Equal sample indices are not assumed to represent equal track positions.",
    }


def lap_time_delta(lap_a: dict[str, Any] | None, lap_b: dict[str, Any] | None) -> dict[str, Any]:
    """Return B minus A seconds; positive means A's selected lap was faster."""
    if not lap_a or not lap_b:
        return {"available": False, "reason": "A selected lap is unavailable"}
    time_a = lap_a.get("LapTimeSeconds")
    time_b = lap_b.get("LapTimeSeconds")
    if time_a is None or time_b is None or not math.isfinite(time_a) or not math.isfinite(time_b):
        return {"available": False, "reason": "A selected lap time is missing or non-finite"}
    delta = time_b - time_a
    return {
        "available": True,
        "convention": "driver_b_seconds - driver_a_seconds",
        "delta_seconds": delta,
        "faster_driver": "A" if delta > 0 else "B" if delta < 0 else "equal",
    }


def _numeric_summary(values: pd.Series) -> dict[str, Any]:
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric[np.isfinite(numeric)]
    return {
        "finite_count": int(len(finite)),
        "non_finite_or_missing_count": int(len(values) - len(finite)),
        "min": float(finite.min()) if not finite.empty else None,
        "max": float(finite.max()) if not finite.empty else None,
        "median": float(finite.median()) if not finite.empty else None,
        "mean": float(finite.mean()) if not finite.empty else None,
    }


def _bounds(summary: dict[str, Any]) -> tuple[float, float] | None:
    low, high = summary.get("min"), summary.get("max")
    if low is None or high is None:
        return None
    return float(low), float(high)


def _seconds_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    seconds = float(value.total_seconds()) if hasattr(value, "total_seconds") else float(value)
    return seconds if math.isfinite(seconds) else None


def _json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (str, int, float, bool)):
        return value if not isinstance(value, float) or math.isfinite(value) else None
    return str(value)
