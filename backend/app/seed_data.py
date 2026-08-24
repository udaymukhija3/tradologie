from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.models import Confirmation, ConfirmationStatus, Distributor, Enquiry, User, UserRole, VoiceAgent, Workspace, WorkspaceCounter


DEMO_WORKSPACE_ID = "workspace_demo"
SECOND_WORKSPACE_ID = "workspace_other"
DEMO_ADMIN_ID = "user_demo_admin"
DEMO_AGENT_ID = "agent_demo"
DEMO_PASSWORD = "TradeVoice123!"


SEED_DISTRIBUTORS = [
    ("dist_001", "Eastern Grain Trading", "West Bengal", ["Rice", "Pulses", "Flour"], "Verified"),
    ("dist_002", "Punjab Agro Exports", "Punjab", ["Basmati Rice", "Wheat", "Pulses"], "Verified"),
    ("dist_003", "Western Foods Supply", "Gujarat", ["Spices", "Oils", "Processed Food"], "Verified"),
    ("dist_004", "Southern Spice Masters", "Kerala", ["Spices", "Tea", "Coffee"], "Pending"),
    ("dist_005", "Central Agri Connect", "Madhya Pradesh", ["Wheat", "Soybean", "Pulses"], "Verified"),
]


def seed_database(db: Session) -> None:
    if db.scalar(select(Workspace.id).limit(1)) is not None:
        return
    demo = Workspace(id=DEMO_WORKSPACE_ID, name="Horizon Imports")
    other = Workspace(id=SECOND_WORKSPACE_ID, name="Other Workspace")
    admin = User(id=DEMO_ADMIN_ID, workspace_id=DEMO_WORKSPACE_ID, email="arjun@horizon.example", name="Arjun Mehta", password_hash=hash_password(DEMO_PASSWORD), role=UserRole.ADMIN)
    other_user = User(id="user_other_admin", workspace_id=SECOND_WORKSPACE_ID, email="admin@other.example", name="Other Admin", password_hash=hash_password(DEMO_PASSWORD), role=UserRole.ADMIN)
    db.add_all([demo, other, admin, other_user])
    db.flush()
    db.add_all([
        WorkspaceCounter(workspace_id=DEMO_WORKSPACE_ID, enquiry_next=1004, support_next=1001),
        WorkspaceCounter(workspace_id=SECOND_WORKSPACE_ID, enquiry_next=1001, support_next=1001),
    ])
    for external_id, name, location, categories, status in SEED_DISTRIBUTORS:
        db.add(Distributor(id=external_id, workspace_id=DEMO_WORKSPACE_ID, external_id=external_id, name=name, location=location, categories=categories, status=status))
    db.add(Distributor(id="dist_other", workspace_id=SECOND_WORKSPACE_ID, external_id="dist_other", name="Private Other Distributor", location="Delhi", categories=["Private"], status="Verified"))
    db.add(VoiceAgent(id=DEMO_AGENT_ID, workspace_id=DEMO_WORKSPACE_ID, name="Trade Support Agent", provider="openai", voice="marin", system_prompt="Use workspace tools for business truth and require confirmation before writes."))
    db.flush()
    seeded = [
        ("ENQ-1001", "Refined Sugar", 25, "tonnes", "Dubai", "Awaiting supplier response"),
        ("ENQ-1002", "Cardamom", 500, "kg", "UAE", "Quote received"),
        ("ENQ-1003", "Wheat Flour", 10, "tonnes", "Oman", "Closed"),
    ]
    for index, (display_id, product, quantity, unit, destination, status) in enumerate(seeded):
        confirmation = Confirmation(id=f"seed_confirm_{index + 1}", workspace_id=DEMO_WORKSPACE_ID, user_id=DEMO_ADMIN_ID, session_id="seed", action="create_enquiry", payload_hash=f"seed-{index + 1}", payload={"product": product, "quantity": quantity, "unit": unit, "destination": destination}, status=ConfirmationStatus.CONSUMED, idempotency_key=f"seed-enquiry-{index + 1}", expires_at=datetime.now(timezone.utc) + timedelta(days=3650), confirmed_at=datetime.now(timezone.utc), consumed_at=datetime.now(timezone.utc))
        db.add(confirmation)
        db.flush()
        db.add(Enquiry(id=f"seed_enquiry_{index + 1}", workspace_id=DEMO_WORKSPACE_ID, buyer_id=DEMO_ADMIN_ID, display_id=display_id, product=product, quantity=quantity, unit=unit, destination=destination, status=status, confirmation_id=confirmation.id, idempotency_key=confirmation.idempotency_key))
    db.commit()
