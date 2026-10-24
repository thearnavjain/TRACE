from requests.exceptions import Timeout

from scripts.validate_fastf1 import classify_exception, sanitize_message, sanitize_url


def test_sanitize_url_removes_credentials_query_and_fragment():
    assert sanitize_url(
        "https://user:password@example.test/path?token=secret#fragment"
    ) == "https://example.test/path"


def test_sanitize_message_redacts_authorization_and_secret_fields():
    message = "Authorization: Bearer very-secret-token; token=abc123"
    sanitized = sanitize_message(message)

    assert "very-secret-token" not in sanitized
    assert "abc123" not in sanitized
    assert "[REDACTED]" in sanitized


def test_classify_timeout_without_network_access():
    assert classify_exception(Timeout("timed out")) == "timeout"
