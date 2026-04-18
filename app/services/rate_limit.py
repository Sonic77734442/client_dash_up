from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from time import time
from typing import Deque, Dict, Tuple


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class InMemoryRateLimiter:
    def __init__(self):
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, *, max_requests: int, window_seconds: int) -> RateLimitDecision:
        now = time()
        threshold = now - window_seconds
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] <= threshold:
                bucket.popleft()
            if len(bucket) >= max_requests:
                retry_after = max(1, int(bucket[0] + window_seconds - now)) if bucket else window_seconds
                return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)
            bucket.append(now)
        return RateLimitDecision(allowed=True, retry_after_seconds=0)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()
