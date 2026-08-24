from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import AuditEvent, SupportRequest, WorkspaceCounter
from app.tools.registry import registry


@registry.register("request_human_support", {
    "name": "request_human_support",
    "description": "Creates a workspace-scoped human-support request.",
    "parameters": {"type": "OBJECT", "properties": {"reason": {"type": "STRING"}}, "required": ["reason"]},
})
def request_human_support(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    db: Session | None = (context or {}).get("db")
    principal: Principal | None = (context or {}).get("principal")
    reason = str(args.get("reason", "")).strip()
    if db is None or principal is None:
        return {"status": "error", "message": "Trusted identity context is required."}
    if not reason or len(reason) > 1000:
        return {"status": "invalid_request", "message": "A support reason between 1 and 1000 characters is required."}
    counter = db.scalar(select(WorkspaceCounter).where(WorkspaceCounter.workspace_id == principal.workspace_id).with_for_update())
    if counter is None:
        return {"status": "error", "message": "Workspace counter is not initialized."}
    display_id = f"SUP-{counter.support_next}"
    counter.support_next += 1
    request = SupportRequest(workspace_id=principal.workspace_id, user_id=principal.user_id, display_id=display_id, reason=reason, status="pending")
    db.add(request)
    db.flush()
    db.add(AuditEvent(workspace_id=principal.workspace_id, actor_id=principal.user_id, event_type="support.requested", resource_type="support_request", resource_id=request.id, details={"display_id": request.display_id}))
    db.commit()
    return {"status": "success", "message": "Human support request recorded.", "support_id": request.display_id}
