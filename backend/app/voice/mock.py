import json
import re
import time
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.auth import Principal
from app.schemas import EnquiryCreate
from app.services.enquiries import cancel_confirmation, confirm_confirmation, prepare_confirmation
from app.tools import registry


MAX_TEXT_LENGTH = 4_096
MAX_EVENT_LENGTH = 16_384


def _log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def _normalise_unit(unit: str) -> str:
    value = unit.lower()
    if value in {"ton", "tons", "tonne", "tonnes"}:
        return "tonnes"
    if value in {"kilogram", "kilograms", "kg"}:
        return "kg"
    return value


def _title_product(product: str) -> str:
    return " ".join(word.capitalize() for word in product.strip().split())


def _parse_enquiry_request(text: str) -> Optional[Dict[str, Any]]:
    pattern = re.compile(
        r"\b(?:i\s+need|i\s+want|create(?:\s+an)?\s+enquiry\s+for)\s+"
        r"(?P<quantity>\d+)\s+"
        r"(?P<unit>tonnes?|tons?|kg|kilograms?)\s+"
        r"(?:of\s+)?(?P<product>.+?)\s+"
        r"(?:for|to)\s+(?P<destination>[a-z][a-z\s-]*?)[.!?]*$",
        re.IGNORECASE,
    )
    match = pattern.search(text.strip())
    if not match:
        return None

    return {
        "product": _title_product(match.group("product")),
        "quantity": int(match.group("quantity")),
        "unit": _normalise_unit(match.group("unit")),
        "destination": match.group("destination").strip().title(),
        "distributor_id": None,
    }


