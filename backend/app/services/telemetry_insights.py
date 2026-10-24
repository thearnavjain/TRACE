"""Deterministic summaries for the provider's observed timestamped samples."""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.schemas.comparison import SpeedDifferenceInsight, SpeedSummary, TelemetryPoint


# OpenF1 car telemetry is sampled at about 3.7 Hz. A 150 ms maximum separation
# is below a typical sample interval and rejects visibly unsynchronized pairs.
SPEED_COMPARISON_TOLERANCE_SECONDS = 0.15


def summarize_speed(points: Sequence[TelemetryPoint]) -> SpeedSummary:
    """Summarize finite speed values actually present in the returned samples."""
    speeds = [
        point.speed_kph
        for point in points
        if point.speed_kph is not None and math.isfinite(point.speed_kph)
    ]
    if not speeds:
        return SpeedSummary(
            minimum_kph=None, maximum_kph=None, mean_kph=None, valid_sample_count=0
        )
    return SpeedSummary(
        minimum_kph=min(speeds),
        maximum_kph=max(speeds),
        mean_kph=math.fsum(speeds) / len(speeds),
        valid_sample_count=len(speeds),
    )


def largest_nearby_speed_difference(
    points_a: Sequence[TelemetryPoint],
    points_b: Sequence[TelemetryPoint],
    *,
    driver_a: str,
    driver_b: str,
    tolerance_seconds: float = SPEED_COMPARISON_TOLERANCE_SECONDS,
) -> SpeedDifferenceInsight:
    """Find the largest observed speed gap among near-time samples.

    Each trace's elapsed coordinate is derived independently from its own first
    timestamp upstream. Only the shared elapsed-time interval is considered.
    For every speed-valid sample in either trace, this function pairs its nearest
    speed-valid sample in the other trace. Pairs farther apart than the tolerance
    are discarded; no extrapolation or interpolation is performed.
    """
    if not math.isfinite(tolerance_seconds) or tolerance_seconds < 0:
        raise ValueError("tolerance_seconds must be finite and non-negative")

    elapsed_a = [point.elapsed_seconds for point in points_a if math.isfinite(point.elapsed_seconds)]
    elapsed_b = [point.elapsed_seconds for point in points_b if math.isfinite(point.elapsed_seconds)]
    if not elapsed_a or not elapsed_b:
        return _unavailable(tolerance_seconds, "One or both traces have no timestamped samples.")

    start = max(min(elapsed_a), min(elapsed_b))
    end = min(max(elapsed_a), max(elapsed_b))
    if start > end:
        return _unavailable(tolerance_seconds, "The traces have no shared elapsed-time range.")
    shared_range = (start, end)

    usable_a = [
        (index, point)
        for index, point in enumerate(points_a)
        if start <= point.elapsed_seconds <= end
        and point.speed_kph is not None
        and math.isfinite(point.speed_kph)
    ]
    usable_b = [
        (index, point)
        for index, point in enumerate(points_b)
        if start <= point.elapsed_seconds <= end
        and point.speed_kph is not None
        and math.isfinite(point.speed_kph)
    ]
    if not usable_a or not usable_b:
        return _unavailable(
            tolerance_seconds,
            "The shared elapsed-time range has no speed-valid samples for one or both drivers.",
            shared_range,
        )

    pair_indices: set[tuple[int, int]] = set()
    candidates: list[tuple[TelemetryPoint, TelemetryPoint, float]] = []

    def consider(source, other, source_is_a: bool) -> None:
        for source_index, source_point in source:
            nearest_index, nearest_point = min(
                other,
                key=lambda item: (
                    abs(item[1].elapsed_seconds - source_point.elapsed_seconds),
                    item[1].elapsed_seconds,
                    item[0],
                ),
            )
            a_index, b_index = (
                (source_index, nearest_index)
                if source_is_a
                else (nearest_index, source_index)
            )
            separation = abs(
                points_a[a_index].elapsed_seconds - points_b[b_index].elapsed_seconds
            )
            if separation > tolerance_seconds or (a_index, b_index) in pair_indices:
                continue
            pair_indices.add((a_index, b_index))
            candidates.append((points_a[a_index], points_b[b_index], separation))

    consider(usable_a, usable_b, True)
    consider(usable_b, usable_a, False)
    if not candidates:
        return _unavailable(
            tolerance_seconds,
            f"No speed-valid sample pairs are within {tolerance_seconds:.3f} seconds in the shared range.",
            shared_range,
        )

    point_a, point_b, separation = max(
        candidates,
        key=lambda pair: (
            abs(pair[1].speed_kph - pair[0].speed_kph),
            -min(pair[0].elapsed_seconds, pair[1].elapsed_seconds),
            -pair[2],
        ),
    )
    signed_delta = point_b.speed_kph - point_a.speed_kph
    if point_a.elapsed_seconds < point_b.elapsed_seconds:
        earlier, later = driver_a, driver_b
    elif point_b.elapsed_seconds < point_a.elapsed_seconds:
        earlier, later = driver_b, driver_a
    else:
        earlier = later = None
    return SpeedDifferenceInsight(
        available=True,
        comparison_tolerance_seconds=tolerance_seconds,
        shared_elapsed_range_seconds=shared_range,
        driver_a_speed_kph=point_a.speed_kph,
        driver_b_speed_kph=point_b.speed_kph,
        driver_b_minus_driver_a_kph=signed_delta,
        absolute_difference_kph=abs(signed_delta),
        driver_a_elapsed_seconds=point_a.elapsed_seconds,
        driver_b_elapsed_seconds=point_b.elapsed_seconds,
        time_separation_seconds=separation,
        earlier_in_elapsed_time_driver=earlier,
        later_in_elapsed_time_driver=later,
        driver_a_timestamp=point_a.timestamp,
        driver_b_timestamp=point_b.timestamp,
    )


def _unavailable(
    tolerance_seconds: float,
    reason: str,
    shared_range: tuple[float, float] | None = None,
) -> SpeedDifferenceInsight:
    return SpeedDifferenceInsight(
        available=False,
        unavailable_reason=reason,
        comparison_tolerance_seconds=tolerance_seconds,
        shared_elapsed_range_seconds=shared_range,
    )
