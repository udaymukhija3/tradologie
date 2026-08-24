from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect


CONTEXT = {"current_page": "distributor_directory", "selected_distributor_id": "dist_001", "buyer": {"id": "attacker"}}


def token(client, auth_headers):
    return auth_headers["Authorization"].removeprefix("Bearer ")


def ask(websocket, text):
    websocket.send_json({"type": "text", "text": text})
    events = []
    while True:
        event = websocket.receive_json()
        events.append(event)
        if event["type"] == "agent_response":
            return events


def event(events, event_type):
    return next(item for item in events if item["type"] == event_type)


def test_authenticated_mock_voice_flow_persists_enquiry(client, auth_headers):
    with client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json({"context": CONTEXT, "access_token": token(client, auth_headers)})
        assert websocket.receive_json()["engine"] == "local_demo"
        assert "Eastern Grain Trading" in ask(websocket, "Tell me about this distributor.")[-1]["text"]
        request = ask(websocket, "I need 50 tonnes of basmati rice for Dubai.")
        assert event(request, "confirmation_required")["arguments"]["quantity"] == 50
        assert not any(item["type"] == "tool_requested" for item in request)
        confirmed = ask(websocket, "confirm")
        assert event(confirmed, "tool_completed")["result"]["enquiry"]["buyer_id"] == "user_demo_admin"
    enquiries = client.get("/api/enquiries", headers=auth_headers).json()
    assert enquiries[0]["display_id"] == "ENQ-1004"


def test_client_context_cannot_replace_authenticated_identity(client, auth_headers):
    with client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json({"context": CONTEXT, "access_token": token(client, auth_headers)})
        websocket.receive_json()
        websocket.send_json({"type": "context_update", "context": {"selected_distributor_id": "dist_002", "buyer": {"id": "user_other_admin"}}})
        assert websocket.receive_json()["selected_distributor_id"] == "dist_002"
        ask(websocket, "I need 10 tonnes of rice for Dubai.")
        confirmed = ask(websocket, "confirm")
        assert event(confirmed, "tool_completed")["result"]["enquiry"]["buyer_id"] == "user_demo_admin"


def test_missing_token_and_untrusted_origin_are_rejected(client):
    with pytest.raises(WebSocketDisconnect) as missing:
        with client.websocket_connect("/ws/voice") as websocket:
            websocket.send_json({"context": CONTEXT})
            websocket.receive_json()
    assert missing.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as origin:
        with client.websocket_connect("/ws/voice", headers={"origin": "https://untrusted.example"}):
            pass
    assert origin.value.code == 1008


def test_confirmation_without_pending_action_cannot_write(client, auth_headers):
    with client.websocket_connect("/ws/voice") as websocket:
        websocket.send_json({"context": CONTEXT, "access_token": token(client, auth_headers)})
        websocket.receive_json()
        result = ask(websocket, "confirm")
        assert "no pending action" in result[-1]["text"].lower()
    assert len(client.get("/api/enquiries", headers=auth_headers).json()) == 3
