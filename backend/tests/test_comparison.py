from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.compare import get_provider
from app.main import app
from app.models.historical import (
    CarDataSample, DataProvenance, DriverMetadata, LapQuality, LapRecord, MeetingMetadata, SessionMetadata,
)
from app.services.comparison import ComparisonError, create_comparison
from app.services.openf1_historical import HistoricalDataHTTPError


def session():
    return SessionMetadata(
        session_key=9554, meeting_key=1240, year=2024, session_name="Qualifying",
        session_type="Qualifying", country_name="United Kingdom", location="Silverstone",
        circuit_name="Silverstone", raw_date_start="2024-07-06T14:00:00+00:00",
        raw_date_end=None, provenance=DataProvenance("openf1", "/sessions", 9554),
    )


def driver(number, acronym):
    return DriverMetadata(
        driver_number=number, acronym=acronym, full_name=f"{acronym} Driver",
        team_name="Team", session_key=9554, meeting_key=1240,
        provenance=DataProvenance("openf1", "/drivers", number),
    )


def lap(number, acronym, duration, start, lap_number=2, pit_out=False):
    return LapRecord(
        driver_number=number, session_key=9554, meeting_key=1240, lap_number=lap_number,
        raw_date_start=start, lap_duration_seconds=duration, sector_1_seconds=27.0,
        sector_2_seconds=35.0, sector_3_seconds=24.0,
        quality=LapQuality(is_pit_out_lap=pit_out),
        source_identifiers={"lap_number": lap_number},
        provenance=DataProvenance("openf1", "/laps", lap_number),
    )


def car(number, date, speed):
    return CarDataSample(
        raw_date=date, session_key=9554, meeting_key=1240, driver_number=number,
        speed_kph=speed, throttle_percent=95.0, brake_percent=0.0, gear=6,
        drs=10, rpm=10000, provenance=DataProvenance("openf1", "/car_data", number),
    )


class FakeProvider:
    def __init__(self):
        self.laps_calls = 0
        self.driver_calls = 0
        self.windows = []

    def find_sessions(self, *, year, session_name=None, country_name=None, meeting_key=None, session_key=None):
        assert year == 2024
        assert country_name is None
        return [session()] if session_name is None or session_name == "Qualifying" else []

    def get_session(self, key):
        return session() if str(key) == "9554" else None

    def get_meetings(self, year):
        return [MeetingMetadata(
            meeting_key=1240, year=year, meeting_name="British Grand Prix",
            meeting_official_name=None, country_name="United Kingdom",
            location="Silverstone", circuit_name="Silverstone", raw_date_start=None,
            provenance=DataProvenance("openf1", "/meetings", 1240),
        )]

    def get_drivers(self, key):
        self.driver_calls += 1
        return [driver(1, "VER"), driver(4, "NOR")]

    def get_laps(self, key, driver_number=None):
        self.laps_calls += 1
        return [
            lap(1, "VER", 86.203, "2024-07-06T15:08:25.755000+00:00"),
            lap(4, "NOR", 86.030, "2024-07-06T15:00:21.699000+00:00"),
        ]

    def get_car_data(self, key, number, *, date_start, date_end):
        self.windows.append(number)
        base = "2024-07-06T15:08:25" if number == 1 else "2024-07-06T15:00:21"
        return [
            car(number, f"{base}.900000+00:00", 250),
            car(number, f"{base}.990000+00:00", 260),
        ]

    def get_location(self, *args, **kwargs):
        raise AssertionError("comparison endpoint must not request unneeded location data")


