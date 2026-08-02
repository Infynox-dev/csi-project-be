"""Assert-based check: scrubber redacts auth headers and drops auth bodies / health."""

from app.common.telemetry import scrub_sentry_event


def test_scrubber_drops_health_events():
    event = {"request": {"url": "https://staging-api.example/api/health"}}
    assert scrub_sentry_event(event, {}) is None


def test_scrubber_filters_authorization_and_drops_auth_body():
    event = {
        "request": {
            "url": "https://staging-api.example/api/auth/login",
            "headers": {
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/json",
            },
            "data": {"password": "hunter2", "email": "a@b.c"},
        },
        "user": {"email": "a@b.c", "ip_address": "1.2.3.4"},
    }
    out = scrub_sentry_event(event, {})
    assert out is not None
    assert out["request"]["headers"]["Authorization"] == "[Filtered]"
    assert out["request"]["headers"]["Content-Type"] == "application/json"
    assert "data" not in out["request"]
    assert "email" not in out["user"]
    assert "ip_address" not in out["user"]


def test_scrubber_keeps_non_sensitive_get_body_absent():
    event = {
        "request": {
            "url": "https://staging-api.example/api/units",
            "headers": {"Accept": "application/json"},
        }
    }
    out = scrub_sentry_event(event, {})
    assert out is not None
    assert out["request"]["headers"]["Accept"] == "application/json"
