from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User, UserRole


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: str
    workspace_id: str
    email: str
    name: str
    role: UserRole


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, expected_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (TypeError, ValueError):
        return False


_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password_or_dummy(password: str, encoded: str | None) -> bool:
    """Verify a password, spending equal work when the account does not exist.

    Skipping the KDF for an unknown email makes a miss roughly twenty times
    faster than a wrong password, which turns a deliberately generic 401 into a
    reliable account-enumeration oracle. Hashing against a throwaway digest with
    identical cost parameters keeps login latency independent of existence.
    """
    if encoded is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return False
    return verify_password(password, encoded)


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "iss": settings.jwt_issuer,
        "sub": user.id,
        "workspace_id": user.workspace_id,
        "role": user.role.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iss", "sub", "workspace_id", "role"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token") from exc


def principal_from_token(token: str, db: Session) -> Principal:
    claims = decode_access_token(token)
    user = db.scalar(select(User).where(User.id == claims["sub"], User.is_active.is_(True)))
    if user is None or user.workspace_id != claims["workspace_id"] or user.role.value != claims["role"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access token is no longer valid")
    return Principal(user.id, user.workspace_id, user.email, user.name, user.role)


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    return principal_from_token(credentials.credentials, db)


def require_roles(*roles: UserRole):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient workspace role")
        return principal
    return dependency
