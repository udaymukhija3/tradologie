"""The credential-free conversational engine.

Dialogue management only: this module owns the socket, the tools, the
confirmation lifecycle and the session's slot-filling state. All language
understanding lives in app.voice.nlu, which is pure and separately tested.

The engine is deterministic by design -- it is the fallback that runs without
an OpenAI project key -- but deterministic is not the same as scripted. It
resolves intent by weighted evidence, extracts entities with real grammars,
asks follow-up questions when a request is under-specified, and answers from
the caller's own workspace data rather than from hardcoded demo nouns.
"""

import json
import time
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import Distributor
from app.schemas import EnquiryCreate
from app.services.enquiries import cancel_confirmation, confirm_confirmation, prepare_confirmation
from app.tools import registry
from app.voice import nlu
from app.voice.nlu import Intent


MAX_TEXT_LENGTH = 4_096
MAX_EVENT_LENGTH = 16_384

# Below this, the winning intent is not distinct enough to act on. Asking beats
# guessing: running a distributor search for "I can't find my invoice" is worse
# than a clarifying question.
MIN_ACTIONABLE_CONFIDENCE = 0.4

CAPABILITIES = (
    "I can search distributors, describe the one you have selected, check an "
    "enquiry by its ID, raise a new enquiry once you confirm it, or put you "
    "through to a person."
)


def _log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def _workspace_vocabulary(db: Session, workspace_id: str) -> tuple[list[str], list[str]]:
    """Product and location vocabulary drawn from the tenant's own catalogue."""
    rows = db.scalars(
        select(Distributor).where(Distributor.workspace_id == workspace_id).limit(200)
    ).all()
    products: set[str] = set()
    locations: set[str] = set()
    for row in rows:
        if row.location:
            locations.add(row.location)
        for category in row.categories or []:
            products.add(category)
    return sorted(products), sorted(locations)


def _describe(draft: nlu.EnquiryDraft) -> str:
    return f"{draft.quantity} {draft.unit} of {draft.product} for delivery to {draft.destination}"


