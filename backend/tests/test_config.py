from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import get_settings, validate_runtime_settings


def test_production_configuration_requires_external_services_and_https():
    unsafe = replace(
        get_settings(),
        app_env="production",
        database_url="sqlite:///production.db",
        jwt_secret="short",
        public_base_url="http://tradevoice.example.com",
        allowed_origins=("http://tradevoice.example.com",),
    )

    with pytest.raises(RuntimeError, match="Invalid production configuration") as error:
        validate_runtime_settings(unsafe)

    assert "DATABASE_URL" in str(error.value)
    assert "JWT_SECRET" in str(error.value)
    assert "HTTPS" in str(error.value)


def test_valid_production_configuration_passes_fast_fail_validation():
    safe = replace(
        get_settings(),
        app_env="production",
        database_url="postgresql+psycopg://app:secret@db.example.com/tradevoice",
        redis_url="rediss://redis.example.com:6380/0",
        jwt_secret="a-unique-production-secret-that-is-long-enough",
        public_base_url="https://tradevoice.example.com",
        allowed_origins=("https://tradevoice.example.com",),
    )

    validate_runtime_settings(safe)
