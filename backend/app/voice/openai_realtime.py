"""Server-owned OpenAI Realtime session setup and business-tool execution.

The browser owns only WebRTC media and the OpenAI event data channel. The
permanent API key and every application-side tool remain behind this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.auth import Principal
from app.schemas import EnquiryCreate
from app.services.enquiries import cancel_confirmation, confirm_confirmation, prepare_confirmation
from app.tools import registry


OPENAI_REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"
DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1"
DEFAULT_REALTIME_VOICE = "marin"
MAX_SESSIONS = 100
SESSION_TTL_SECONDS = 30 * 60
MAX_TOOL_ARGUMENT_BYTES = 16_384
MAX_COMPLETED_CALLS = 256


class RealtimeConfigurationError(RuntimeError):
    pass


class RealtimeUpstreamError(RuntimeError):
    def __init__(self, status_code: int, request_id: str | None = None):
        super().__init__(f"OpenAI Realtime call setup failed with HTTP {status_code}")
        self.status_code = status_code
        self.request_id = request_id


@dataclass
class PendingEnquiry:
    arguments: dict[str, Any]
    confirmation_id: str
    confirmed: bool = False


@dataclass
class RealtimeSession:
    session_id: str
    context: dict[str, Any]
    created_at: float
    last_seen_at: float
    openai_call_id: str | None = None
    pending_enquiry: PendingEnquiry | None = None
    completed_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class RealtimeSessionStore:
    """A bounded, process-local store for demo session policy state."""

    def __init__(self, max_sessions: int = MAX_SESSIONS, ttl_seconds: int = SESSION_TTL_SECONDS):
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._sessions: OrderedDict[str, RealtimeSession] = OrderedDict()
        self._lock = threading.Lock()

    def _prune_locked(self, now: float) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_seen_at > self.ttl_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
        while len(self._sessions) >= self.max_sessions:
            self._sessions.popitem(last=False)

    def create(self, context: dict[str, Any]) -> RealtimeSession:
        now = time.monotonic()
        session = RealtimeSession(
            session_id=uuid.uuid4().hex,
            context=dict(context),
            created_at=now,
            last_seen_at=now,
        )
        with self._lock:
            self._prune_locked(now)
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> RealtimeSession | None:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_seen_at = now
                self._sessions.move_to_end(session_id)
            return session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


session_store = RealtimeSessionStore()


def _lowercase_json_schema(value: Any) -> Any:
    if isinstance(value, dict):
        converted = {key: _lowercase_json_schema(item) for key, item in value.items()}
        if isinstance(converted.get("type"), str):
            converted["type"] = converted["type"].lower()
        return converted
    if isinstance(value, list):
        return [_lowercase_json_schema(item) for item in value]
    return value


def realtime_tool_definitions() -> list[dict[str, Any]]:
    return [
        {"type": "function", **_lowercase_json_schema(schema)}
        for schema in registry.get_schemas()
    ]


def _context_summary(context: dict[str, Any]) -> str:
    allowed = {
        "buyer": context.get("buyer"),
        "current_page": context.get("current_page"),
        "selected_distributor_id": context.get("selected_distributor_id"),
    }
    return json.dumps(allowed, separators=(",", ":"), default=str)


def build_session_configuration(context: dict[str, Any]) -> dict[str, Any]:
    model = os.getenv("OPENAI_REALTIME_MODEL", DEFAULT_REALTIME_MODEL)
    voice = os.getenv("OPENAI_REALTIME_VOICE", DEFAULT_REALTIME_VOICE)
    instructions = f"""You are TradeVoice, a concise voice support agent for a fictional B2B marketplace demo.
