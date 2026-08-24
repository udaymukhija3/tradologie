from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= window_seconds:
                events.popleft()
            if len(events) >= limit:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            events.append(now)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


limiter = SlidingWindowLimiter()
