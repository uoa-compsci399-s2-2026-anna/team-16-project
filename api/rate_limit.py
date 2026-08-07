"""Process-local fixed-window limiter; addresses are never persisted."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Window:
    started: float
    count: int


class FixedWindowRateLimiter:
    def __init__(self, window_seconds: int = 3600) -> None:
        self.window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, *, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        with self._lock:
            window = self._windows.get(key)
            if window is None or now - window.started >= self.window_seconds:
                self._windows[key] = _Window(now, 1)
                return True, 0
            retry_after = max(1, int(self.window_seconds - (now - window.started)))
            if window.count >= limit:
                return False, retry_after
            window.count += 1
            return True, 0

