from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
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
        database_url=os.getenv("DATABASE_URL", "sqlite:///./tradevoice.db"),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        jwt_secret=os.getenv("JWT_SECRET", "local-demo-secret-change-before-deploy"),
        jwt_issuer=os.getenv("JWT_ISSUER", "tradevoice"),
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "60")),
        allowed_origins=tuple(
            origin.strip()
            for origin in os.getenv("ALLOWED_ORIGINS", default_origins).split(",")
            if origin.strip()
        ),
        voice_mode=os.getenv("VOICE_MODE", "mock").lower(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", "local-twilio-test-token"),
    )