async def run_mock_session(
    websocket,
    context: Dict[str, Any],
    db: Session,
    principal: Principal,
):
    """Run the deterministic, credential-free local demo engine."""

    session_id = uuid.uuid4().hex
    pending_enquiry: Optional[Dict[str, Any]] = None
    pending_confirmation_id: Optional[str] = None

    await websocket.send_json(
        {"type": "session_started", "session_id": session_id, "engine": "local_demo"}
    )
    _log("session_started", session_id=session_id)

    async def send_response(text: str, request_id: str, started_at: float) -> None:
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        await websocket.send_json(
            {
                "type": "response_started",
                "request_id": request_id,
                "latency_ms": latency_ms,
            }
        )
        await websocket.send_json(
            {"type": "agent_response", "request_id": request_id, "text": text}
        )
        _log(
            "response_completed",
            session_id=session_id,
            request_id=request_id,
            latency_ms=latency_ms,
        )

    async def execute_tool(
        name: str,
        args: Dict[str, Any],
        request_id: str,
        tool_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await websocket.send_json(
            {"type": "tool_requested", "request_id": request_id, "name": name}
        )
        await websocket.send_json(
            {"type": "tool_started", "request_id": request_id, "name": name}
        )

        start = time.perf_counter()
        tool = registry.get_tool(name)
        if tool is None:
            result = {"status": "error", "message": "Tool is not registered."}
        else:
            try:
                trusted_context = {
                    **(tool_context if tool_context is not None else context),
                    "db": db,
                    "principal": principal,
                    "workspace_id": principal.workspace_id,
                }
                result = tool(args, trusted_context)
            except Exception as exc:
                result = {"status": "error", "message": "Tool execution failed."}
                _log(
                    "tool_error",
                    session_id=session_id,
                    request_id=request_id,
                    tool=name,
                    error_type=type(exc).__name__,
                )

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        await websocket.send_json(
            {
                "type": "tool_completed",
                "request_id": request_id,
                "name": name,
                "duration_ms": duration_ms,
                "result": result,
            }
        )
        _log(
            "tool_completed",
            session_id=session_id,
            request_id=request_id,
            tool=name,
            duration_ms=duration_ms,
            status=result.get("status"),
        )
        return result

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            # Production media adapters consume binary audio. The local demo
            # engine ignores it safely so media cannot terminate the session.
            if message.get("bytes") is not None:
                await websocket.send_json(
                    {"type": "media_ignored", "reason": "local_demo_engine"}
                )
                continue

            raw_text = message.get("text")
            if raw_text is None:
                continue
            if len(raw_text) > MAX_EVENT_LENGTH:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Event exceeds {MAX_EVENT_LENGTH} characters.",
                    }
                )
                continue

            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "message": "Message must be valid JSON."}
                )
                continue

            if data.get("type") == "context_update":
                updated_context = data.get("context")
                if not isinstance(updated_context, dict):
                    await websocket.send_json(
                        {"type": "error", "message": "Context must be an object."}
                    )
                    continue

                context["current_page"] = str(updated_context.get("current_page", ""))[:160]
                selected = updated_context.get("selected_distributor_id")
                context["selected_distributor_id"] = str(selected)[:32] if selected else None
                selected_distributor_id = context.get("selected_distributor_id")
                await websocket.send_json(
                    {
                        "type": "context_updated",
                        "selected_distributor_id": selected_distributor_id,
                    }
                )
                _log(
                    "context_updated",
                    session_id=session_id,
                    selected_distributor_id=selected_distributor_id,
                )
                continue

            if data.get("type") != "text":
                continue

            user_text = str(data.get("text", "")).strip()
            if not user_text:
                continue
            if len(user_text) > MAX_TEXT_LENGTH:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Message exceeds {MAX_TEXT_LENGTH} characters.",
                    }
                )
                continue

            started_at = time.perf_counter()
            request_id = uuid.uuid4().hex[:12]
            text = user_text.lower()
            _log(
                "user_message_received",
                session_id=session_id,
                request_id=request_id,
                character_count=len(user_text),
            )

            if pending_enquiry and text in {"confirm", "confirmed", "yes", "yes confirm"}:
                if not pending_confirmation_id or confirm_confirmation(db, principal, pending_confirmation_id) is None:
                    pending_enquiry = None
                    pending_confirmation_id = None
                    await send_response("That confirmation expired. Please state the enquiry again.", request_id, started_at)
                    continue
                result = await execute_tool(
                    "create_enquiry",
                    pending_enquiry,
                    request_id,
                    {**context, "confirmation_id": pending_confirmation_id},
                )
                pending_enquiry = None
                pending_confirmation_id = None
                if result.get("status") == "success":
                    await send_response(
                        f"Your enquiry has been created. The enquiry ID is {result['enquiry_id']}.",
                        request_id,
                        started_at,
                    )
                else:
                    await send_response(
                        result.get("message", "I could not create the enquiry."),
                        request_id,
                        started_at,
                    )
                continue

            if pending_enquiry and text in {"cancel", "cancel it", "no", "no cancel"}:
                if pending_confirmation_id:
                    cancel_confirmation(db, principal, pending_confirmation_id)
                pending_enquiry = None
                pending_confirmation_id = None
                await send_response(
                    "The enquiry was cancelled and nothing was created.",
                    request_id,
                    started_at,
                )
                continue

            if text in {"confirm", "confirmed", "yes", "yes confirm"}:
                await send_response(
                    "There is no pending action to confirm. Tell me what enquiry you want to create first.",
                    request_id,
                    started_at,
                )
                continue

            enquiry_request = _parse_enquiry_request(user_text)
            if enquiry_request:
                try:
                    validated = EnquiryCreate.model_validate(enquiry_request)
                except Exception:
                    await send_response("The enquiry details were invalid.", request_id, started_at)
                    continue
                pending_enquiry = validated.model_dump(mode="json")
                confirmation = prepare_confirmation(db, principal, session_id, validated)
                pending_confirmation_id = confirmation.id
                await websocket.send_json(
                    {
                        "type": "confirmation_required",
                        "request_id": request_id,
                        "action": "create_enquiry",
                        "arguments": enquiry_request,
                    }
                )
                await send_response(
                    "Please confirm: create an enquiry for "
                    f"{enquiry_request['quantity']} {enquiry_request['unit']} of "
                    f"{enquiry_request['product']} for delivery to "
                    f"{enquiry_request['destination']}? Say confirm or cancel.",
                    request_id,
                    started_at,
                )
                continue

            if "this distributor" in text:
                result = await execute_tool("get_distributor", {}, request_id)
                if result.get("status") == "success":
                    distributor = result["distributor"]
                    categories = ", ".join(distributor["categories"])
                    await send_response(
                        f"{distributor['name']} is a {distributor['status'].lower()} distributor "
                        f"in {distributor['location']}. They supply {categories}.",
                        request_id,
                        started_at,
                    )
                else:
                    await send_response(
                        "Select a distributor first, then ask me about this distributor.",
                        request_id,
                        started_at,
                    )
                continue

            if "search" in text or "find" in text or "supplier" in text:
                product = "basmati rice" if "basmati" in text else ""
                location = "punjab" if "punjab" in text else ""
                result = await execute_tool(
                    "search_distributors",
                    {"product": product, "location": location},
                    request_id,
                )
                matches = result.get("results", [])
                if matches:
                    first = matches[0]
                    await send_response(
                        f"I found {first['name']} in {first['location']}. "
                        f"They are {first['status'].lower()} and supply "
                        f"{', '.join(first['categories'])}.",
                        request_id,
                        started_at,
                    )
                else:
                    await send_response(
                        "I could not find a distributor matching those criteria.",
                        request_id,
                        started_at,
                    )
                continue

            enquiry_match = re.search(r"\bENQ-\d+\b", user_text, re.IGNORECASE)
            if enquiry_match:
                enquiry_id = enquiry_match.group(0).upper()
                result = await execute_tool(
                    "get_enquiry_status", {"enquiry_id": enquiry_id}, request_id
                )
                if result.get("status") == "success":
                    enquiry = result["enquiry"]
                    await send_response(
                        f"{enquiry_id} is currently {enquiry['status']}. It is for "
                        f"{enquiry['quantity']} {enquiry['unit']} of {enquiry['product']} "
                        f"to {enquiry['destination']}.",
                        request_id,
                        started_at,
                    )
                else:
                    await send_response(
                        f"I could not find enquiry {enquiry_id}.",
                        request_id,
                        started_at,
                    )
                continue

            if any(word in text for word in ("person", "human", "someone", "agent")):
                result = await execute_tool(
                    "request_human_support", {"reason": user_text}, request_id
                )
                if result.get("status") == "success":
                    await send_response(
                        f"I recorded your request for human support. The reference is {result['support_id']}.",
                        request_id,
                        started_at,
                    )
                else:
                    await send_response(
                        "I could not record the support request.",
                        request_id,
                        started_at,
                    )
                continue

            await send_response(
                "I can search distributors, check an enquiry, create a confirmed enquiry, "
                "or request human support. Please try one of those tasks.",
                request_id,
                started_at,
            )
    finally:
        _log("session_ended", session_id=session_id)
