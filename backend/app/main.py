from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import Principal, create_access_token, current_principal, principal_from_token, require_roles, verify_password
from app.config import get_settings, validate_runtime_settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import Call, CallStatus, Distributor, Enquiry, SupportRequest, User, UserRole, VoiceAgent
from app.observability import configure_logging, observe_request, render_metrics
from app.providers.twilio import TwilioProvider
from app.rate_limit import limiter
from app.schemas import CallResponse, DashboardResponse, DistributorResponse, EnquiryResponse, LoginRequest, SimulatedCallRequest, TokenResponse, VoiceAgentResponse
from app.seed_data import seed_database
from app.services.calls import advance_call, create_simulated_call
from app.services.summaries import enqueue_summary, queue_stats
from app.voice.mock import run_mock_session
from app.voice.openai_realtime import DEFAULT_REALTIME_MODEL, RealtimeConfigurationError, RealtimeUpstreamError, create_openai_realtime_call, execute_realtime_tool, observe_user_transcript, session_store, update_session_context


load_dotenv()
configure_logging()
logger = logging.getLogger("tradevoice.api")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_runtime_settings(settings)
    if os.getenv("AUTO_CREATE_SCHEMA", "true").lower() == "true":
        Base.metadata.create_all(engine)
    if os.getenv("AUTO_SEED", "true").lower() == "true":
        with SessionLocal() as db:
            seed_database(db)
    yield


app = FastAPI(title="TradeVoice Platform", version="1.0.0", lifespan=lifespan)
ALLOWED_ORIGINS = set(settings.allowed_origins)
app.add_middleware(CORSMiddleware, allow_origins=sorted(ALLOWED_ORIGINS), allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Twilio-Signature", "X-Request-ID"])


class RealtimeCallRequest(BaseModel):
    sdp: str = Field(min_length=1, max_length=131_072)
    context: dict[str, Any]


class RealtimeToolRequest(BaseModel):
    call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any]


class RealtimeContextRequest(BaseModel):
    context: dict[str, Any]


class RealtimeEventRequest(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    transcript: str = Field(default="", max_length=4_096)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:12])[:64]
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_seconds = time.perf_counter() - started_at
        route = getattr(request.scope.get("route"), "path", "unmatched")
        observe_request(request.method, route, 500, duration_seconds)
        logger.exception(
            "http_request_failed",
            extra={"request_id": request_id, "method": request.method, "route": route, "duration_ms": round(duration_seconds * 1000, 2), "error_type": type(exc).__name__},
        )
        raise
    duration_seconds = time.perf_counter() - started_at
    duration_ms = round(duration_seconds * 1000, 2)
    route = getattr(request.scope.get("route"), "path", "unmatched")
    observe_request(request.method, route, response.status_code, duration_seconds)
    response.headers["x-request-id"] = request_id
    response.headers["server-timing"] = f"app;dur={duration_ms}"
    response.headers["cache-control"] = response.headers.get("cache-control", "no-store")
    logger.info(
        "http_request_completed",
        extra={"request_id": request_id, "method": request.method, "route": route, "status_code": response.status_code, "duration_ms": duration_ms},
    )
    return response


def _require_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Origin is not allowed")


def _bounded_context(context: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(context, default=str).encode()) > 16_384:
        raise HTTPException(status_code=413, detail="Application context is too large")
    selected = context.get("selected_distributor_id")
    return {"current_page": str(context.get("current_page", ""))[:160], "selected_distributor_id": str(selected)[:32] if selected else None}


def _session_context(principal: Principal, page_context: dict[str, Any]) -> dict[str, Any]:
    return {**_bounded_context(page_context), "buyer": {"id": principal.user_id, "name": principal.name}, "user_id": principal.user_id, "workspace_id": principal.workspace_id, "role": principal.role.value}


def _require_owned_session(session_id: str, principal: Principal):
    session = session_store.get(session_id)
    if session is None or session.context.get("user_id") != principal.user_id or session.context.get("workspace_id") != principal.workspace_id:
        raise HTTPException(status_code=404, detail="Realtime session was not found")
    return session


