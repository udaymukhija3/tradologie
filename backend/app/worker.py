from __future__ import annotations

import json
import logging

from redis import Redis

from app.config import get_settings
from app.database import SessionLocal
from app.services.summaries import (
    MAX_SUMMARY_ATTEMPTS,
    SUMMARY_DEAD_LETTER_QUEUE,
    SUMMARY_PROCESSING_QUEUE,
    SUMMARY_QUEUE,
    WORKER_HEARTBEAT_KEY,
    summarize_call,
)


logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","message":"%(message)s"}')
logger = logging.getLogger("tradevoice.worker")


def _heartbeat(client: Redis) -> None:
    client.set(WORKER_HEARTBEAT_KEY, "ok", ex=15)


def _restore_interrupted_jobs(client: Redis) -> int:
    restored = 0
    while client.rpoplpush(SUMMARY_PROCESSING_QUEUE, SUMMARY_QUEUE) is not None:
        restored += 1
    return restored


def _process_item(client: Redis, raw_item: str) -> None:
    try:
        payload = json.loads(raw_item)
        call_id = payload["call_id"]
        attempt = int(payload.get("attempt", 0))
        with SessionLocal() as db:
            if summarize_call(db, call_id):
                logger.info("summary_completed call_id=%s", call_id)
            else:
                logger.warning("summary_call_missing call_id=%s", call_id)
        client.lrem(SUMMARY_PROCESSING_QUEUE, 1, raw_item)
    except Exception as exc:
        client.lrem(SUMMARY_PROCESSING_QUEUE, 1, raw_item)
        try:
            payload = json.loads(raw_item)
            payload["attempt"] = int(payload.get("attempt", 0)) + 1
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            payload = {"invalid_payload": raw_item, "attempt": MAX_SUMMARY_ATTEMPTS}
        destination = SUMMARY_QUEUE if payload["attempt"] < MAX_SUMMARY_ATTEMPTS else SUMMARY_DEAD_LETTER_QUEUE
        client.rpush(destination, json.dumps(payload))
        logger.exception("summary_failed attempt=%s error_type=%s", payload["attempt"], type(exc).__name__)


def run() -> None:
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    restored = _restore_interrupted_jobs(client)
    _heartbeat(client)
    logger.info("summary_worker_started restored_jobs=%s", restored)
    while True:
        _heartbeat(client)
        item = client.brpoplpush(SUMMARY_QUEUE, SUMMARY_PROCESSING_QUEUE, timeout=5)
        if item is None:
            continue
        _process_item(client, item)


if __name__ == "__main__":
    run()
