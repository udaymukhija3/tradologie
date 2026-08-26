from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import ClassVar

from app.voice import openai_realtime

CONTEXT = {"current_page": "distributor_directory", "selected_distributor_id": "dist_001"}


def create_owned_session():
    return openai_realtime.session_store.create({**CONTEXT, "buyer": {"id": "user_demo_admin", "name": "Arjun Mehta"}, "user_id": "user_demo_admin", "workspace_id": "workspace_demo", "role": "admin"})


def test_realtime_configuration_has_tools_and_bounded_sessions(monkeypatch):
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1")
    configuration = openai_realtime.build_session_configuration(CONTEXT)
    assert configuration["model"] == "gpt-realtime-2.1"
    assert configuration["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert {tool["name"] for tool in configuration["tools"]} == {"get_distributor", "search_distributors", "get_enquiry_status", "create_enquiry", "request_human_support"}


def test_missing_key_fails_closed(client, auth_headers, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import app.main as main

    monkeypatch.setattr(main, "settings", replace(main.settings, voice_mode="openai"))
    response = client.post("/api/realtime/call", headers={**auth_headers, "origin": "http://localhost:5173"}, json={"sdp": "v=0\r\n", "context": CONTEXT})
    assert response.status_code == 503


def test_realtime_call_keeps_server_key_and_binds_owner(client, auth_headers, monkeypatch):
    captured = {}

    class FakeResponse:
        is_success = True
        status_code = 201
        text = "v=0\r\na=answer\r\n"
        headers: ClassVar[dict[str, str]] = {"location": "/v1/realtime/calls/call_demo", "x-request-id": "req_demo"}

    class FakeClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, *, headers, files):
            captured.update(url=url, headers=headers, files=files)
            return FakeResponse()

    import app.main as main

    monkeypatch.setattr(main, "settings", replace(main.settings, voice_mode="openai"))
    monkeypatch.setenv("OPENAI_API_KEY", "server-test-key")
    monkeypatch.setattr(openai_realtime.httpx, "AsyncClient", FakeClient)
    response = client.post("/api/realtime/call", headers={**auth_headers, "origin": "http://localhost:5173"}, json={"sdp": "v=0\r\n", "context": {**CONTEXT, "buyer": {"id": "attacker"}}})
    assert response.status_code == 200
    session_id = response.headers["x-tradevoice-session-id"]
    assert openai_realtime.session_store.get(session_id).context["user_id"] == "user_demo_admin"
    assert captured["headers"]["Authorization"] == "Bearer server-test-key"
    assert "server-test-key" not in response.text


def test_confirmation_is_one_time_and_gateway_replay_is_concurrency_safe(client, auth_headers):
    session = create_owned_session()
    args = {"product": "Basmati Rice", "quantity": 50, "unit": "tonnes", "destination": "Dubai", "distributor_id": None}
    url = f"/api/realtime/sessions/{session.session_id}/tools"
    proposed = client.post(url, headers=auth_headers, json={"call_id": "prepare", "name": "create_enquiry", "arguments": args})
    assert proposed.json()["result"]["status"] == "confirmation_required"
    observed = client.post(f"/api/realtime/sessions/{session.session_id}/events", headers=auth_headers, json={"type": "user_transcript", "transcript": "confirm"})
    assert observed.json()["status"] == "confirmation_recorded"

    def create_once(_):
        return client.post(url, headers=auth_headers, json={"call_id": "create-same-call", "name": "create_enquiry", "arguments": args}).json()["result"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(create_once, range(10)))
    assert {result["enquiry_id"] for result in results} == {"ENQ-1004"}
    fresh = client.post(url, headers=auth_headers, json={"call_id": "fresh-intent", "name": "create_enquiry", "arguments": args})
    assert fresh.json()["result"]["status"] == "confirmation_required"


def test_session_is_bound_to_user(client, other_headers):
    session = create_owned_session()
    response = client.post(f"/api/realtime/sessions/{session.session_id}/tools", headers=other_headers, json={"call_id": "read", "name": "search_distributors", "arguments": {}})
    assert response.status_code == 404


def test_tool_schemas_are_lowercase_json_schema():
    """OpenAI requires JSON Schema types; uppercase is Gemini's dialect.

    The schemas were authored uppercase and rewritten at the boundary by a
    recursive converter. They are now authored correctly, so this pins the
    shape rather than the conversion.
    """

    def types(node):
        if isinstance(node, dict):
            if isinstance(node.get("type"), str):
                yield node["type"]
            for value in node.values():
                yield from types(value)
        elif isinstance(node, list):
            for item in node:
                yield from types(item)

    definitions = openai_realtime.realtime_tool_definitions()
    assert definitions

    for definition in definitions:
        assert definition["type"] == "function"
        assert definition["parameters"]["type"] == "object"
        for declared in types(definition["parameters"]):
            assert declared == declared.lower(), f"{definition['name']}: {declared!r} is not JSON Schema"
