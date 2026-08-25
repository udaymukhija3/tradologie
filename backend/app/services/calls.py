from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime

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

# Set iteration order is not stable across runs; fixing an order keeps the
# inferred path deterministic when two routes share a length.
_STATUS_ORDER = [
    CallStatus.QUEUED,
    CallStatus.RINGING,
    CallStatus.ACTIVE,
    CallStatus.TRANSFERRED,
    CallStatus.COMPLETED,
    CallStatus.FAILED,
]


class CallTransitionError(ValueError):
    """A provider reported a state this call cannot legally reach."""

    def __init__(self, current: CallStatus, target: CallStatus):
        super().__init__(f"Invalid call transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def transition_path(current: CallStatus, target: CallStatus) -> list[CallStatus] | None:
    """Shortest legal route between two states, or None if unreachable.

    Derived from ALLOWED_TRANSITIONS so the graph has exactly one definition.
    """
    if current == target:
        return []
    queue: deque[tuple[CallStatus, list[CallStatus]]] = deque([(current, [])])
    seen = {current}
    while queue:
        node, path = queue.popleft()
        for candidate in sorted(ALLOWED_TRANSITIONS[node], key=_STATUS_ORDER.index):
            if candidate in seen:
                continue
            seen.add(candidate)
            route = [*path, candidate]
            if candidate == target:
                return route
            queue.append((candidate, route))
    return None


def create_simulated_call(db: Session, principal: Principal, payload: SimulatedCallRequest) -> tuple[Call, bool]:
    existing = db.scalar(select(Call).where(Call.workspace_id == principal.workspace_id, Call.idempotency_key == payload.idempotency_key))
    if existing is not None:
        return existing, True
    agent = db.scalar(select(VoiceAgent).where(VoiceAgent.id == payload.agent_id, VoiceAgent.workspace_id == principal.workspace_id, VoiceAgent.is_active.is_(True)))
    if agent is None:
        raise ValueError("Voice agent was not found in this workspace")
    call = Call(
        workspace_id=principal.workspace_id,
        agent_id=agent.id,
        created_by_id=principal.user_id,
        provider="simulator",
        provider_call_id=f"SIM-{uuid.uuid4().hex[:12]}",
        direction=payload.direction,
        from_number=payload.from_number,
        to_number=payload.to_number,
        status=CallStatus.QUEUED,
        transcript=payload.transcript,
        outcome=payload.outcome,
        idempotency_key=payload.idempotency_key,
    )
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


def advance_call(
    db: Session,
    call: Call,
    target: CallStatus,
    *,
    transcript: str | None = None,
    outcome: str | None = None,
) -> Call:
    """Apply a reported call state, inferring any states the provider skipped.

    Carriers omit callbacks, so a provider can report `completed` on a call we
    still hold as `queued`. The intermediate states are inferred to keep the
    state machine consistent, but only the reported state is written to the
    audit log: emitting one audit row per inferred hop records `ringing` and
    `active` events that no provider ever sent, which makes the audit trail a
    record of this function's internals rather than of what happened.

    Raises CallTransitionError when the target is unreachable, so an
    out-of-order or replayed provider event is surfaced instead of silently
    becoming a no-op that still answers 200.
    """
    if call.status == target:
        return call

    route = transition_path(call.status, target)
    if route is None:
        raise CallTransitionError(call.status, target)

    now = datetime.now(UTC)
    origin = call.status
    for step in route:
        call.status = step
        if step == CallStatus.ACTIVE and call.started_at is None:
            call.started_at = now
        if step in TERMINAL_STATUSES:
            call.ended_at = now

    if transcript is not None:
        call.transcript = transcript[:20_000]
    if outcome is not None:
        call.outcome = outcome[:80]

    details: dict[str, object] = {"provider": call.provider, "from_status": origin.value}
    inferred = [step.value for step in route[:-1]]
    if inferred:
        details["inferred_states"] = inferred

    db.add(
        AuditEvent(
            workspace_id=call.workspace_id,
            actor_id=call.created_by_id,
            event_type=f"call.{target.value}",
            resource_type="call",
            resource_id=call.id,
            details=details,
        )
    )
    db.commit()
    db.refresh(call)
    return call
