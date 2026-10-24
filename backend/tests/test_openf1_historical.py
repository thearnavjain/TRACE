import math

import httpx
import pytest

from app.services.openf1_historical import (
    HistoricalDataHTTPError,
    HistoricalDataRequestError,
    HistoricalDataResponseError,
    OpenF1HistoricalData,
    lap_delta_seconds,
    normalize_car_data,
    normalize_driver,
    normalize_lap,
    normalize_location,
    normalize_meeting,
    normalize_session,
    select_timed_non_pit_out_candidate,
)
from scripts.validate_openf1 import summarize_car_data


def test_normalizes_session_driver_and_raw_timestamps():
    session = normalize_session({
        "session_key": 9554,
        "meeting_key": 1240,
        "year": 2024,
        "session_name": "Qualifying",
        "country_name": "United Kingdom",
        "location": "Silverstone",
        "date_start": "2024-07-06T14:00:00+00:00",
    })
    driver = normalize_driver({
        "session_key": 9554,
        "driver_number": 1,
        "name_acronym": "VER",
        "full_name": "Max VERSTAPPEN",
    })

    assert session.session_key == 9554
    assert session.raw_date_start == "2024-07-06T14:00:00+00:00"
    assert driver.acronym == "VER"
    assert driver.team_name is None


def test_normalizes_meeting_fields_and_discovers_sessions_by_year():
    meeting = normalize_meeting({
        "meeting_key": 1240, "year": 2024, "meeting_name": "British Grand Prix",
        "country_name": "United Kingdom", "location": "Silverstone",
        "circuit_short_name": "Silverstone",
    })
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    with OpenF1HistoricalData(transport=httpx.MockTransport(handler)) as provider:
        meetings = provider.get_meetings(2024)
        sessions = provider.find_sessions(year=2024)

    assert meeting.meeting_key == 1240
    assert meeting.meeting_name == "British Grand Prix"
    assert meetings == [] and sessions == []
    assert len(seen) == 2
    assert "/meetings?year=2024" in seen[0]
    assert "/sessions?year=2024" in seen[1]
    assert "session_name=" not in seen[1]


def test_normalizes_lap_without_inventing_validity_indicators():
    lap = normalize_lap({
        "session_key": 9554,
        "driver_number": 4,
        "lap_number": 19,
        "date_start": "2024-07-06T15:00:21.699000+00:00",
        "lap_duration": 86.030,
        "duration_sector_1": 28.025,
        "duration_sector_2": 34.567,
        "duration_sector_3": 23.438,
        "is_pit_out_lap": False,
    })

    assert lap.lap_duration_seconds == pytest.approx(86.030)
    assert lap.quality.is_pit_out_lap is False
    assert lap.quality.validity_status is None
    assert lap.quality.personal_best is None
    assert any("validity" in warning for warning in lap.warnings)


def test_missing_and_invalid_numeric_fields_become_explicit_none_with_warnings():
    car = normalize_car_data({
        "date": "2024-07-06T15:00:21Z",
        "speed": "NaN",
        "throttle": "not-a-number",
        "n_gear": 3.5,
    })
    location = normalize_location({"x": math.inf, "y": 12.0})
    incomplete_lap = normalize_lap({"lap_duration": -math.inf})

    assert car.speed_kph is None
    assert car.throttle_percent is None
    assert car.gear is None
    assert car.driver_number is None
    assert len(car.warnings) >= 3
    assert location.x is None
    assert location.coordinate_unit is None
    assert location.z is None
    assert incomplete_lap.lap_number is None
    assert incomplete_lap.lap_duration_seconds is None


def test_candidate_policy_requires_explicit_non_pit_out_and_complete_sectors():
    rows = [
        normalize_lap({"lap_number": 1, "lap_duration": 80, "is_pit_out_lap": False}),
        normalize_lap({
            "lap_number": 2,
            "lap_duration": 81,
            "duration_sector_1": 27,
            "duration_sector_2": 27,
            "duration_sector_3": 27,
            "is_pit_out_lap": True,
        }),
        normalize_lap({
            "lap_number": 3,
            "lap_duration": 82,
            "duration_sector_1": 27,
            "duration_sector_2": 27,
            "duration_sector_3": 28,
            "is_pit_out_lap": False,
        }),
    ]

    selected = select_timed_non_pit_out_candidate(rows)

    assert selected is rows[2]
    assert selected.quality.validity_status is None


def test_delta_is_b_minus_a_and_supports_missing_laps():
    ver = normalize_lap({"lap_duration": 86.203})
    nor = normalize_lap({"lap_duration": 86.030})

    assert lap_delta_seconds(ver, nor) == pytest.approx(-0.173)
    assert lap_delta_seconds(nor, ver) == pytest.approx(0.173)
    assert lap_delta_seconds(ver, ver) == 0
    assert lap_delta_seconds(ver, None) is None


