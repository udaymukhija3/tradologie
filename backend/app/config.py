from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Loaded at import time so that every consumer of get_settings() observes the
# same environment. app.database builds its engine during import, which happens
# before any application entrypoint runs, so deferring this to main.py would
# leave the engine bound to the pre-.env DATABASE_URL.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    redis_url: str
    jwt_secret: str
    jwt_issuer: str
    access_token_minutes: int
    allowed_origins: tuple[str, ...]
    voice_mode: str
    public_base_url: str
    twilio_auth_token: str


def get_settings() -> Settings:
    default_origins = "http://localhost:5173,http://127.0.0.1:5173"
    return Settings(
        app_env=os.getenv("APP_ENV", "development").lower(),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./tradevoice.db"),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        jwt_secret=os.getenv("JWT_SECRET", "local-demo-secret-change-before-deploy"),
        jwt_issuer=os.getenv("JWT_ISSUER", "tradevoice"),
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "60")),
        allowed_origins=tuple(origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", default_origins).split(",") if origin.strip()),
        voice_mode=os.getenv("VOICE_MODE", "mock").lower(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", "local-twilio-test-token"),
    )


def validate_runtime_settings(settings: Settings) -> None:
    if settings.app_env != "production":
        return

    errors: list[str] = []
    if settings.database_url.startswith("sqlite"):
        errors.append("DATABASE_URL must use a production database")
    if len(settings.jwt_secret) < 32 or settings.jwt_secret in {
        "local-demo-secret-change-before-deploy",
        "local-compose-secret-replace-in-hosting",
    }:
        errors.append("JWT_SECRET must be a unique value of at least 32 characters")
    if not settings.public_base_url.startswith("https://"):
        errors.append("PUBLIC_BASE_URL must use HTTPS")
    if not settings.allowed_origins or any(not origin.startswith("https://") for origin in settings.allowed_origins):
        errors.append("ALLOWED_ORIGINS must contain only HTTPS origins")
    if settings.voice_mode not in {"mock", "openai"}:
        errors.append("VOICE_MODE must be mock or openai")

    if errors:
        raise RuntimeError("Invalid production configuration: " + "; ".join(errors))