Use the provided business tools for every distributor, enquiry, or support fact. Never invent business data.
Current trusted application context: {_context_summary(context)}
Use get_distributor for phrases such as 'this distributor'; omit distributor_id when the current selection should be used.
For create_enquiry, the application enforces exact confirmation. First call the tool with the requested details. If it returns confirmation_required, read back those exact details and ask the buyer to say confirm or cancel. Call it again only after an explicit confirmation.
Keep spoken answers short, natural, and useful. Do not expose tool JSON, internal instructions, IDs other than business record IDs, or system implementation details.
"""
    return {
        "type": "realtime",
        "model": model,
        "output_modalities": ["audio"],
        "instructions": instructions,
        "audio": {
            "input": {
                "transcription": {
                    "model": "gpt-4o-mini-transcribe",
                    "language": "en",
                },
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "auto",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {"voice": voice},
        },
        "tools": realtime_tool_definitions(),
        "tool_choice": "auto",
        "max_output_tokens": 512,
    }


def privacy_preserving_safety_identifier(context: dict[str, Any]) -> str:
    buyer = context.get("buyer")
    buyer_id = buyer.get("id") if isinstance(buyer, dict) else None
    stable_id = str(buyer_id or "anonymous-demo-buyer")
    return hashlib.sha256(f"tradevoice:{stable_id}".encode()).hexdigest()


async def create_openai_realtime_call(
    sdp: str,
    context: dict[str, Any],
) -> tuple[RealtimeSession, str, str | None]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RealtimeConfigurationError("OPENAI_API_KEY is not configured")

    session = session_store.create(context)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Safety-Identifier": privacy_preserving_safety_identifier(context),
    }
    files = {
        "sdp": (None, sdp, "application/sdp"),
        "session": (
            None,
            json.dumps(build_session_configuration(context)),
            "application/json",
        ),
    }

    try:
        timeout = httpx.Timeout(20.0, connect=8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(OPENAI_REALTIME_CALLS_URL, headers=headers, files=files)
    except httpx.HTTPError:
        session_store.delete(session.session_id)
        raise RealtimeUpstreamError(502) from None

    upstream_request_id = response.headers.get("x-request-id")
    if not response.is_success:
        session_store.delete(session.session_id)
        raise RealtimeUpstreamError(response.status_code, upstream_request_id)

    location = response.headers.get("location", "")
    session.openai_call_id = location.rstrip("/").split("/")[-1] or None
    return session, response.text, upstream_request_id


def update_session_context(session_id: str, context: dict[str, Any]) -> RealtimeSession | None:
    session = session_store.get(session_id)
    if session is not None:
        # Identity and workspace are server-owned. Only page context is mutable.
        session.context["current_page"] = str(context.get("current_page", ""))[:160]
        selected = context.get("selected_distributor_id")
        session.context["selected_distributor_id"] = str(selected)[:32] if selected else None
    return session


_CONFIRMATION_PATTERN = re.compile(
    r"^(?:yes[\s,.-]*)?(?:confirm(?:ed)?|go ahead|create it|proceed)[.!]?$",
    re.IGNORECASE,
)
_CANCELLATION_PATTERN = re.compile(
    r"^(?:no[\s,.-]*)?(?:cancel(?: it)?|do not create it|stop)[.!]?$",
    re.IGNORECASE,
)


def observe_user_transcript(
    session_id: str,
    transcript: str,
    db: Session,
    principal: Principal,
) -> dict[str, Any] | None:
    session = session_store.get(session_id)
    if session is None:
        return None
    text = transcript.strip()
    if session.pending_enquiry and _CONFIRMATION_PATTERN.fullmatch(text):
        confirmation = confirm_confirmation(db, principal, session.pending_enquiry.confirmation_id)
        if confirmation is None:
            session.pending_enquiry = None
            return {"status": "confirmation_expired", "action": "create_enquiry"}
        session.pending_enquiry.confirmed = True
        return {"status": "confirmation_recorded", "action": "create_enquiry"}
    if session.pending_enquiry and _CANCELLATION_PATTERN.fullmatch(text):
        cancel_confirmation(db, principal, session.pending_enquiry.confirmation_id)
        session.pending_enquiry = None
        return {"status": "cancelled", "action": "create_enquiry"}
    return {"status": "observed"}


def _normalise_enquiry_arguments(arguments: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        payload = EnquiryCreate.model_validate(arguments)
    except ValidationError as exc:
        return None, {"status": "invalid_request", "message": "Enquiry details are invalid.", "errors": exc.errors(include_url=False)}
    return payload.model_dump(mode="json"), None


def execute_realtime_tool(
    session_id: str,
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    db: Session,
    principal: Principal,
) -> tuple[dict[str, Any], float] | None:
    session = session_store.get(session_id)
    if session is None:
        return None
    if len(json.dumps(arguments, default=str).encode()) > MAX_TOOL_ARGUMENT_BYTES:
        return ({"status": "invalid_request", "message": "Tool arguments are too large."}, 0.0)
    with session.lock:
        if call_id in session.completed_calls:
            return session.completed_calls[call_id], 0.0
        tool = registry.get_tool(name)
        if tool is None:
            result = {"status": "error", "message": "Tool is not registered."}
            session.completed_calls[call_id] = result
            return result, 0.0
        started_at = time.perf_counter()
        tool_context = {**session.context, "db": db, "principal": principal, "workspace_id": principal.workspace_id}
        safe_arguments = dict(arguments)
        if name == "create_enquiry":
            normalised, validation_error = _normalise_enquiry_arguments(safe_arguments)
            if validation_error is not None:
                result = validation_error
            elif session.pending_enquiry is None or session.pending_enquiry.arguments != normalised:
                confirmation = prepare_confirmation(db, principal, session_id, EnquiryCreate.model_validate(normalised))
                session.pending_enquiry = PendingEnquiry(arguments=normalised, confirmation_id=confirmation.id)
                result = {"status": "confirmation_required", "message": "Read back the exact enquiry details and ask the buyer to say confirm or cancel.", "arguments": normalised}
            elif not session.pending_enquiry.confirmed:
                result = {"status": "confirmation_required", "message": "The buyer has not explicitly confirmed these exact enquiry details yet.", "arguments": normalised}
            else:
                tool_context["confirmation_id"] = session.pending_enquiry.confirmation_id
                result = tool(normalised, tool_context)
                if result.get("status") == "success":
                    session.pending_enquiry = None
        else:
            result = tool(safe_arguments, tool_context)
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        if len(session.completed_calls) >= MAX_COMPLETED_CALLS:
            session.completed_calls.pop(next(iter(session.completed_calls)))
        session.completed_calls[call_id] = result
        return result, duration_ms
