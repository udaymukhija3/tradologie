from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.auth import Principal
from app.database import SessionLocal
from app.models import UserRole
from app.schemas import EnquiryCreate
from app.services.enquiries import confirm_confirmation, consume_confirmation, prepare_confirmation
from app.tools.registry import registry

PRINCIPAL = Principal("user_demo_admin", "workspace_demo", "arjun@horizon.example", "Arjun Mehta", UserRole.ADMIN)


def context(db):
    return {"db": db, "principal": PRINCIPAL, "workspace_id": PRINCIPAL.workspace_id, "selected_distributor_id": "dist_001"}


def test_workspace_scoped_distributor_tools():
    with SessionLocal() as db:
        current = registry.get_tool("get_distributor")({}, context(db))
        assert current["distributor"]["name"] == "Eastern Grain Trading"
        search = registry.get_tool("search_distributors")({"product": "Basmati Rice", "location": "Punjab"}, context(db))
        assert [row["name"] for row in search["results"]] == ["Punjab Agro Exports"]
        forbidden = registry.get_tool("get_distributor")({"distributor_id": "dist_other"}, context(db))
        assert forbidden["status"] == "not_found"


def test_enquiry_requires_fresh_exact_confirmation_and_consumes_it():
    args = {"product": "Coffee", "quantity": 100, "unit": "kg", "destination": "USA", "distributor_id": None}
    with SessionLocal() as db:
        tool = registry.get_tool("create_enquiry")
        assert tool(args, context(db))["status"] == "confirmation_required"
        confirmation = prepare_confirmation(db, PRINCIPAL, "session-1", EnquiryCreate.model_validate(args))
        assert confirm_confirmation(db, PRINCIPAL, confirmation.id) is not None
        trusted = {**context(db), "confirmation_id": confirmation.id}
        created = tool(args, trusted)
        assert created["status"] == "success"
        assert created["enquiry_id"] == "ENQ-1004"
        replay = tool(args, trusted)
        assert replay["enquiry_id"] == "ENQ-1004"
        assert replay["idempotent_replay"] is True
        fresh = prepare_confirmation(db, PRINCIPAL, "session-1", EnquiryCreate.model_validate(args))
        assert fresh.id != confirmation.id
        assert fresh.status.value == "pending"


def test_fractional_boolean_and_unknown_units_are_rejected():
    tool = registry.get_tool("create_enquiry")
    with SessionLocal() as db:
        base = {**context(db), "confirmation_id": "not-real"}
        for quantity in (1.9, True, "2"):
            result = tool({"product": "Rice", "quantity": quantity, "unit": "kg", "destination": "Dubai"}, base)
            assert result["status"] == "invalid_request"
        result = tool({"product": "Rice", "quantity": 2, "unit": "truckloads", "destination": "Dubai"}, base)
        assert result["status"] == "invalid_request"


def test_database_confirmation_consumption_is_concurrency_safe():
    payload = EnquiryCreate(product="Coffee", quantity=100, unit="kg", destination="USA")
    with SessionLocal() as db:
        confirmation = prepare_confirmation(db, PRINCIPAL, "database-race", payload)
        assert confirm_confirmation(db, PRINCIPAL, confirmation.id) is not None
        confirmation_id = confirmation.id

    def consume_once(_: int) -> tuple[str, str]:
        with SessionLocal() as db:
            enquiry, status = consume_confirmation(db, PRINCIPAL, confirmation_id, payload)
            assert enquiry is not None
            return enquiry.display_id, status

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(consume_once, range(10)))

    assert {display_id for display_id, _ in results} == {"ENQ-1004"}
    assert [status for _, status in results].count("created") == 1
    assert [status for _, status in results].count("idempotent_replay") == 9


def test_support_requires_identity_and_bounded_reason():
    tool = registry.get_tool("request_human_support")
    assert tool({"reason": "help"})["status"] == "error"
    with SessionLocal() as db:
        assert tool({"reason": "  "}, context(db))["status"] == "invalid_request"
        assert tool({"reason": "I need a trade specialist"}, context(db))["support_id"] == "SUP-1001"
