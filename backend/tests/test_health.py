from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

import app.api.compare as compare_api
from app.main import app, health


def test_health_returns_ok() -> None:
    route = next(
        route for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/api/health"
    )
    assert route.methods == {"GET"}
    assert route.endpoint() == {"status": "ok", "service": "trace-api"}


def test_health_http_request_does_not_create_provider(monkeypatch) -> None:
    def fail_if_provider_is_requested():
        raise AssertionError("health checks must not initialize a data provider")

    monkeypatch.setattr(compare_api, "OpenF1HistoricalData", fail_if_provider_is_requested)
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "trace-api"}
