from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import AuditEvent, Confirmation, ConfirmationStatus, Enquiry, WorkspaceCounter
from app.schemas import EnquiryCreate


CONFIRMATION_TTL_SECONDS = 5 * 60


def canonical_payload(payload: EnquiryCreate) -> dict:
    return payload.model_dump(mode="json")


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def prepare_confirmation(
    db: Session,
    principal: Principal,
    session_id: str,
    payload: EnquiryCreate,
) -> Confirmation:
    data = canonical_payload(payload)
    digest = payload_hash(data)
    existing = db.scalar(
        select(Confirmation).where(
            Confirmation.workspace_id == principal.workspace_id,
            Confirmation.user_id == principal.user_id,
            Confirmation.session_id == session_id,
            Confirmation.payload_hash == digest,
            Confirmation.status == ConfirmationStatus.PENDING,
        )
    )
    now = datetime.now(timezone.utc)
    if existing is not None and _as_utc(existing.expires_at) > now:
        return existing
    confirmation = Confirmation(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        session_id=session_id,
        action="create_enquiry",
        payload_hash=digest,
        payload=data,
        status=ConfirmationStatus.PENDING,
        idempotency_key=f"confirm:{session_id}:{digest}:{now.timestamp()}",
        expires_at=now + timedelta(seconds=CONFIRMATION_TTL_SECONDS),
    )
    db.add(confirmation)
    db.commit()
    db.refresh(confirmation)
    return confirmation


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def confirm_confirmation(db: Session, principal: Principal, confirmation_id: str) -> Confirmation | None:
    confirmation = db.scalar(
        select(Confirmation).where(
            Confirmation.id == confirmation_id,
            Confirmation.workspace_id == principal.workspace_id,
            Confirmation.user_id == principal.user_id,
        )
    )
    if confirmation is None or confirmation.status != ConfirmationStatus.PENDING:
        return None
    now = datetime.now(timezone.utc)
    if _as_utc(confirmation.expires_at) <= now:
        confirmation.status = ConfirmationStatus.EXPIRED
        db.commit()
        return None
    confirmation.status = ConfirmationStatus.CONFIRMED
    confirmation.confirmed_at = now
    db.commit()
    return confirmation


def cancel_confirmation(db: Session, principal: Principal, confirmation_id: str) -> bool:
    confirmation = db.scalar(
        select(Confirmation).where(
            Confirmation.id == confirmation_id,
            Confirmation.workspace_id == principal.workspace_id,
            Confirmation.user_id == principal.user_id,
            Confirmation.status.in_([ConfirmationStatus.PENDING, ConfirmationStatus.CONFIRMED]),
        )
    )
    if confirmation is None:
        return False
    confirmation.status = ConfirmationStatus.CANCELLED
    db.commit()
    return True


def consume_confirmation(
    db: Session,
    principal: Principal,
    confirmation_id: str,
    payload: EnquiryCreate,
) -> tuple[Enquiry | None, str]:
    data = canonical_payload(payload)
    digest = payload_hash(data)
    confirmation = db.scalar(
        select(Confirmation).where(
            Confirmation.id == confirmation_id,
            Confirmation.workspace_id == principal.workspace_id,
            Confirmation.user_id == principal.user_id,
        ).with_for_update()
    )
    if confirmation is None:
        return None, "confirmation_not_found"
    existing = db.scalar(
        select(Enquiry).where(
            Enquiry.workspace_id == principal.workspace_id,
            Enquiry.confirmation_id == confirmation.id,
        )
    )
    if existing is not None:
        return existing, "idempotent_replay"
    now = datetime.now(timezone.utc)
    if confirmation.payload_hash != digest or confirmation.payload != data:
        return None, "confirmation_payload_mismatch"
    if confirmation.status != ConfirmationStatus.CONFIRMED:
        return None, "confirmation_required"
    if _as_utc(confirmation.expires_at) <= now:
        confirmation.status = ConfirmationStatus.EXPIRED
        db.commit()
        return None, "confirmation_expired"

    counter = db.scalar(select(WorkspaceCounter).where(WorkspaceCounter.workspace_id == principal.workspace_id).with_for_update())
    if counter is None:
        raise RuntimeError("Workspace counter is not initialized")
    next_number = counter.enquiry_next
    counter.enquiry_next += 1
    enquiry = Enquiry(
        workspace_id=principal.workspace_id,
        buyer_id=principal.user_id,
        display_id=f"ENQ-{next_number}",
        product=payload.product,
        quantity=payload.quantity,
        unit=payload.unit,
        destination=payload.destination,
        status="Newly created",
        distributor_id=payload.distributor_id,
        confirmation_id=confirmation.id,
        idempotency_key=confirmation.idempotency_key,
    )
    confirmation.status = ConfirmationStatus.CONSUMED
    confirmation.consumed_at = now
    db.add(enquiry)
    try:
        db.flush()
        db.add(AuditEvent(workspace_id=principal.workspace_id, actor_id=principal.user_id, event_type="enquiry.created", resource_type="enquiry", resource_id=enquiry.id, details={"display_id": enquiry.display_id, "confirmation_id": confirmation.id}))
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = db.scalar(select(Enquiry).where(Enquiry.workspace_id == principal.workspace_id, Enquiry.confirmation_id == confirmation.id))
        if replay is not None:
            return replay, "idempotent_replay"
        raise
    db.refresh(enquiry)
    return enquiry, "created"
