import pytest

from app.main import _frontend_origins


def test_local_origins_are_always_allowed_without_deployment_config() -> None:
    assert _frontend_origins(None) == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_https_frontend_origin_is_normalized_and_added() -> None:
    assert _frontend_origins(" HTTPS://Trace.Example:443/ ") == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://trace.example",
    ]


@pytest.mark.parametrize(
    "configured",
    [
        "*",
        "https://*.example.com",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com?query=1",
        "https://example.com#fragment",
        "http://example.com",
        "https://one.example,https://two.example",
        "https://example.com with-space",
        "not an origin",
    ],
)
def test_unsafe_or_non_origin_frontend_configuration_is_rejected(configured: str) -> None:
    with pytest.raises(ValueError, match="FRONTEND_ORIGIN"):
        _frontend_origins(configured)


def test_local_http_frontend_origin_is_allowed_and_not_duplicated() -> None:
    assert _frontend_origins("http://localhost:3000/") == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
