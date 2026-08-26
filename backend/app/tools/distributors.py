from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Distributor
from app.tools.registry import registry


def _context(context: dict[str, Any] | None) -> tuple[Session | None, str | None]:
    return ((context or {}).get("db"), (context or {}).get("workspace_id"))


def _dump(distributor: Distributor) -> dict:
    return {
        "id": distributor.id,
        "external_id": distributor.external_id,
        "name": distributor.name,
        "location": distributor.location,
        "categories": distributor.categories,
        "status": distributor.status,
    }


@registry.register(
    "get_distributor",
    {
        "name": "get_distributor",
        "description": "Returns authoritative workspace-scoped information about a distributor.",
        "parameters": {"type": "object", "properties": {"distributor_id": {"type": "string"}}},
    },
)
def get_distributor(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    db, workspace_id = _context(context)
    if db is None or workspace_id is None:
        return {"status": "error", "message": "Trusted workspace context is required."}
    distributor_id = args.get("distributor_id") or (context or {}).get("selected_distributor_id")
    distributor = db.scalar(select(Distributor).where(Distributor.id == distributor_id, Distributor.workspace_id == workspace_id))
    if distributor is None:
        return {"status": "not_found", "message": "Distributor was not found in this workspace."}
    return {"status": "success", "distributor": _dump(distributor)}


@registry.register(
    "search_distributors",
    {
        "name": "search_distributors",
        "description": "Search workspace distributors by product/category and location.",
        "parameters": {"type": "object", "properties": {"product": {"type": "string"}, "location": {"type": "string"}}},
    },
)
def search_distributors(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    db, workspace_id = _context(context)
    if db is None or workspace_id is None:
        return {"status": "error", "message": "Trusted workspace context is required."}
    product = str(args.get("product", "")).strip().lower()[:160]
    location = str(args.get("location", "")).strip().lower()[:160]
    distributors = db.scalars(select(Distributor).where(Distributor.workspace_id == workspace_id).order_by(Distributor.name).limit(100)).all()
    matches = []
    for distributor in distributors:
        location_match = not location or location in distributor.location.lower()
        product_match = not product or any(product in category.lower() or category.lower() in product for category in distributor.categories)
        if location_match and product_match:
            matches.append(_dump(distributor))
    return {"status": "success", "results": matches[:20]}
