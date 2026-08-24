from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import AuditEvent, Call, CallStatus, VoiceAgent
from app.schemas import SimulatedCallRequest


TERMINAL_STATUSES = {CallStatus.COMPLETED, CallStatus.FAILED}
ALLOWED_TRANSITIONS = {
    CallStatus.QUEUED: {CallStatus.RINGING, CallStatus.FAILED},
    CallStatus.RINGING: {CallStatus.ACTIVE, CallStatus.FAILED},
    CallStatus.ACTIVE: {CallStatus.TRANSFERRED, CallStatus.COMPLETED, CallStatus.FAILED},
    CallStatus.TRANSFERRED: {CallStatus.COMPLETED, CallStatus.FAILED},
    CallStatus.COMPLETED: set(),
    CallStatus.FAILED: set(),
}


def create_simulated_call(db: Session, principal: Principal, payload: SimulatedCallRequest) -> tuple[Call, bool]:
    existing = db.scalar(select(Call).where(Call.workspace_id == principal.workspace_id, Call.idempotency_key == payload.idempotency_key))
    if existing is not None:
        return existing, True
    agent = db.scalar(select(VoiceAgent).where(VoiceAgent.id == payload.agent_id, VoiceAgent.workspace_id == principal.workspace_id, VoiceAgent.is_active.is_(True)))
    if agent is None:
        raise ValueError("Voice agent was not found in this workspace")
    call = Call(workspace_id=principal.workspace_id, agent_id=agent.id, created_by_id=principal.user_id, provider="simulator", provider_call_id=f"SIM-{uuid.uuid4().hex[:12]}", direction=payload.direction, from_number=payload.from_number, to_number=payload.to_number, status=CallStatus.QUEUED, transcript=payload.transcript, outcome=payload.outcome, idempotency_key=payload.idempotency_key)
    db.add(call)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = db.scalar(select(Call).where(Call.workspace_id == principal.workspace_id, Call.idempotency_key == payload.idempotency_key))
        if replay is None:
            raise
        return replay, True
    db.refresh(call)
    return call, False


def transition_call(db: Session, call: Call, target: CallStatus, *, transcript: str | None = None, outcome: str | None = None) -> Call:
    if target == call.status:
        return call
    if target not in ALLOWED_TRANSITIONS[call.status]:
        raise ValueError(f"Invalid call transition: {call.status.value} -> {target.value}")
    now = datetime.now(timezone.utc)
    call.status = target
    if target == CallStatus.ACTIVE and call.started_at is None:
        call.started_at = now
    if target in TERMINAL_STATUSES:
        call.ended_at = now
    if transcript is not None:
        call.transcript = transcript[:20_000]
    if outcome is not None:
        call.outcome = outcome[:80]
    db.add(AuditEvent(workspace_id=call.workspace_id, actor_id=call.created_by_id, event_type=f"call.{target.value}", resource_type="call", resource_id=call.id, details={"provider": call.provider}))
    db.commit()
    db.refresh(call)
    return call


def advance_call(db: Session, call: Call, target: CallStatus, *, transcript: str | None = None, outcome: str | None = None) -> Call:
    """Advance through valid intermediate states when providers omit callbacks."""
    paths = {
        CallStatus.QUEUED: [CallStatus.QUEUED],
        CallStatus.RINGING: [CallStatus.RINGING],
        CallStatus.ACTIVE: [CallStatus.RINGING, CallStatus.ACTIVE],
        CallStatus.TRANSFERRED: [CallStatus.RINGING, CallStatus.ACTIVE, CallStatus.TRANSFERRED],
        CallStatus.COMPLETED: [CallStatus.RINGING, CallStatus.ACTIVE, CallStatus.COMPLETED],
        CallStatus.FAILED: [CallStatus.FAILED],
    }
    if target == call.status:
        return call
    for step in paths[target]:
        if step == call.status:
            continue
        if step in ALLOWED_TRANSITIONS[call.status]:
            call = transition_call(db, call, step, transcript=transcript if step == target else None, outcome=outcome if step == target else None)
    return call
