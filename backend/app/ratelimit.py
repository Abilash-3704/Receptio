"""Sliding-window rate limiting, in process. No dependencies.

Why in process: one backend and one web process serve the demo, so one dict is exact for that
deployment, costs no round trip and adds no moving part. With several replicas each would
enforce the limit on its own, so the effective limit is the configured one times the replica
count; move the counters to a shared store (Redis was in the original plan) if that ever matters.
A restart forgets the window, which only ever lets a few extra requests through.

This is abuse throttling and has nothing to do with the cost guard's daily counters
(backend/app/capacity.py), which live in the database and are never touched from here: a request
that is rate limited is refused before the cost guard is asked, so it can neither use up a
visitor's daily allowance nor be counted twice.

Two ways to use it. `hit` records the request and says whether it may proceed (token issuance:
every request counts). `blocked` and `record` split the two (identification: only a failure
counts, and a refusal is decided before the work is done)."""

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Request


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        now: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._now = now
        self._max_keys = max_keys
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> deque[float]:
        stamps = self._seen.get(key)
        if stamps is None:
            stamps = self._seen[key] = deque()
        while stamps and stamps[0] <= now - self._window:
            stamps.popleft()
        return stamps

    def _wait(self, stamps: deque[float], now: float) -> float | None:
        if len(stamps) < self._limit:
            return None
        return stamps[len(stamps) - self._limit] + self._window - now

    def _make_room(self, now: float) -> None:
        if len(self._seen) < self._max_keys:
            return
        for key in [k for k, v in self._seen.items() if not v or v[-1] <= now - self._window]:
            del self._seen[key]
        # Still full of live keys (a flood from many addresses): forget the stalest. Forgetting
        # a key only ever grants that key a fresh allowance, and the flood must keep paying for it.
        while len(self._seen) >= self._max_keys:
            del self._seen[next(iter(self._seen))]

    def hit(self, key: str) -> float | None:
        """Count a request. None if it may proceed, else seconds until it could."""
        with self._lock:
            now = self._now()
            if key not in self._seen:
                self._make_room(now)
            stamps = self._live(key, now)
            wait = self._wait(stamps, now)
            if wait is None:
                stamps.append(now)
            return wait

    def blocked(self, key: str) -> float | None:
        """Seconds until `key` may proceed, or None. Counts nothing."""
        with self._lock:
            now = self._now()
            stamps = self._seen.get(key)
            if stamps is None:
                return None
            return self._wait(self._live(key, now), now)

    def record(self, key: str) -> None:
        with self._lock:
            now = self._now()
            if key not in self._seen:
                self._make_room(now)
            self._live(key, now).append(now)

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._seen)


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """The address a request really came from. The connection's own address, unless
    `trusted_proxy_hops` says how many reverse proxies of ours append to X-Forwarded-For; then
    only the entry our outermost proxy added is used, never what the client wrote further left
    (a client can claim anything there). The same rules for the token endpoint, the cost guard
    and authentication throttling."""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_hops <= 0:
        return peer
    forwarded = [
        p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
    ]
    if len(forwarded) < trusted_proxy_hops:
        return peer  # not what we expect from our own proxies: do not trust it
    return forwarded[-trusted_proxy_hops]
