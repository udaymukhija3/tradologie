import base64
import json


def test_liveness_readiness_and_runtime(client):
    live = client.get("/api/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert live.headers["x-request-id"]
    assert "app;dur=" in live.headers["server-timing"]
    ready = client.get("/api/health/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"
    assert ready.json()["redis"] == "degraded"
    assert ready.json()["summary_processing_depth"] is None
    assert ready.json()["summary_dead_letter_depth"] is None


def test_internal_metrics_have_bounded_route_labels(client):
    client.get("/api/health/live")
    metrics = client.get("/internal/metrics")

    assert metrics.status_code == 200
    assert "tradevoice_http_requests_total" in metrics.text
    assert 'route="/api/health/live"' in metrics.text
    assert "tradevoice_db_pool_checked_out_connections" in metrics.text
    assert "tradevoice_summary_worker_available" in metrics.text


def test_authentication_is_required_and_bad_credentials_are_generic(client):
    assert client.get("/api/enquiries").status_code == 401
    rejected = client.post("/api/auth/token", json={"email": "missing@example.com", "password": "wrong-pass"})
    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "Invalid email or password"}


def test_workspace_data_is_isolated(client, auth_headers, other_headers):
    demo_names = {row["name"] for row in client.get("/api/distributors", headers=auth_headers).json()}
    other_names = {row["name"] for row in client.get("/api/distributors", headers=other_headers).json()}
    assert "Eastern Grain Trading" in demo_names
    assert other_names == {"Private Other Distributor"}
    assert len(client.get("/api/enquiries", headers=auth_headers).json()) == 3
    assert client.get("/api/enquiries", headers=other_headers).json() == []


def test_login_returns_the_authenticated_workspace(client):
    response = client.post("/api/auth/token", json={"email": "admin@other.example", "password": "TradeVoice123!"})
    assert response.status_code == 200
    assert response.json()["user"]["workspace_name"] == "Other Workspace"


def test_dashboard_is_workspace_scoped(client, auth_headers):
    dashboard = client.get("/api/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["counts"] == {"agents": 1, "calls": 0, "active_calls": 0, "enquiries": 3}
    assert dashboard.json()["agents"][0]["name"] == "Trade Support Agent"


def test_tampered_token_is_rejected(client, auth_headers):
    """Both a mutated signature and forged claims must be refused.

    Mutating only the final signature character is not a reliable tamper: the
    last base64url character of an HS256 signature carries four significant
    bits, so a fixed substitution decodes to the same 32 bytes roughly one time
    in sixteen and the token still verifies.
    """
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    header, payload, signature = token.split(".")

    mutated = ("B" if signature[0] != "B" else "C") + signature[1:]
    assert client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {header}.{payload}.{mutated}"}
    ).status_code == 401

    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["workspace_id"] = "workspace_other"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    assert client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {header}.{forged}.{signature}"}
    ).status_code == 401


def test_login_spends_equal_hashing_work_on_unknown_accounts(monkeypatch):
    """A missing account must not skip the KDF, or 401 latency leaks existence."""
    from app import auth

    stored = auth.hash_password("real-password")

    calls = []
    real = auth.hashlib.pbkdf2_hmac
    monkeypatch.setattr(
        auth.hashlib,
        "pbkdf2_hmac",
        lambda *args, **kwargs: calls.append(args[0]) or real(*args, **kwargs),
    )

    assert auth.verify_password_or_dummy("wrong-password", stored) is False
    wrong_password = len(calls)

    calls.clear()
    assert auth.verify_password_or_dummy("wrong-password", None) is False
    unknown_account = len(calls)

    assert wrong_password == unknown_account == 1
