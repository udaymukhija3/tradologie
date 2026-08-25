from __future__ import annotations

import os
import tempfile

import pytest

TEST_DATABASE = os.path.join(tempfile.gettempdir(), f"tradevoice-tests-{os.getpid()}.db")
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", f"sqlite:///{TEST_DATABASE}")
os.environ["AUTO_CREATE_SCHEMA"] = "false"
os.environ["AUTO_SEED"] = "false"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/15"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-tests"

from starlette.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.rate_limit import limiter
from app.seed_data import DEMO_PASSWORD, seed_database
from app.voice.openai_realtime import session_store


@pytest.fixture(autouse=True)
def reset_database():
    session_store.clear()
    limiter.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()
    Base.metadata.create_all(engine)
    engine.dispose()
    with SessionLocal() as db:
        seed_database(db)
    yield
    session_store.clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def login_headers(client: TestClient, email: str = "arjun@horizon.example") -> dict[str, str]:
    response = client.post("/api/auth/token", json={"email": email, "password": DEMO_PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def auth_headers(client):
    return login_headers(client)


@pytest.fixture
def other_headers(client):
    return login_headers(client, "admin@other.example")
