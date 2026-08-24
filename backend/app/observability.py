from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from redis import Redis

from app.config import get_settings
from app.database import engine
from app.services.summaries import queue_stats


HTTP_REQUESTS = Counter(
    "tradevoice_http_requests_total",
    "HTTP requests handled by the API.",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "tradevoice_http_request_duration_seconds",
    "Time spent handling HTTP requests.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
DB_POOL_CHECKED_OUT = Gauge(
    "tradevoice_db_pool_checked_out_connections",
    "Database connections currently checked out from the API pool.",
)
SUMMARY_QUEUE_DEPTH = Gauge(
    "tradevoice_summary_queue_depth",
    "Summary jobs waiting, processing, or dead-lettered.",
    ("state",),
)
SUMMARY_WORKER_AVAILABLE = Gauge(
    "tradevoice_summary_worker_available",
    "Whether a summary-worker heartbeat exists in Redis.",
)


class JsonFormatter(logging.Formatter):
    _standard = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._standard and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def observe_request(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status_code)).inc()
    HTTP_DURATION.labels(method=method, route=route).observe(duration_seconds)


def render_metrics() -> tuple[bytes, str]:
    pool = engine.pool
    checked_out = getattr(pool, "checkedout", None)
    if callable(checked_out):
        DB_POOL_CHECKED_OUT.set(checked_out())

    settings = get_settings()
    try:
        redis_client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
        stats = queue_stats(redis_client)
        for state in ("waiting", "processing", "dead_letter"):
            SUMMARY_QUEUE_DEPTH.labels(state=state).set(stats[state])
        SUMMARY_WORKER_AVAILABLE.set(1 if stats["worker_available"] else 0)
    except Exception:
        for state in ("waiting", "processing", "dead_letter"):
            SUMMARY_QUEUE_DEPTH.labels(state=state).set(float("nan"))
        SUMMARY_WORKER_AVAILABLE.set(0)

    return generate_latest(), CONTENT_TYPE_LATEST