def test_comparison_uses_metadata_candidate_policy_telemetry_and_delta_schema():
    provider = FakeProvider()
    result = create_comparison(
        provider, year=2024, event="Silverstone", session_name="Qualifying",
        driver_a="VER", driver_b="NOR",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    body = result.model_dump(mode="json")

    assert body["session"]["session_key"] == 9554
    assert body["driver_a"]["lap"]["lap_number"] == 2
    assert body["driver_a"]["lap"]["validity"] == "unknown"
    assert body["driver_a"]["telemetry"][1]["elapsed_seconds"] == pytest.approx(0.09)
    assert body["driver_a"]["telemetry"][0]["speed_kph"] == 250
    assert body["driver_a"]["speed_summary"] == {
        "minimum_kph": 250,
        "maximum_kph": 260,
        "mean_kph": 255,
        "valid_sample_count": 2,
    }
    assert body["lap_time_comparison"]["delta_seconds"] == pytest.approx(-0.173)
    assert body["lap_time_comparison"]["faster_driver"] == "NOR"
    assert body["lap_time_comparison"]["convention"] == "driver_b_minus_driver_a"
    assert body["speed_difference_insight"]["available"] is True
    assert body["speed_difference_insight"]["absolute_difference_kph"] == 0
    assert provider.driver_calls == provider.laps_calls == 1
    assert provider.windows == [1, 4]


def test_comparison_rejects_ambiguous_or_missing_session():
    provider = FakeProvider()
    provider.find_sessions = lambda **kwargs: [session(), session()]
    with pytest.raises(ComparisonError, match="multiple sessions"):
        create_comparison(provider, year=2024, event="Silverstone", session_name="Qualifying", driver_a="VER", driver_b="NOR")

    provider.find_sessions = lambda **kwargs: []
    with pytest.raises(ComparisonError) as error:
        create_comparison(provider, year=2024, event="Silverstone", session_name="Qualifying", driver_a="VER", driver_b="NOR")
    assert error.value.status_code == 404


def test_provider_rate_limit_is_mapped_without_retrying_or_hiding_retry_after():
    class Limited(FakeProvider):
        def find_sessions(self, **kwargs):
            raise HistoricalDataHTTPError("/sessions", 429, retry_after="7", attempts=1)

    with pytest.raises(ComparisonError) as error:
        create_comparison(Limited(), year=2024, event="Silverstone", session_name="Qualifying", driver_a="VER", driver_b="NOR")
    assert error.value.status_code == 429
    assert error.value.retry_after == "7"


def test_compare_endpoint_returns_stable_schema_from_mocked_provider():
    provider = FakeProvider()
    app.dependency_overrides[get_provider] = lambda: provider
    try:
        response = TestClient(app).get(
            "/api/compare",
            params={"year": 2024, "event": "Silverstone", "session": "Qualifying", "driver_a": "VER", "driver_b": "NOR"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["session"]["session_key"] == 9554
    assert payload["lap_time_comparison"]["faster_driver"] == "NOR"
    assert payload["driver_a"]["telemetry"][0]["speed_kph"] == 250
    assert payload["driver_a"]["speed_summary"]["valid_sample_count"] == 2
    assert payload["speed_difference_insight"]["available"] is True
    assert payload["provenance"]["provider"] == "openf1"


def test_compare_endpoint_returns_retry_after_for_rate_limit():
    class Limited(FakeProvider):
        def find_sessions(self, **kwargs):
            raise HistoricalDataHTTPError("/sessions", 429, retry_after="9", attempts=1)

    app.dependency_overrides[get_provider] = lambda: Limited()
    try:
        response = TestClient(app).get(
            "/api/compare", params={"year": 2024, "event": "Silverstone", "session": "Qualifying", "driver_a": "VER", "driver_b": "NOR"}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 429
    assert response.headers["retry-after"] == "9"
    assert response.json()["detail"]["code"] == "provider_rate_limited"


def test_session_key_compare_and_dynamic_discovery_endpoints():
    provider = FakeProvider()
    app.dependency_overrides[get_provider] = lambda: provider
    try:
        client = TestClient(app)
        sessions_response = client.get("/api/sessions", params={"year": 2024})
        drivers_response = client.get("/api/drivers", params={"session_key": 9554})
        comparison_response = client.get("/api/compare", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR",
        })
    finally:
        app.dependency_overrides.clear()

    assert sessions_response.status_code == 200
    meeting = sessions_response.json()["meetings"][0]
    assert meeting["meeting_key"] == 1240
    assert meeting["meeting_name"] == "British Grand Prix"
    assert meeting["sessions"][0]["session_key"] == 9554
    assert drivers_response.status_code == 200
    assert {row["identifier"] for row in drivers_response.json()["drivers"]} == {"VER", "NOR"}
    assert comparison_response.status_code == 200
    assert comparison_response.json()["session"]["session_key"] == 9554


def test_session_key_compare_rejects_unknown_session_and_nonmember_driver():
    provider = FakeProvider()
    app.dependency_overrides[get_provider] = lambda: provider
    try:
        client = TestClient(app)
        missing_session = client.get("/api/compare", params={"session_key": 123, "driver_a": "VER", "driver_b": "NOR"})
        missing_driver = client.get("/api/compare", params={"session_key": 9554, "driver_a": "VER", "driver_b": "HAM"})
    finally:
        app.dependency_overrides.clear()
    assert missing_session.status_code == 404
    assert missing_session.json()["detail"]["code"] == "session_not_found"
    assert missing_driver.status_code == 404
    assert missing_driver.json()["detail"]["code"] == "driver_not_found"


def test_driver_fallback_identifier_uses_provider_number_and_excludes_wrong_session():
    class SparseDrivers(FakeProvider):
        def get_drivers(self, key):
            return [
                DriverMetadata(
                    driver_number=63, acronym=None, full_name=None, team_name=None,
                    session_key=9554, meeting_key=1240,
                    provenance=DataProvenance("openf1", "/drivers", 63),
                ),
                DriverMetadata(
                    driver_number=44, acronym="HAM", full_name="Lewis Hamilton", team_name="Team",
                    session_key=9999, meeting_key=1240,
                    provenance=DataProvenance("openf1", "/drivers", 44),
                ),
            ]

    app.dependency_overrides[get_provider] = lambda: SparseDrivers()
    try:
        response = TestClient(app).get("/api/drivers", params={"session_key": 9554})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["drivers"] == [{
        "identifier": "63", "driver_number": 63, "acronym": None,
        "full_name": None, "team_name": None,
    }]


def test_session_discovery_keeps_sessions_and_same_circuit_meetings_distinct_by_keys():
    class MultipleMeetings(FakeProvider):
        def get_meetings(self, year):
            return [
                MeetingMetadata(1240, year, "British Grand Prix", None, "United Kingdom", "Silverstone", "Silverstone", None, DataProvenance("openf1", "/meetings", 1240)),
                MeetingMetadata(1300, year, "Silverstone Testing", None, "United Kingdom", "Silverstone", "Silverstone", None, DataProvenance("openf1", "/meetings", 1300)),
            ]

        def find_sessions(self, **kwargs):
            original = session()
            second_session = SessionMetadata(
                session_key=9555, meeting_key=1240, year=2024, session_name="Practice 3",
                session_type="Practice", country_name="United Kingdom", location="Silverstone",
                circuit_name="Silverstone", raw_date_start="2024-07-06T10:00:00+00:00",
                raw_date_end=None, provenance=DataProvenance("openf1", "/sessions", 9555),
            )
            same_circuit_new_meeting = SessionMetadata(
                session_key=9900, meeting_key=1300, year=2024, session_name="Practice 1",
                session_type="Practice", country_name="United Kingdom", location="Silverstone",
                circuit_name="Silverstone", raw_date_start="2024-02-20T10:00:00+00:00",
                raw_date_end=None, provenance=DataProvenance("openf1", "/sessions", 9900),
            )
            return [original, second_session, same_circuit_new_meeting]

    app.dependency_overrides[get_provider] = lambda: MultipleMeetings()
    try:
        response = TestClient(app).get("/api/sessions", params={"year": 2024})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    meetings = response.json()["meetings"]
    assert len(meetings) == 2
    british = next(item for item in meetings if item["meeting_key"] == 1240)
    testing = next(item for item in meetings if item["meeting_key"] == 1300)
    assert {item["session_key"] for item in british["sessions"]} == {9554, 9555}
    assert {item["session_key"] for item in testing["sessions"]} == {9900}
    assert british["location"] == testing["location"] == "Silverstone"


def test_metadata_rate_limit_preserves_retry_after_and_year_validation_is_local():
    class LimitedMetadata(FakeProvider):
        def get_meetings(self, year):
            raise HistoricalDataHTTPError("/meetings", 429, retry_after="6", attempts=1)

    app.dependency_overrides[get_provider] = lambda: LimitedMetadata()
    try:
        client = TestClient(app)
        limited = client.get("/api/sessions", params={"year": 2024})
        unsupported = client.get("/api/sessions", params={"year": 2022})
    finally:
        app.dependency_overrides.clear()
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "6"
    assert limited.json()["detail"]["code"] == "provider_rate_limited"
    assert unsupported.status_code == 422


def test_session_discovery_explains_upstream_401_without_calling_it_empty_data():
    class AccessRestricted(FakeProvider):
        def get_meetings(self, year):
            raise HistoricalDataHTTPError("/meetings", 401, attempts=1)

    app.dependency_overrides[get_provider] = lambda: AccessRestricted()
    try:
        response = TestClient(app).get("/api/sessions", params={"year": 2024})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "provider_access_restricted"
    assert "HTTP 401" in response.json()["detail"]["message"]
    assert "active live-session window" in response.json()["detail"]["message"]
    assert "credentials configured" in response.json()["detail"]["message"]


def test_comparison_maps_upstream_401_to_access_restricted_error():
    class AccessRestricted(FakeProvider):
        def get_session(self, key):
            raise HistoricalDataHTTPError("/sessions", 401, attempts=1)

    app.dependency_overrides[get_provider] = lambda: AccessRestricted()
    try:
        response = TestClient(app).get("/api/compare", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR",
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "provider_access_restricted"
    assert "HTTP 401" in response.json()["detail"]["message"]


def test_manual_candidate_laps_override_automatic_selection_and_are_reported():
    class ManualChoices(FakeProvider):
        def get_laps(self, key, driver_number=None):
            return [
                lap(1, "VER", 86.203, "2024-07-06T15:08:25.755000+00:00", lap_number=2),
                lap(1, "VER", 87.111, "2024-07-06T15:10:25.755000+00:00", lap_number=3),
                lap(4, "NOR", 86.030, "2024-07-06T15:00:21.699000+00:00", lap_number=2),
                lap(4, "NOR", 87.222, "2024-07-06T15:02:21.699000+00:00", lap_number=4),
            ]

    app.dependency_overrides[get_provider] = lambda: ManualChoices()
    try:
        response = TestClient(app).get("/api/compare", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR", "lap_a": 3, "lap_b": 4,
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    payload = response.json()
    assert payload["driver_a"]["lap"]["lap_number"] == 3
    assert payload["driver_b"]["lap"]["lap_number"] == 4
    assert payload["driver_a"]["lap"]["selection_mode"] == "manual"
    assert payload["driver_b"]["lap"]["selection_mode"] == "manual"
    assert payload["driver_a"]["lap"]["lap_time_seconds"] == pytest.approx(87.111)
    assert payload["lap_time_comparison"]["delta_seconds"] == pytest.approx(87.222 - 87.111)


@pytest.mark.parametrize("contamination", ["wrong_session", "wrong_driver"])
def test_manual_lap_number_cannot_resolve_to_another_session_or_driver(contamination):
    class ContaminatedLaps(FakeProvider):
        def get_laps(self, key, driver_number=None):
            valid_nor = lap(4, "NOR", 86.030, "2024-07-06T15:00:21.699000+00:00", lap_number=2)
            if contamination == "wrong_session":
                invalid_ver = lap(1, "VER", 87.111, "2024-07-06T15:10:25+00:00", lap_number=3)
                invalid_ver.session_key = 9999
            else:
                invalid_ver = lap(4, "NOR", 87.111, "2024-07-06T15:10:25+00:00", lap_number=3)
            return [invalid_ver, valid_nor]

    app.dependency_overrides[get_provider] = lambda: ContaminatedLaps()
    try:
        response = TestClient(app).get("/api/compare", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR", "lap_a": 3,
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "lap_not_found"


@pytest.mark.parametrize("bad_field", ["lap_time", "sector_1", "sector_2", "sector_3"])
def test_manual_lap_rejects_nonfinite_timing_metadata(bad_field):
    class NonFiniteLaps(FakeProvider):
        def get_laps(self, key, driver_number=None):
            bad = lap(1, "VER", 87.111, "2024-07-06T15:10:25+00:00", lap_number=3)
            setattr(bad, {
                "lap_time": "lap_duration_seconds",
                "sector_1": "sector_1_seconds",
                "sector_2": "sector_2_seconds",
                "sector_3": "sector_3_seconds",
            }[bad_field], float("nan"))
            return [bad, lap(4, "NOR", 86.030, "2024-07-06T15:00:21.699000+00:00")]

    app.dependency_overrides[get_provider] = lambda: NonFiniteLaps()
    try:
        response = TestClient(app).get("/api/compare", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR", "lap_a": 3,
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "lap_not_selectable"


def test_automatic_candidate_selection_skips_nonfinite_lap_record():
    provider = FakeProvider()
    nonfinite = lap(1, "VER", float("nan"), "2024-07-06T15:10:25+00:00", lap_number=1)
    provider.get_laps = lambda key, driver_number=None: [
        nonfinite,
        lap(1, "VER", 86.203, "2024-07-06T15:08:25.755000+00:00", lap_number=2),
        lap(4, "NOR", 86.030, "2024-07-06T15:00:21.699000+00:00", lap_number=2),
    ]
    result = create_comparison(provider, session_key=9554, driver_a="VER", driver_b="NOR")
    assert result.driver_a.lap.lap_number == 2
    assert result.driver_a.lap.selection_mode == "automatic"


@pytest.mark.parametrize("start", [None, "not-a-time", "2024-07-06T15:10:25"])
def test_manual_lap_requires_parseable_timezone_aware_start(start):
    class BadTimestampLaps(FakeProvider):
        def get_laps(self, key, driver_number=None):
            return [
                lap(1, "VER", 87.111, start, lap_number=3),
                lap(4, "NOR", 86.030, "2024-07-06T15:00:21.699000+00:00"),
            ]

    app.dependency_overrides[get_provider] = lambda: BadTimestampLaps()
    try:
        response = TestClient(app).get("/api/compare", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR", "lap_a": 3,
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "lap_not_selectable"


def test_manual_duplicate_lap_numbers_are_rejected_as_ambiguous():
    class DuplicateLaps(FakeProvider):
        def get_laps(self, key, driver_number=None):
            return [
                lap(1, "VER", 87.111, "2024-07-06T15:10:25+00:00", lap_number=3),
                lap(1, "VER", 87.222, "2024-07-06T15:12:25+00:00", lap_number=3),
                lap(4, "NOR", 86.030, "2024-07-06T15:00:21.699000+00:00"),
            ]

    app.dependency_overrides[get_provider] = lambda: DuplicateLaps()
    try:
        response = TestClient(app).get("/api/compare", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR", "lap_a": 3,
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "lap_ambiguous"


def test_lap_options_endpoint_is_timing_only_and_disables_unusable_rows():
    class LapOptions(FakeProvider):
        def get_laps(self, key, driver_number=None):
            return [
                lap(1, "VER", 86.203, "2024-07-06T15:08:25.755000+00:00", lap_number=2),
                LapRecord(
                    driver_number=1, session_key=9554, meeting_key=1240, lap_number=3,
                    raw_date_start="2024-07-06T15:10:25+00:00", lap_duration_seconds=87.0,
                    sector_1_seconds=27.0, sector_2_seconds=None, sector_3_seconds=24.0,
                    quality=LapQuality(is_pit_out_lap=False), source_identifiers={"lap_number": 3},
                    provenance=DataProvenance("openf1", "/laps", 3),
                ),
                lap(4, "NOR", 86.030, "2024-07-06T15:00:21.699000+00:00", lap_number=5, pit_out=True),
            ]

    provider = LapOptions()
    app.dependency_overrides[get_provider] = lambda: provider
    try:
        response = TestClient(app).get("/api/laps", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR",
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    ver, nor = body["drivers"]
    assert ver["automatic_candidate_lap_number"] == 2
    assert ver["laps"][0]["selectable"] is True
    assert ver["laps"][1]["selectable"] is False
    assert "sector 2" in ver["laps"][1]["unavailable_reason"]
    assert nor["laps"][0]["selectable"] is False
    assert "Pit-out" in nor["laps"][0]["unavailable_reason"]
    assert provider.windows == []


def test_lap_options_handles_empty_records_and_provider_failure_explicitly():
    class EmptyLaps(FakeProvider):
        def get_laps(self, key, driver_number=None):
            return []

    app.dependency_overrides[get_provider] = lambda: EmptyLaps()
    try:
        empty = TestClient(app).get("/api/laps", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR",
        })
    finally:
        app.dependency_overrides.clear()
    assert empty.status_code == 200
    assert all(driver_options["laps"] == [] for driver_options in empty.json()["drivers"])
    assert all(driver_options["automatic_candidate_lap_number"] is None for driver_options in empty.json()["drivers"])

    class FailedLaps(FakeProvider):
        def get_laps(self, key, driver_number=None):
            raise RuntimeError("synthetic provider failure")

    app.dependency_overrides[get_provider] = lambda: FailedLaps()
    try:
        failed = TestClient(app).get("/api/laps", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR",
        })
    finally:
        app.dependency_overrides.clear()
    assert failed.status_code == 502
    assert failed.json()["detail"]["code"] == "provider_unavailable"


@pytest.mark.parametrize("requested_lap, expected_status, expected_code", [
    (99, 404, "lap_not_found"),
    (3, 422, "lap_not_selectable"),
])
def test_manual_lap_selection_reports_missing_or_incomplete_records(requested_lap, expected_status, expected_code):
    class IncompleteChoices(FakeProvider):
        def get_laps(self, key, driver_number=None):
            return [
                lap(1, "VER", 86.203, "2024-07-06T15:08:25.755000+00:00", lap_number=2),
                LapRecord(
                    driver_number=1, session_key=9554, meeting_key=1240, lap_number=3,
                    raw_date_start="2024-07-06T15:10:25+00:00", lap_duration_seconds=87.0,
                    sector_1_seconds=None, sector_2_seconds=35.0, sector_3_seconds=24.0,
                    quality=LapQuality(is_pit_out_lap=False), source_identifiers={"lap_number": 3},
                    provenance=DataProvenance("openf1", "/laps", 3),
                ),
                lap(4, "NOR", 86.030, "2024-07-06T15:00:21.699000+00:00", lap_number=5),
            ]

    app.dependency_overrides[get_provider] = lambda: IncompleteChoices()
    try:
        response = TestClient(app).get("/api/compare", params={
            "session_key": 9554, "driver_a": "VER", "driver_b": "NOR", "lap_a": requested_lap,
        })
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
