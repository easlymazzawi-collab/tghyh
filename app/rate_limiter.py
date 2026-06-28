from __future__ import annotations

import threading
import time


class RateLimiter:
    """Simple token-bucket style limiter for outbound login requests."""

    def __init__(self, requests_per_second: float) -> None:
        self._interval = 1.0 / max(requests_per_second, 0.1)
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
            self._next_allowed = time.monotonic() + self._interval
