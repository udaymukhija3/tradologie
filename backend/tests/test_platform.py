from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.providers.twilio import TwilioProvider


def call_payload(key="call-key-0001"):
    return {
        "agent_id": "agent_demo",
        "direction": "outbound",
        "from_number": "+911204000001",
        "to_number": "+971500000001",
        "transcript": "Buyer requested a basmati rice quote. Follow up tomorrow.",
        "outcome": "qualified_lead",
        "idempotency_key": key,
    }


def test_simulated_call_lifecycle_summary_and_idempotency(client, auth_headers):
    created = client.post("/api/telephony/simulate", headers=auth_headers, json=call_payload())
    assert created.status_code == 201
    assert created.json()["status"] == "completed"
    assert created.json()["summary"].startswith("Buyer requested")
    replay = client.post("/api/telephony/simulate", headers=auth_headers, json=call_payload())
    assert replay.status_code == 200
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json()["id"] == created.json()["id"]


def test_call_validation_and_cross_workspace_agent_guard(client, other_headers):
    invalid = call_payload("call-key-invalid")
    invalid["from_number"] = "not-a-phone"
    assert client.post("/api/telephony/simulate", headers=other_headers, json=invalid).status_code == 422
    guarded = call_payload("call-key-guard")
    assert client.post("/api/telephony/simulate", headers=other_headers, json=guarded).status_code == 404


def test_signed_twilio_webhook_and_replay(client):
    settings = get_settings()
    provider = TwilioProvider(settings.twilio_auth_token)
    parameters = {"CallSid": "CA123", "CallStatus": "in-progress", "Direction": "inbound", "From": "+971500000001", "To": "+911204000001", "WorkspaceId": "workspace_demo", "AgentId": "agent_demo"}
    url = f"{settings.public_base_url}/api/telephony/twilio/events"
    signature = provider.signature(url, parameters)
    response = client.post("/api/telephony/twilio/events", data=parameters, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert response.json()["call_status"] == "active"
    assert client.post("/api/telephony/twilio/events", data=parameters, headers={"X-Twilio-Signature": "bad"}).status_code == 403


def test_twilio_webhook_fails_closed_when_provider_is_not_configured(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "settings", replace(main.settings, twilio_auth_token=""))
    response = client.post("/api/telephony/twilio/events", data={"CallSid": "CA123"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Telephony provider is not configured"}


def test_migration_builds_empty_database(tmp_path):
    database = tmp_path / "migration.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database}"}
    result = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], cwd=Path(__file__).parents[1], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert database.exists()
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "workspace_counters" in tables
        unique_indexes = [row[1] for row in connection.execute("PRAGMA index_list('users')") if row[2]]
        assert any([column[2] for column in connection.execute(f"PRAGMA index_info('{index_name}')")] == ["email"] for index_name in unique_indexes)


def test_counter_migration_upgrades_an_existing_database(tmp_path):
    database = tmp_path / "upgrade.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database}"}
    backend = Path(__file__).parents[1]
    first = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "20260822_0001"],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert first.returncode == 0, first.stderr
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
            ("existing_workspace", "Existing Workspace", "2026-08-22 00:00:00"),
        )
        connection.commit()
    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT enquiry_next, support_next FROM workspace_counters WHERE workspace_id = ?",
            ("existing_workspace",),
        ).fetchone() == (1001, 1001)


def test_one_provider_event_writes_one_audit_row(client, auth_headers):
    """Inferred intermediate states must not appear as observed events."""
    from app.database import SessionLocal
    from app.models import AuditEvent

    created = client.post("/api/telephony/simulate", headers=auth_headers, json=call_payload("audit-key-0001"))
    assert created.status_code == 201
    call_id = created.json()["id"]

    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.resource_id == call_id).order_by(AuditEvent.created_at)).all()

    # The simulator reports two states: active, then completed.
    assert [event.event_type for event in events] == ["call.active", "call.completed"]
    assert events[0].details["inferred_states"] == ["ringing"]
    assert "inferred_states" not in events[1].details


def test_out_of_order_provider_event_is_reported_not_silently_dropped(client):
    settings = get_settings()
    provider = TwilioProvider(settings.twilio_auth_token)
    url = f"{settings.public_base_url}/api/telephony/twilio/events"

    def deliver(status):
        parameters = {
            "CallSid": "CA-ooo-1",
            "CallStatus": status,
            "Direction": "inbound",
            "From": "+971500000001",
            "To": "+911204000001",
            "WorkspaceId": "workspace_demo",
            "AgentId": "agent_demo",
        }
        return client.post(
            "/api/telephony/twilio/events",
            data=parameters,
            headers={"X-Twilio-Signature": provider.signature(url, parameters)},
        )

    assert deliver("completed").json()["call_status"] == "completed"

    replayed = deliver("in-progress")
    assert replayed.status_code == 200, "carriers retry on non-2xx"
    body = replayed.json()
    assert body["status"] == "ignored"
    assert body["reason"] == "out_of_order_event"
    assert body["call_status"] == "completed"
    assert body["reported_status"] == "active"


def test_unreachable_transitions_are_rejected_at_the_service_boundary():
    from app.models import CallStatus
    from app.services.calls import CallTransitionError, transition_path

    assert transition_path(CallStatus.QUEUED, CallStatus.COMPLETED) == [
        CallStatus.RINGING,
        CallStatus.ACTIVE,
        CallStatus.COMPLETED,
    ]
    assert transition_path(CallStatus.COMPLETED, CallStatus.ACTIVE) is None
    assert issubclass(CallTransitionError, ValueError)
