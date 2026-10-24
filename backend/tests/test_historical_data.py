import math

import pandas as pd

from app.services.historical_data import (
    compare_distance_sampling,
    lap_time_delta,
    summarize_telemetry,
)


def test_telemetry_summary_reports_missing_duplicates_and_nonmonotonic_distance() -> None:
    telemetry = pd.DataFrame({
        "Speed": [100.0, math.nan, math.inf, 110.0],
        "Distance": [0.0, 10.0, 10.0, 8.0],
        "Source": ["car", "car", "car", "car"],
    })

    summary = summarize_telemetry(telemetry)

    assert summary["sample_count"] == 4
    assert summary["columns"] == ["Speed", "Distance", "Source"]
    assert summary["missing_values_by_column"]["Speed"] == 1
    assert summary["speed"]["non_finite_or_missing_count"] == 2
    assert summary["distance_quality"]["duplicate_sample_count"] == 1
    assert summary["distance_quality"]["non_increasing_step_count"] == 2
    assert summary["distance_quality"]["negative_step_count"] == 1


def test_distance_coverage_compares_ranges_not_row_indices() -> None:
    telemetry_a = summarize_telemetry(pd.DataFrame({
        "Speed": [100, 110, 120], "Distance": [0, 10, 20]
    }))
    telemetry_b = summarize_telemetry(pd.DataFrame({
        "Speed": [105, 115, 125], "Distance": [5, 15, 25]
    }))

    comparison = compare_distance_sampling(telemetry_a, telemetry_b)

    assert comparison["comparable"] is True
    assert comparison["overlap_metres"] == 15
    assert comparison["sample_count_a"] == comparison["sample_count_b"] == 3
    assert "row-index alignment" in comparison["alignment_method"]


def test_lap_delta_sign_means_positive_is_driver_a_faster() -> None:
    delta = lap_time_delta({"LapTimeSeconds": 90.0}, {"LapTimeSeconds": 91.25})

    assert delta["delta_seconds"] == 1.25
    assert delta["faster_driver"] == "A"
    assert delta["convention"] == "driver_b_seconds - driver_a_seconds"


def test_lap_delta_rejects_non_finite_values() -> None:
    result = lap_time_delta({"LapTimeSeconds": math.nan}, {"LapTimeSeconds": 90.0})

    assert result["available"] is False
