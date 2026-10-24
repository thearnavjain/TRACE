"""Opt-in live probe of OpenF1's 2024 British GP qualifying archive.

Run from ``backend`` with ``.venv/Scripts/python -m scripts.validate_openf1``.
This command performs network requests and is intentionally not run by pytest.
"""

from __future__ import annotations

import json
import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
from time import sleep
from typing import Any

from app.models.historical import LapRecord
from app.services.openf1_historical import (
    HistoricalDataHTTPError,
    OpenF1HistoricalData,
    lap_delta_seconds,
    select_timed_non_pit_out_candidate,
)


# These identifiers and times come from the prior real /drivers and /laps
# responses documented in docs/data-validation.md. This mode reuses those
# records and requests only the two known telemetry windows.
KNOWN_SESSION_KEY = 9554
KNOWN_LAP_WINDOWS = {
    "VER": {
        "driver_number": 1,
        "lap_number": 23,
        "date_start": "2024-07-06T15:08:25.755000+00:00",
        "lap_duration_seconds": 86.203,
    },
    "NOR": {
        "driver_number": 4,
        "lap_number": 19,
        "date_start": "2024-07-06T15:00:21.699000+00:00",
        "lap_duration_seconds": 86.030,
    },
}


def _to_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _to_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json(item) for item in value]
    return value


def _time_window(lap: LapRecord) -> tuple[str, str] | None:
    if lap.raw_date_start is None or lap.lap_duration_seconds is None:
        return None
    try:
        start = datetime.fromisoformat(lap.raw_date_start.replace("Z", "+00:00"))
    except ValueError:
        return None
    end = start + timedelta(seconds=lap.lap_duration_seconds)
    return start.isoformat(timespec="milliseconds"), end.isoformat(timespec="milliseconds")


def summarize_car_data(samples: list[Any]) -> dict[str, Any]:
    fields = sorted({field for sample in samples for field in sample.raw_record})
    timestamps: list[datetime] = []
    parse_failures = 0
    naive_timestamps = 0
    for sample in samples:
        if sample.raw_date is None:
            parse_failures += 1
            continue
        try:
            timestamp = datetime.fromisoformat(sample.raw_date.replace("Z", "+00:00"))
        except ValueError:
            parse_failures += 1
            continue
        if timestamp.tzinfo is None:
            naive_timestamps += 1
        timestamps.append(timestamp)
    speeds = [sample.speed_kph for sample in samples if sample.speed_kph is not None]
    return {
        "sample_count": len(samples),
        "response_fields": fields,
        "timestamp_parseable_count": len(timestamps),
        "timestamp_parse_failures_or_missing": parse_failures,
        "timestamp_without_timezone_count": naive_timestamps,
        "speed_unit": "km/h (OpenF1 documented)",
        "speed_nonmissing_count": len(speeds),
        "speed_missing_or_invalid_count": len(samples) - len(speeds),
        "speed_min_kmh": min(speeds, default=None),
        "speed_max_kmh": max(speeds, default=None),
        "missing_values_by_response_field": {
            field: sum(sample.raw_record.get(field) is None for sample in samples)
            for field in fields
        },
    }


def _validate_known_telemetry() -> int:
    completed: dict[str, Any] = {}
    current_driver: str | None = None
    # Respect the Retry-After: 1 received by the previous controlled run before
    # starting another validation attempt in this process.
    sleep(1.1)
    try:
        with OpenF1HistoricalData() as provider:
            for current_driver, known in KNOWN_LAP_WINDOWS.items():
                start = datetime.fromisoformat(known["date_start"])
                end = start + timedelta(seconds=known["lap_duration_seconds"])
                samples = provider.get_car_data(
                    KNOWN_SESSION_KEY,
                    known["driver_number"],
                    date_start=start,
                    date_end=end,
                )
                completed[current_driver] = {
                    "driver_number": known["driver_number"],
                    "lap_number": known["lap_number"],
                    "lap_time_seconds": known["lap_duration_seconds"],
                    "candidate_validity": None,
                    "date_window": [start.isoformat(), end.isoformat()],
                    "telemetry": summarize_car_data(samples),
                }
        print(json.dumps({
            "status": "telemetry_loaded",
            "provider": "openf1",
            "session_key": KNOWN_SESSION_KEY,
            "metadata_reused_from_prior_real_responses": True,
            "drivers": completed,
        }, indent=2))
        return 0
    except Exception as exc:
        report: dict[str, Any] = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "failed_driver": current_driver,
            "completed_driver_data": completed,
        }
        if isinstance(exc, HistoricalDataHTTPError):
            report.update({
                "endpoint": exc.endpoint,
                "http_status": exc.status_code,
                "retry_after": exc.retry_after,
                "attempts": exc.attempts,
            })
        print(json.dumps(report, indent=2))
        return 2