async def run_mock_session(
    websocket,
    context: Dict[str, Any],
    db: Session,
    principal: Principal,
):
    """Run the deterministic, credential-free conversational engine."""

    session_id = uuid.uuid4().hex
    pending_enquiry: Optional[Dict[str, Any]] = None
    pending_confirmation_id: Optional[str] = None
    slots: Dict[str, Any] = {}

    products, locations = _workspace_vocabulary(db, principal.workspace_id)

    await websocket.send_json(
        {"type": "session_started", "session_id": session_id, "engine": "local_demo"}
    )
    _log("session_started", session_id=session_id, vocabulary_products=len(products))

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

    async def propose_enquiry(draft: nlu.EnquiryDraft, request_id: str, started_at: float):
        """Stage an exact, confirmable action. Nothing is written yet."""
        arguments = draft.as_tool_arguments()
        try:
            validated = EnquiryCreate.model_validate(arguments)
        except Exception:
            await send_response(
                f"I could not use those details. Quantities must be a whole number "
                f"above zero, and I support kg, tonnes and units. {CAPABILITIES}",
                request_id,
                started_at,
            )
            return None, None

        confirmation = prepare_confirmation(db, principal, session_id, validated)
        await websocket.send_json(
            {
                "type": "confirmation_required",
                "request_id": request_id,
                "action": "create_enquiry",
                "arguments": arguments,
            }
        )
        await send_response(
            f"Please confirm: create an enquiry for {_describe(draft)}? "
            "Say confirm or cancel.",
            request_id,
            started_at,
        )
        return validated.model_dump(mode="json"), confirmation.id

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

            classification = nlu.classify(
                user_text,
                has_pending_action=pending_enquiry is not None,
                has_selection=bool(context.get("selected_distributor_id")),
            )
            intent = classification.intent
            _log(
                "user_message_received",
                session_id=session_id,
                request_id=request_id,
                character_count=len(user_text),
                intent=intent.value,
                confidence=classification.confidence,
            )

            # ---------- confirmation lifecycle ----------

            if pending_enquiry is not None and intent is Intent.CONFIRM:
                if not pending_confirmation_id or confirm_confirmation(db, principal, pending_confirmation_id) is None:
                    pending_enquiry = None
                    pending_confirmation_id = None
                    await send_response(
                        "That confirmation expired. Please state the enquiry again.",
                        request_id,
                        started_at,
                    )
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

            if pending_enquiry is not None and intent is Intent.CANCEL:
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

            if intent is Intent.CONFIRM:
                await send_response(
                    "There is no pending action to confirm. Tell me what enquiry "
                    "you want to create first.",
                    request_id,
                    started_at,
                )
                continue

            if intent is Intent.CANCEL and slots:
                slots = {}
                await send_response(
                    "No problem, I have dropped that request.", request_id, started_at
                )
                continue

            # ---------- multi-turn slot filling ----------
            # An under-specified enquiry parks its known slots and asks for the
            # rest, so "I need 50 tonnes of rice" / "Dubai" completes over two
            # turns instead of dead-ending.

            if slots:
                awaiting_destination = "destination" in slots["missing"]
                restated = nlu.extract_enquiry(user_text)
                draft = None

                if restated.complete:
                    # The caller restated the whole request rather than
                    # answering the question. Prefer what they just said.
                    draft = restated.draft
                elif awaiting_destination and intent in {Intent.UNKNOWN, Intent.CREATE_ENQUIRY}:
                    place = nlu.tokenise(user_text)[:4]
                    if place:
                        draft = nlu.EnquiryDraft(
                            product=str(slots["product"]),
                            quantity=int(slots["quantity"]),
                            unit=str(slots["unit"]),
                            destination=nlu.titlecase(place),
                        )

                slots = {}
                if draft is not None:
                    pending_enquiry, pending_confirmation_id = await propose_enquiry(
                        draft, request_id, started_at
                    )
                    continue

            # ---------- intents ----------

            if intent is Intent.CREATE_ENQUIRY:
                extraction = nlu.extract_enquiry(user_text)

                if extraction.complete:
                    pending_enquiry, pending_confirmation_id = await propose_enquiry(
                        extraction.draft, request_id, started_at
                    )
                    continue

                if extraction.unsupported_unit is not None:
                    await send_response(
                        f"I cannot raise an enquiry in {extraction.unsupported_unit}. "
                        "Tell me the quantity in kg, tonnes or units and I will "
                        "read the enquiry back to you.",
                        request_id,
                        started_at,
                    )
                    continue

                if "destination" in extraction.missing and "product" not in extraction.missing:
                    slots = {**extraction.partial, "missing": extraction.missing}
                    await send_response(
                        f"I have {extraction.partial['quantity']} "
                        f"{extraction.partial['unit']} of "
                        f"{extraction.partial['product']}. Where should it be delivered?",
                        request_id,
                        started_at,
                    )
                    continue

                if "quantity" in extraction.missing or "unit" in extraction.missing:
                    await send_response(
                        "How much do you need, and in what unit? I work in kg, "
                        "tonnes and units.",
                        request_id,
                        started_at,
                    )
                    continue

                await send_response(
                    "What product should the enquiry be for?", request_id, started_at
                )
                continue

            if intent is Intent.DESCRIBE_SELECTED:
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

            if intent is Intent.SEARCH_DISTRIBUTORS and classification.confidence >= MIN_ACTIONABLE_CONFIDENCE:
                found = nlu.extract_search_slots(
                    user_text, products=products, locations=locations
                )
                result = await execute_tool(
                    "search_distributors",
                    {"product": found.product, "location": found.location},
                    request_id,
                )
                matches = result.get("results", [])
                if not matches:
                    criteria = " and ".join(
                        part for part in (found.product, found.location) if part
                    )
                    await send_response(
                        f"I could not find a distributor for {criteria}."
                        if criteria
                        else "I could not find a distributor matching those criteria.",
                        request_id,
                        started_at,
                    )
                    continue

                first = matches[0]
                lead = (
                    f"{first['name']} in {first['location']} is {first['status'].lower()} "
                    f"and supplies {', '.join(first['categories'])}."
                )
                if len(matches) > 1:
                    others = ", ".join(row["name"] for row in matches[1:3])
                    lead += f" I also found {others}."
                await send_response(lead, request_id, started_at)
                continue

            if intent is Intent.CHECK_ENQUIRY and classification.enquiry_id:
                result = await execute_tool(
                    "get_enquiry_status",
                    {"enquiry_id": classification.enquiry_id},
                    request_id,
                )
                if result.get("status") == "success":
                    enquiry = result["enquiry"]
                    await send_response(
                        f"{classification.enquiry_id} is currently {enquiry['status']}. "
                        f"It is for {enquiry['quantity']} {enquiry['unit']} of "
                        f"{enquiry['product']} to {enquiry['destination']}.",
                        request_id,
                        started_at,
                    )
                else:
                    await send_response(
                        f"I could not find enquiry {classification.enquiry_id}.",
                        request_id,
                        started_at,
                    )
                continue

            if intent is Intent.CHECK_ENQUIRY:
                await send_response(
                    "Which enquiry? Give me the reference, for example ENQ-1001.",
                    request_id,
                    started_at,
                )
                continue

            if intent is Intent.HUMAN_SUPPORT:
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
                        "I could not record the support request.", request_id, started_at
                    )
                continue

            if intent is Intent.GREETING:
                await send_response(
                    f"Hello. {CAPABILITIES}", request_id, started_at
                )
                continue

            await send_response(
                f"I did not catch what you need there. {CAPABILITIES}",
                request_id,
                started_at,
            )
    finally:
        _log("session_ended", session_id=session_id)
