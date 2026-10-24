from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.comparison import TelemetryPoint
from app.services.telemetry_insights import (
    SPEED_COMPARISON_TOLERANCE_SECONDS,
    largest_nearby_speed_difference,
    summarize_speed,
)


ORIGIN = datetime(2024, 7, 6, tzinfo=timezone.utc)


def point(elapsed: float, speed: float | None, *, offset: float = 0) -> TelemetryPoint:
    return TelemetryPoint(
        timestamp=ORIGIN + timedelta(seconds=offset + elapsed),
        elapsed_seconds=elapsed,
        speed_kph=speed,
        throttle_percent=None,
        brake_percent=None,
        gear=None,
        drs_status=None,
        rpm=None,
    )


def compare(a, b, tolerance=SPEED_COMPARISON_TOLERANCE_SECONDS):
    return largest_nearby_speed_difference(
        a, b, driver_a="VER", driver_b="NOR", tolerance_seconds=tolerance
    )


def test_unequal_sample_rates_use_nearest_points_and_report_elapsed_offset_order():
    result = compare(
        [point(0, 100), point(0.3, 110), point(0.6, 140)],
        [point(0, 101), point(0.25, 115), point(0.5, 120), point(0.75, 121)],
    )

    assert result.available is True
    assert result.absolute_difference_kph == pytest.approx(20)
    assert result.driver_a_speed_kph == 140
    assert result.driver_b_speed_kph == 120
    assert result.driver_a_elapsed_seconds == pytest.approx(0.6)
    assert result.driver_b_elapsed_seconds == pytest.approx(0.5)
    assert result.time_separation_seconds == pytest.approx(0.1)
    assert result.earlier_in_elapsed_time_driver == "NOR"
    assert result.later_in_elapsed_time_driver == "VER"
    assert result.shared_elapsed_range_seconds == (0, 0.6)
    assert result.comparison_tolerance_seconds == pytest.approx(0.15)


def test_mismatched_samples_report_timestamps_and_separation_without_claiming_exact_match():
    result = compare(
        [point(0.8, 100), point(1.0, 250), point(1.2, 100)],
        [point(0.8, 100, offset=-500), point(1.05, 230, offset=-500), point(1.2, 100, offset=-500)],
    )

    assert result.available is True
    assert result.time_separation_seconds == pytest.approx(0.05)
    # Raw wall-clock order differs because these are separate laps; the
    # earlier/later labels deliberately refer to the elapsed-time coordinate.
    assert result.driver_b_timestamp < result.driver_a_timestamp
    assert result.earlier_in_elapsed_time_driver == "VER"
    assert result.later_in_elapsed_time_driver == "NOR"


def test_pairs_outside_tolerance_are_not_compared_across_large_gaps():
    result = compare([point(0, 100), point(0.2, 110), point(0.5, 100)], [point(0.25, 95), point(0.75, 95)])

    assert result.available is False
    assert "within 0.150 seconds" in result.unavailable_reason
    assert result.absolute_difference_kph is None


def test_missing_speed_is_skipped_in_favor_of_nearest_speed_valid_sample():
    result = compare(
        [point(0, 100), point(0.1, None), point(0.2, 120)],
        [point(0, 99), point(0.05, 95), point(0.2, 118)],
    )

    assert result.available is True
    assert result.absolute_difference_kph == pytest.approx(5)
    assert result.driver_a_elapsed_seconds == 0
    assert result.driver_b_elapsed_seconds == pytest.approx(0.05)


def test_no_shared_elapsed_range_returns_an_unavailable_insight():
    result = compare([point(0, 100), point(0.1, 110)], [point(0.5, 90), point(0.6, 95)])

    assert result.available is False
    assert result.shared_elapsed_range_seconds is None
    assert "no shared elapsed-time range" in result.unavailable_reason


def test_shared_range_without_speed_valid_samples_returns_unavailable():
    result = compare(
        [point(0, 100), point(0.2, 110)],
        [point(0, None), point(0.2, None)],
    )

    assert result.available is False
    assert result.shared_elapsed_range_seconds == (0, 0.2)
    assert result.driver_a_speed_kph is None


def test_equal_speed_samples_report_a_zero_gap_without_inventing_a_winner():
    result = compare([point(0.4, 198)], [point(0.4, 198)])

    assert result.available is True
    assert result.absolute_difference_kph == 0
    assert result.driver_b_minus_driver_a_kph == 0
    assert result.earlier_in_elapsed_time_driver is None
    assert result.later_in_elapsed_time_driver is None


def test_speed_summary_handles_missing_and_empty_values():
    summary = summarize_speed([point(0, 120), point(0.1, None), point(0.2, 90)])
    empty = summarize_speed([point(0, None)])

    assert summary.valid_sample_count == 2
    assert summary.minimum_kph == 90
    assert summary.maximum_kph == 120
    assert summary.mean_kph == 105
    assert empty.valid_sample_count == 0
    assert empty.minimum_kph is None
    assert empty.maximum_kph is None
    assert empty.mean_kph is None


def test_nonfinite_speed_values_are_excluded_from_summaries_and_gap_pairs():
    summary = summarize_speed([point(0, float("nan")), point(0.1, 120)])
    result = compare(
        [point(0, float("nan")), point(0.1, 120)],
        [point(0, 100), point(0.1, 119)],
    )

    assert summary.valid_sample_count == 1
    assert summary.mean_kph == 120
    assert result.available is True
    assert result.absolute_difference_kph == 20
    assert result.driver_a_elapsed_seconds == pytest.approx(0.1)


def test_invalid_tolerance_is_rejected():
    with pytest.raises(ValueError, match="finite and non-negative"):
        compare([point(0, 1)], [point(0, 2)], tolerance=float("inf"))
