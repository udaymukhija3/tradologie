from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

# Draining expired timestamps leaves the key itself behind, so a process that
# has seen many distinct keys (one per realtime session id, one per client
# address) grows a dict entry per key for the lifetime of the process. Sweeping
# is throttled because it is O(keys).
SWEEP_INTERVAL_SECONDS = 60


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._widest_window = 0
        self._last_sweep = time.monotonic()

    def _sweep_locked(self, now: float) -> None:
        if now - self._last_sweep < SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep = now
        # A key whose newest event predates the widest window ever used cannot
        # be limiting anything, whatever window its next caller asks for.
        stale = [key for key, events in self._events.items() if not events or now - events[-1] >= self._widest_window]
        for key in stale:
            del self._events[key]

    def check(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        with self._lock:
            self._widest_window = max(self._widest_window, window_seconds)
            self._sweep_locked(now)
            events = self._events[key]
            while events and now - events[0] >= window_seconds:
                events.popleft()
            if len(events) >= limit:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            events.append(now)

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._widest_window = 0
            self._last_sweep = time.monotonic()


limiter = SlidingWindowLimiter()
