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
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token[:-1]}x"})
    assert response.status_code == 401