def test_http_status_errors_without_retry_hint_are_explicit_and_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"message": "rate limited"})

    with OpenF1HistoricalData(transport=httpx.MockTransport(handler)) as provider:
        with pytest.raises(HistoricalDataHTTPError) as error:
            provider.find_sessions(year=2024, country_name="UK", session_name="Qualifying")

    assert error.value.status_code == 429
    assert error.value.retry_after is None
    assert error.value.endpoint == "/sessions"
    assert calls == 1


def test_validation_summary_counts_actual_fields_timestamps_and_missing_speeds():
    samples = [
        normalize_car_data({
            "date": "2024-07-06T15:08:25.884000+00:00",
            "speed": 265,
            "throttle": 100,
        }),
        normalize_car_data({
            "date": "bad-time",
            "speed": None,
            "throttle": 99,
        }),
    ]

    report = summarize_car_data(samples)

    assert report["sample_count"] == 2
    assert report["response_fields"] == ["date", "speed", "throttle"]
    assert report["timestamp_parseable_count"] == 1
    assert report["timestamp_parse_failures_or_missing"] == 1
    assert report["speed_unit"] == "km/h (OpenF1 documented)"
    assert report["speed_missing_or_invalid_count"] == 1
    assert report["missing_values_by_response_field"]["speed"] == 1


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def wall_time(self) -> float:
        return 1_800_000_000.0

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _provider_with_clock(handler, clock, **kwargs):
    return OpenF1HistoricalData(
        transport=httpx.MockTransport(handler),
        _monotonic=clock.monotonic,
        _sleep=clock.sleep,
        _wall_time=clock.wall_time,
        **kwargs,
    )


def test_requests_are_paced_below_documented_free_tier_minute_limit():
    clock = FakeClock()
    start_times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start_times.append(clock.monotonic())
        return httpx.Response(200, json=[])

    with _provider_with_clock(handler, clock) as provider:
        provider.get_drivers(9554)
        provider.get_drivers(9554)
        provider.get_drivers(9554)

    assert len(start_times) == 3
    assert start_times[1] - start_times[0] >= 2.1
    assert start_times[2] - start_times[1] >= 2.1


def test_429_waits_for_retry_after_then_retries_once():
    clock = FakeClock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json=[])
        return httpx.Response(200, json=[])

    with _provider_with_clock(handler, clock) as provider:
        records = provider.get_drivers(9554)

    assert records == []
    assert calls == 2
    assert clock.sleeps[0] == 1.0
    assert sum(clock.sleeps) >= 2.1


def test_repeated_429_stops_after_one_bounded_retry():
    clock = FakeClock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "1"}, json=[])

    with _provider_with_clock(handler, clock) as provider:
        with pytest.raises(HistoricalDataHTTPError) as error:
            provider.get_drivers(9554)

    assert calls == 2
    assert error.value.status_code == 429
    assert error.value.retry_after == "1"
    assert error.value.attempts == 2


def test_long_retry_after_is_reported_without_exceeding_retry_bound():
    clock = FakeClock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "120"}, json=[])

    with _provider_with_clock(handler, clock) as provider:
        with pytest.raises(HistoricalDataHTTPError) as error:
            provider.get_drivers(9554)

    assert calls == 1
    assert clock.sleeps == []
    assert error.value.retry_after == "120"
    assert error.value.attempts == 1


def test_time_filters_use_operator_suffix_query_syntax():
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=[])

    with OpenF1HistoricalData(transport=httpx.MockTransport(handler)) as provider:
        samples = provider.get_car_data(
            9554,
            1,
            date_start="2024-07-06T15:08:25+00:00",
            date_end="2024-07-06T15:09:51+00:00",
        )

    assert samples == []
    assert len(seen_urls) == 1
    assert "date%3E2024" in seen_urls[0]
    assert "date%3C2024" in seen_urls[0]
    assert "date%3E=" not in seen_urls[0]
    assert "date%3C=" not in seen_urls[0]


def test_request_and_response_failures_are_distinguished():
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with OpenF1HistoricalData(transport=httpx.MockTransport(timeout_handler)) as provider:
        with pytest.raises(HistoricalDataRequestError) as request_error:
            provider.get_drivers(9554)
    assert request_error.value.error_type == "ReadTimeout"

    def bad_json_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with OpenF1HistoricalData(transport=httpx.MockTransport(bad_json_handler)) as provider:
        with pytest.raises(HistoricalDataResponseError, match="invalid JSON"):
            provider.get_drivers(9554)
