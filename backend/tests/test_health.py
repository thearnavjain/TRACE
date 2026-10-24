from fastapi.routing import APIRoute

from app.main import app, health


def test_health_returns_ok() -> None:
    route = next(
        route for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/api/health"
    )
    assert route.methods == {"GET"}
    assert route.endpoint() == {"status": "ok", "service": "trace-api"}
