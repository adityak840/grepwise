"""In-memory rate limits: per client (a minute and/or a day cap), and shared caps. They protect
the LLM quota and the server.

In memory is enough for one server process; limits reset when it restarts.
"""

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

MINUTE, DAY = 60.0, 86_400.0


class RateLimiter:
    def __init__(
        self,
        per_minute: int | None,
        per_day: int | None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limits = [(w, n) for w, n in ((DAY, per_day), (MINUTE, per_minute)) if n]
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def wait(self, key: str) -> int | None:
        """Seconds until `key` may make another request, or None if it may now. Records
        nothing: call `hit` once the request is actually accepted."""
        with self._lock:
            now = self.clock()
            hits = self._hits[key]
            while hits and now - hits[0] >= DAY:
                hits.popleft()
            waits = []
            for window, limit in self.limits:
                recent = [t for t in hits if now - t < window]
                if len(recent) >= limit:
                    waits.append(max(1, math.ceil(window - (now - recent[-limit]))))
            return max(waits) if waits else None

    def hit(self, key: str) -> None:
        with self._lock:
            self._hits[key].append(self.clock())

    def check(self, key: str) -> int | None:
        """`wait`, and `hit` if allowed."""
        wait = self.wait(key)
        if wait is None:
            self.hit(key)
        return wait