def _validate_full_session() -> int:
    try:
        with OpenF1HistoricalData() as provider:
            sessions = provider.find_sessions(
                year=2024, country_name="United Kingdom", session_name="Qualifying"
            )
            matches = [
                session
                for session in sessions
                if session.year == 2024
                and session.country_name == "United Kingdom"
                and session.session_name == "Qualifying"
                and session.location == "Silverstone"
            ]
            if len(matches) != 1 or matches[0].session_key is None:
                raise RuntimeError(f"Expected one identified session, found {len(matches)}")
            session = matches[0]
            drivers = provider.get_drivers(session.session_key)
            laps = provider.get_laps(session.session_key)

            driver_results: dict[str, Any] = {}
            selected: dict[str, LapRecord | None] = {}
            for acronym in ("VER", "NOR"):
                driver = next((item for item in drivers if item.acronym == acronym), None)
                driver_laps = [
                    lap for lap in laps
                    if driver is not None and lap.driver_number == driver.driver_number
                ]
                candidate = select_timed_non_pit_out_candidate(driver_laps)
                selected[acronym] = candidate
                telemetry: dict[str, Any] | None = None
                if (
                    driver is not None
                    and driver.driver_number is not None
                    and candidate is not None
                ):
                    window = _time_window(candidate)
                    if window is not None:
                        car = provider.get_car_data(
                            session.session_key,
                            driver.driver_number,
                            date_start=window[0],
                            date_end=window[1],
                        )
                        location = provider.get_location(
                            session.session_key,
                            driver.driver_number,
                            date_start=window[0],
                            date_end=window[1],
                        )
                        telemetry = {
                            "window_utc": window,
                            "car_data_count": len(car),
                            "location_count": len(location),
                            "car_data_fields": sorted({
                                field for sample in car for field in sample.raw_record
                            }),
                            "speed_kph_range": [
                                min((s.speed_kph for s in car if s.speed_kph is not None), default=None),
                                max((s.speed_kph for s in car if s.speed_kph is not None), default=None),
                            ],
                            "location_xy_count": sum(
                                sample.x is not None and sample.y is not None
                                for sample in location
                            ),
                            "location_unit": None,
                        }
                driver_results[acronym] = {
                    "exists_in_session": driver is not None,
                    "driver": asdict(driver) if driver is not None else None,
                    "session_lap_count": len(driver_laps),
                    "timed_non_pit_out_candidate": (
                        asdict(candidate) if candidate is not None else None
                    ),
                    "candidate_is_officially_valid": None,
                    "telemetry": telemetry,
                }

            delta = lap_delta_seconds(selected["VER"], selected["NOR"])
            report = {
                "status": "loaded",
                "provider": "openf1",
                "session": asdict(session),
                "session_driver_count": len(drivers),
                "session_lap_count": len(laps),
                "drivers": driver_results,
                "lap_delta": {
                    "convention": "NOR seconds - VER seconds",
                    "delta_seconds": delta,
                    "faster_candidate": (
                        "NOR" if delta is not None and delta < 0
                        else "VER" if delta is not None and delta > 0
                        else "equal" if delta == 0
                        else None
                    ),
                    "official_validity_verified": False,
                },
            }
            print(json.dumps(_to_json(report), indent=2, allow_nan=False))
        return 0
    except Exception as exc:
        report = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        if isinstance(exc, HistoricalDataHTTPError):
            report.update({
                "endpoint": exc.endpoint,
                "http_status": exc.status_code,
                "retry_after": exc.retry_after,
            })
        print(json.dumps(report, indent=2))
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--telemetry-only",
        action="store_true",
        help="Reuse known session/lap metadata and request only the candidate car-data windows.",
    )
    args = parser.parse_args()
    if args.telemetry_only:
        return _validate_known_telemetry()
    return _validate_full_session()


if __name__ == "__main__":
    raise SystemExit(main())
