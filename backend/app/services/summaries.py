from __future__ import annotations

import json
from typing import cast

from redis import Redis
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Call

SUMMARY_QUEUE = "tradevoice:call-summaries"
SUMMARY_PROCESSING_QUEUE = "tradevoice:call-summaries:processing"
SUMMARY_DEAD_LETTER_QUEUE = "tradevoice:call-summaries:dead"
WORKER_HEARTBEAT_KEY = "tradevoice:summary-worker:heartbeat"
MAX_SUMMARY_ATTEMPTS = 3


def build_summary(transcript: str, outcome: str | None) -> str:
    clean = " ".join(transcript.split())
    if not clean:
        return "Call completed without a captured transcript."
    sentences = [segment.strip() for segment in clean.replace("?", ".").replace("!", ".").split(".") if segment.strip()]
    excerpt = ". ".join(sentences[:2])
    suffix = f" Outcome: {outcome}." if outcome else ""
    return f"{excerpt[:700]}.{suffix}".strip()


def summarize_call(db: Session, call_id: str) -> bool:
    call = db.get(Call, call_id)
    if call is None:
        return False
    call.summary = build_summary(call.transcript, call.outcome)
    db.commit()
    return True


def enqueue_summary(call_id: str) -> bool:
    try:
        client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)
        client.rpush(SUMMARY_QUEUE, json.dumps({"call_id": call_id, "attempt": 0}))
        return True
    except Exception:
        with SessionLocal() as db:
            return summarize_call(db, call_id)


def queue_depth(client: Redis | None = None) -> int | None:
    try:
        redis_client = client or Redis.from_url(get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)
        return cast(int, redis_client.llen(SUMMARY_QUEUE))
    except Exception:
        return None


def queue_stats(client: Redis) -> dict[str, int | bool]:
    return {
        "waiting": cast(int, client.llen(SUMMARY_QUEUE)),
        "processing": cast(int, client.llen(SUMMARY_PROCESSING_QUEUE)),
        "dead_letter": cast(int, client.llen(SUMMARY_DEAD_LETTER_QUEUE)),
        "worker_available": worker_is_available(client),
    }


def worker_is_available(client: Redis) -> bool:
    return bool(client.exists(WORKER_HEARTBEAT_KEY))