@app.get("/api/health")
@app.get("/api/health/live")
def get_health():
    return {"status": "ok", "service": "tradevoice-backend", "voice_mode": settings.voice_mode}


@app.get("/api/health/ready")
def get_readiness(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is unavailable") from exc
    redis_status = "ok"
    worker_status = "ok"
    depth: int | None = None
    try:
        redis_client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)
        redis_client.ping()
        stats = queue_stats(redis_client)
        depth = int(stats["waiting"])
        if not stats["worker_available"]:
            worker_status = "unavailable"
    except Exception:
        redis_status = "degraded"
        worker_status = "synchronous_fallback"
    if redis_status == "ok" and worker_status != "ok":
        raise HTTPException(status_code=503, detail="Summary worker is unavailable")
    return {
        "status": "ready",
        "database": "ok",
        "redis": redis_status,
        "summary_worker": worker_status,
        "summary_queue_depth": depth,
        "summary_processing_depth": int(stats["processing"]) if redis_status == "ok" else None,
        "summary_dead_letter_depth": int(stats["dead_letter"]) if redis_status == "ok" else None,
    }


@app.get("/internal/metrics", include_in_schema=False)
def metrics():
    content, media_type = render_metrics()
    return Response(content=content, media_type=media_type)


@app.get("/api/runtime")
def get_runtime():
    if settings.voice_mode == "openai":
        return {"voice_mode": "openai", "engine_label": "OpenAI Realtime", "speech_transport": "webrtc", "model": os.getenv("OPENAI_REALTIME_MODEL", DEFAULT_REALTIME_MODEL), "configured": bool(os.getenv("OPENAI_API_KEY", "").strip())}
    return {"voice_mode": "mock", "engine_label": "Local Demo Engine", "speech_transport": "browser_speech"}


@app.post("/api/auth/token", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"login:{request.client.host if request.client else 'unknown'}", limit=10, window_seconds=60)
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.lower(), User.is_active.is_(True)))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user), expires_in=settings.access_token_minutes * 60, user={"id": user.id, "workspace_id": user.workspace_id, "workspace_name": user.workspace.name, "email": user.email, "name": user.name, "role": user.role.value})


@app.get("/api/auth/me")
def me(principal: Principal = Depends(current_principal)):
    return principal.__dict__


@app.get("/api/distributors", response_model=list[DistributorResponse])
def get_distributors(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.scalars(select(Distributor).where(Distributor.workspace_id == principal.workspace_id).order_by(Distributor.name).limit(100)).all()


@app.get("/api/enquiries", response_model=list[EnquiryResponse])
def get_enquiries(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.scalars(select(Enquiry).where(Enquiry.workspace_id == principal.workspace_id).order_by(Enquiry.created_at.desc()).limit(100)).all()


@app.get("/api/support-requests")
def get_support_requests(principal: Principal = Depends(require_roles(UserRole.ADMIN, UserRole.AGENT)), db: Session = Depends(get_db)):
    rows = db.scalars(select(SupportRequest).where(SupportRequest.workspace_id == principal.workspace_id).order_by(SupportRequest.created_at.desc()).limit(100)).all()
    return [{"id": row.display_id, "user_id": row.user_id, "reason": row.reason, "status": row.status, "created_at": row.created_at} for row in rows]


@app.get("/api/agents", response_model=list[VoiceAgentResponse])
def get_agents(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.scalars(select(VoiceAgent).where(VoiceAgent.workspace_id == principal.workspace_id).order_by(VoiceAgent.created_at.desc())).all()


@app.get("/api/calls", response_model=list[CallResponse])
def get_calls(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.scalars(select(Call).where(Call.workspace_id == principal.workspace_id).order_by(Call.created_at.desc()).limit(100)).all()


@app.get("/api/dashboard", response_model=DashboardResponse)
def dashboard(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    agents = db.scalars(select(VoiceAgent).where(VoiceAgent.workspace_id == principal.workspace_id).order_by(VoiceAgent.name)).all()
    calls = db.scalars(select(Call).where(Call.workspace_id == principal.workspace_id).order_by(Call.created_at.desc()).limit(20)).all()
    enquiries = db.scalars(select(Enquiry).where(Enquiry.workspace_id == principal.workspace_id).order_by(Enquiry.created_at.desc()).limit(20)).all()
    return {"counts": {"agents": len(agents), "calls": db.scalar(select(func.count()).select_from(Call).where(Call.workspace_id == principal.workspace_id)) or 0, "active_calls": db.scalar(select(func.count()).select_from(Call).where(Call.workspace_id == principal.workspace_id, Call.status == CallStatus.ACTIVE)) or 0, "enquiries": db.scalar(select(func.count()).select_from(Enquiry).where(Enquiry.workspace_id == principal.workspace_id)) or 0}, "agents": agents, "calls": calls, "enquiries": enquiries}


@app.post("/api/telephony/simulate", response_model=CallResponse, status_code=201)
def simulate_call(payload: SimulatedCallRequest, principal: Principal = Depends(require_roles(UserRole.ADMIN, UserRole.AGENT)), db: Session = Depends(get_db)):
    limiter.check(f"simulate:{principal.workspace_id}", limit=30, window_seconds=60)
    try:
        call, replay = create_simulated_call(db, principal, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not replay:
        call = advance_call(db, call, CallStatus.ACTIVE)
        call = advance_call(db, call, CallStatus.COMPLETED, transcript=payload.transcript, outcome=payload.outcome)
        enqueue_summary(call.id)
        db.refresh(call)
    return Response(content=CallResponse.model_validate(call).model_dump_json(), media_type="application/json", status_code=200 if replay else 201, headers={"Idempotent-Replay": str(replay).lower()})


@app.post("/api/telephony/twilio/events")
async def twilio_event(request: Request, db: Session = Depends(get_db)):
    if not settings.twilio_auth_token:
        raise HTTPException(status_code=503, detail="Telephony provider is not configured")
    parameters = {key: str(value) for key, value in (await request.form()).items()}
    provider = TwilioProvider(settings.twilio_auth_token)
    signature = request.headers.get("x-twilio-signature", "")
    url = f"{settings.public_base_url}/api/telephony/twilio/events"
    if not provider.verify_signature(url, parameters, signature):
        raise HTTPException(status_code=403, detail="Invalid telephony provider signature")
    try:
        event = provider.parse_event(parameters)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    call = db.scalar(select(Call).where(Call.provider == "twilio", Call.provider_call_id == event.provider_call_id))
    if call is None:
        workspace_id = parameters.get("WorkspaceId", "")
        agent_id = parameters.get("AgentId", "")
        owner = db.scalar(select(User).where(User.workspace_id == workspace_id, User.role == UserRole.ADMIN, User.is_active.is_(True)).limit(1))
        agent = db.scalar(select(VoiceAgent).where(VoiceAgent.id == agent_id, VoiceAgent.workspace_id == workspace_id, VoiceAgent.is_active.is_(True)))
        if owner is None or agent is None:
            raise HTTPException(status_code=404, detail="Provider destination is not configured")
        call = Call(workspace_id=workspace_id, agent_id=agent.id, created_by_id=owner.id, provider="twilio", provider_call_id=event.provider_call_id, direction=event.direction, from_number=event.from_number, to_number=event.to_number, status=CallStatus.QUEUED, transcript="", idempotency_key=f"twilio:{event.provider_call_id}")
        db.add(call)
        try:
            db.commit()
            db.refresh(call)
        except IntegrityError:
            db.rollback()
            call = db.scalar(select(Call).where(Call.provider == "twilio", Call.provider_call_id == event.provider_call_id))
            if call is None:
                raise
    call = advance_call(db, call, event.status)
    if call.status in {CallStatus.COMPLETED, CallStatus.FAILED}:
        enqueue_summary(call.id)
    return {"status": "accepted", "call_id": call.id, "call_status": call.status.value}


@app.post("/api/realtime/call")
async def create_realtime_call_route(payload: RealtimeCallRequest, request: Request, principal: Principal = Depends(current_principal)):
    _require_allowed_origin(request)
    if settings.voice_mode != "openai":
        raise HTTPException(status_code=409, detail="OpenAI realtime mode is not enabled")
    context = _session_context(principal, payload.context)
    try:
        session, answer_sdp, upstream_request_id = await create_openai_realtime_call(payload.sdp, context)
    except RealtimeConfigurationError:
        raise HTTPException(status_code=503, detail="OpenAI realtime is not configured") from None
    except RealtimeUpstreamError as exc:
        logger.warning("realtime_call_setup_failed", extra={"upstream_status": exc.status_code, "upstream_request_id": exc.request_id})
        raise HTTPException(status_code=502, detail="OpenAI realtime session setup failed") from None
    return Response(content=answer_sdp, media_type="application/sdp", headers={"x-tradevoice-session-id": session.session_id, "cache-control": "no-store", "x-upstream-request-id": upstream_request_id or ""})


@app.post("/api/realtime/sessions/{session_id}/tools")
def run_realtime_tool(session_id: str, payload: RealtimeToolRequest, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _require_owned_session(session_id, principal)
    limiter.check(f"tools:{session_id}", limit=120, window_seconds=60)
    executed = execute_realtime_tool(session_id, payload.call_id, payload.name, payload.arguments, db, principal)
    if executed is None:
        raise HTTPException(status_code=404, detail="Realtime session was not found")
    result, duration_ms = executed
    return {"request_id": uuid.uuid4().hex[:12], "duration_ms": duration_ms, "result": result}


@app.post("/api/realtime/sessions/{session_id}/events")
def record_realtime_event(session_id: str, payload: RealtimeEventRequest, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _require_owned_session(session_id, principal)
    if payload.type != "user_transcript":
        raise HTTPException(status_code=400, detail="Unsupported realtime event")
    return observe_user_transcript(session_id, payload.transcript, db, principal)


@app.patch("/api/realtime/sessions/{session_id}/context")
def change_realtime_context(session_id: str, payload: RealtimeContextRequest, principal: Principal = Depends(current_principal)):
    _require_owned_session(session_id, principal)
    session = update_session_context(session_id, _bounded_context(payload.context))
    return {"status": "updated", "selected_distributor_id": session.context.get("selected_distributor_id")}


@app.delete("/api/realtime/sessions/{session_id}", status_code=204)
def end_realtime_session(session_id: str, principal: Principal = Depends(current_principal)):
    _require_owned_session(session_id, principal)
    session_store.delete(session_id)
    return Response(status_code=204)


@app.websocket("/ws/voice")
async def websocket_endpoint(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        await websocket.close(code=1008, reason="Origin is not allowed")
        return
    await websocket.accept()
    with SessionLocal() as db:
        try:
            init_message = await websocket.receive_text()
            if len(init_message) > 16_384:
                await websocket.close(code=1009, reason="Initial context is too large")
                return
            init_data = json.loads(init_message)
            token = str(init_data.get("access_token", ""))
            try:
                principal = principal_from_token(token, db)
            except HTTPException:
                await websocket.close(code=1008, reason="Authentication required")
                return
            if settings.voice_mode != "mock":
                await websocket.send_json({"type": "error", "message": "Mock WebSocket mode is not enabled."})
                await websocket.close(code=1011, reason="Unsupported voice mode")
                return
            await run_mock_session(websocket, _session_context(principal, init_data.get("context") or {}), db, principal)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.exception("websocket_session_failed", extra={"error_type": type(exc).__name__})
            try:
                await websocket.send_json({"type": "error", "message": "The voice session could not be initialized."})
            except Exception:
                pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), proxy_headers=True)
