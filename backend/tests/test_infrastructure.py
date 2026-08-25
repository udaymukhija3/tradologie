from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from app.providers import get_provider, supported_providers
from app.providers.base import TelephonyProvider
from app.rate_limit import SlidingWindowLimiter


def test_registered_adapters_satisfy_the_telephony_protocol():
    for name in supported_providers():
        provider = get_provider(name, "token")
        assert isinstance(provider, TelephonyProvider)
        assert provider.name == name


def test_unknown_provider_resolves_to_none():
    assert get_provider("carrier-that-does-not-exist", "token") is None


def test_limiter_still_enforces_its_window():
    limiter = SlidingWindowLimiter()
    for _ in range(3):
        limiter.check("caller", limit=3, window_seconds=60)
    with pytest.raises(HTTPException) as error:
        limiter.check("caller", limit=3, window_seconds=60)
    assert error.value.status_code == 429


def test_limiter_releases_keys_it_can_no_longer_be_limiting(monkeypatch):
    """Draining timestamps leaves the key; a sweep must drop it."""
    monkeypatch.setattr("app.rate_limit.SWEEP_INTERVAL_SECONDS", 0)
    limiter = SlidingWindowLimiter()

    for index in range(500):
        limiter.check(f"session-{index}", limit=10, window_seconds=1)
    assert limiter.tracked_keys() == 500

    time.sleep(1.05)
    limiter.check("session-fresh", limit=10, window_seconds=1)
    assert limiter.tracked_keys() == 1


def test_sweep_never_drops_a_key_inside_its_window(monkeypatch):
    monkeypatch.setattr("app.rate_limit.SWEEP_INTERVAL_SECONDS", 0)
    limiter = SlidingWindowLimiter()
    limiter.check("wide", limit=2, window_seconds=3600)
    limiter.check("narrow", limit=2, window_seconds=1)
    limiter.check("trigger-sweep", limit=2, window_seconds=1)
    assert limiter.tracked_keys() == 3
