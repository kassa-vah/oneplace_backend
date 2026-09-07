# ============================================================
# FILE BELONGS AT:  tests/test_rate_limits.py
# ============================================================
"""
Rate-limit tests.

Two things make these work that are easy to trip over:

1. `RATELIMIT_ENABLED` defaults to False under the "testing" config
   (see app/__init__.py) so your normal test suite doesn't get 429s
   from unrelated loops of requests. These tests explicitly flip it
   back on via app.config, just for this file.

2. Flask-Limiter's default in-memory storage persists for the life of
   the process, not per-test — so counts from one test would bleed
   into the next. The `_reset_limiter` fixture calls limiter.reset()
   before every test to start each one with a clean slate.

The Flask test client sends every request from the same fake IP, so
these all use *public* routes (no auth mocking needed) — the
rate_limit_key() fallback to get_remote_address() gives every request
in a test the same key, which is exactly what we want.
"""
import pytest

from app import create_app
from app.config import config_by_name
from app.extensions import db, limiter


@pytest.fixture()
def app(monkeypatch):
    # Must happen BEFORE create_app() runs, not after: Flask-Limiter only
    # builds its storage backend if rate limiting is enabled at the moment
    # limiter.init_app(app) executes (inside create_app). Our app defaults
    # RATELIMIT_ENABLED to False under "testing" — flipping it on app.config
    # after the fact is too late, since init_app already decided "disabled"
    # and skipped setting up storage (that's the `assert self._storage`
    # AssertionError you'd see otherwise). Patching the config class
    # attribute beforehand makes create_app() see it as enabled from the
    # start, so init_app sets storage up correctly.
    monkeypatch.setattr(config_by_name["testing"], "RATELIMIT_ENABLED", True, raising=False)

    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _reset_limiter(app):
    with app.app_context():
        limiter.reset()
    yield


def test_newsletter_subscribe_allows_up_to_limit_then_429(client):
    # subscribe() is limited to "10 per hour" — first 10 should succeed
    # (200/201 both count as "not rate limited"), the 11th should 429.
    for i in range(10):
        resp = client.post(
            "/api/newsletter/subscribe",
            json={"email": f"tester{i}@example.com"},
        )
        assert resp.status_code in (200, 201), (
            f"request {i} unexpectedly rate-limited: {resp.status_code} {resp.get_json()}"
        )

    blocked = client.post(
        "/api/newsletter/subscribe",
        json={"email": "one-too-many@example.com"},
    )
    assert blocked.status_code == 429


def test_beneficiaries_public_list_allows_up_to_limit_then_429(client):
    # list_beneficiaries() is limited to "100 per hour".
    for _ in range(100):
        resp = client.get("/api/beneficiaries")
        assert resp.status_code == 200

    blocked = client.get("/api/beneficiaries")
    assert blocked.status_code == 429


def test_health_check_is_exempt_from_rate_limiting(client):
    # /health carries @limiter.exempt — hammer it well past any of the
    # limits used elsewhere in the app and it should never 429.
    for _ in range(150):
        resp = client.get("/health")
        assert resp.status_code == 200


def test_rate_limit_response_has_retry_after(client):
    """Sanity check on the 429 shape itself — Flask-Limiter sets
    Retry-After so a well-behaved client knows when to try again."""
    for _ in range(100):
        client.get("/api/beneficiaries")

    blocked = client.get("/api/beneficiaries")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers