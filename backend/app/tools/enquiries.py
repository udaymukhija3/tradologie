from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import Enquiry
from app.schemas import EnquiryCreate
from app.services.enquiries import consume_confirmation
from app.tools.registry import registry


def _dump(enquiry: Enquiry) -> dict:
    return {"id": enquiry.display_id, "record_id": enquiry.id, "buyer_id": enquiry.buyer_id, "product": enquiry.product, "quantity": enquiry.quantity, "unit": enquiry.unit, "destination": enquiry.destination, "status": enquiry.status, "distributor_id": enquiry.distributor_id}


def _trusted(context: dict[str, Any] | None) -> tuple[Session | None, Principal | None]:
    return ((context or {}).get("db"), (context or {}).get("principal"))


@registry.register("get_enquiry_status", {
    "name": "get_enquiry_status",
    "description": "Returns an enquiry status within the authenticated workspace.",
    "parameters": {"type": "OBJECT", "properties": {"enquiry_id": {"type": "STRING"}}, "required": ["enquiry_id"]},
})
def get_enquiry_status(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    db, principal = _trusted(context)
    if db is None or principal is None:
        return {"status": "error", "message": "Trusted identity context is required."}
    display_id = str(args.get("enquiry_id", ""))[:32].upper()
    enquiry = db.scalar(select(Enquiry).where(Enquiry.display_id == display_id, Enquiry.workspace_id == principal.workspace_id))
    if enquiry is None:
        return {"status": "not_found", "message": "Enquiry was not found in this workspace."}
    return {"status": "success", "enquiry": _dump(enquiry)}


@registry.register("create_enquiry", {
    "name": "create_enquiry",
    "description": "Creates an enquiry after a fresh, exact, server-recorded confirmation.",
    "parameters": {"type": "OBJECT", "properties": {"product": {"type": "STRING"}, "quantity": {"type": "INTEGER"}, "unit": {"type": "STRING", "enum": ["kg", "tonnes", "units"]}, "destination": {"type": "STRING"}, "distributor_id": {"type": "STRING"}}, "required": ["product", "quantity", "unit", "destination"]},
})
def create_enquiry(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    db, principal = _trusted(context)
    confirmation_id = (context or {}).get("confirmation_id")
    if db is None or principal is None:
        return {"status": "error", "message": "Trusted identity context is required."}
    if not confirmation_id:
        return {"status": "confirmation_required", "message": "A fresh server-recorded confirmation is required."}
    try:
        payload = EnquiryCreate.model_validate(args)
    except ValidationError as exc:
        return {"status": "invalid_request", "message": "Enquiry details are invalid.", "errors": exc.errors(include_url=False)}
    enquiry, outcome = consume_confirmation(db, principal, confirmation_id, payload)
    if enquiry is None:
        return {"status": outcome, "message": "The exact enquiry details require a fresh confirmation."}
    return {"status": "success", "message": "Enquiry created successfully." if outcome == "created" else "This confirmed request was already processed.", "enquiry_id": enquiry.display_id, "enquiry": _dump(enquiry), "idempotent_replay": outcome == "idempotent_replay"}
