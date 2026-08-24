from __future__ import annotations

import json

from app import worker
from app.services.summaries import SUMMARY_DEAD_LETTER_QUEUE, SUMMARY_PROCESSING_QUEUE, SUMMARY_QUEUE


class FakeRedis:
    def __init__(self) -> None:
        self.removed: list[tuple[str, int, str]] = []
        self.pushed: list[tuple[str, str]] = []

    def lrem(self, queue: str, count: int, item: str) -> None:
        self.removed.append((queue, count, item))

    def rpush(self, queue: str, item: str) -> None:
        self.pushed.append((queue, item))


class DummySession:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_worker_acknowledges_a_completed_summary(monkeypatch):
    client = FakeRedis()
    raw = json.dumps({"call_id": "call-1", "attempt": 0})
    monkeypatch.setattr(worker, "SessionLocal", DummySession)
    monkeypatch.setattr(worker, "summarize_call", lambda _db, _call_id: True)

    worker._process_item(client, raw)

    assert client.removed == [(SUMMARY_PROCESSING_QUEUE, 1, raw)]
    assert client.pushed == []


def test_worker_retries_then_dead_letters_failures(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(worker, "SessionLocal", DummySession)
    monkeypatch.setattr(worker, "summarize_call", lambda _db, _call_id: (_ for _ in ()).throw(RuntimeError("boom")))

    worker._process_item(client, json.dumps({"call_id": "call-1", "attempt": 0}))
    worker._process_item(client, json.dumps({"call_id": "call-2", "attempt": 2}))

    assert client.pushed[0][0] == SUMMARY_QUEUE
    assert json.loads(client.pushed[0][1])["attempt"] == 1
    assert client.pushed[1][0] == SUMMARY_DEAD_LETTER_QUEUE
    assert json.loads(client.pushed[1][1])["attempt"] == 3
