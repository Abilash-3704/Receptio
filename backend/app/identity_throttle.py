"""Throttle for identification attempts: the defence against guessing name + phone pairs.

Identification (booking.identify) proves who a Caller is with nothing but a full name and a
phone number, and answers every failure with the same 403. That uniformity hides who exists, but
it does not stop a script from trying pairs through the API. This module counts FAILED attempts
in three buckets, and refuses further attempts (429, before any work) once one is full:

- per name: guessing phone numbers for a known name;
- per phone: guessing names for a known number;
- overall: varying both every time. The backend sees the agent as its only client, not the
  Caller's address, so this is the bound on a pair-guessing script; it is deliberately not tied
  to an IP the backend cannot see.

Successes are never counted. The price: someone who knows a Patient's name can lock that name
out of identification for the window by failing on purpose. That is a nuisance (the Caller gets
a Callback Request instead), not a leak, and preferred over letting guesses through. The
state is in process (see ratelimit.py); a restart forgets it."""

import hashlib
import math
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from backend.app.config import BackendSettings
from backend.app.ratelimit import SlidingWindowLimiter

OVERALL = "all"


def _digest(kind: str, value: str) -> str:
    return hashlib.sha256(f"{kind}\x1f{value}".encode()).hexdigest()


def _name_key(name: str) -> str:
    return _digest("name", re.sub(r"\s+", " ", name).strip().casefold())


def _phone_key(phone: str) -> str:
    return _digest("phone", re.sub(r"[^\d+]", "", phone))


class IdentityThrottle:
    def __init__(self, settings: BackendSettings, now: Callable[[], float]) -> None:
        window = settings.identity_failure_window_seconds
        self._by_name = SlidingWindowLimiter(settings.identity_failures_per_name, window, now)
        self._by_phone = SlidingWindowLimiter(settings.identity_failures_per_phone, window, now)
        self._overall = SlidingWindowLimiter(settings.identity_failures_global, window, now)

    def _waits(self, name: str, phone: str) -> list[float]:
        found = (
            self._by_name.blocked(_name_key(name)),
            self._by_phone.blocked(_phone_key(phone)),
            self._overall.blocked(OVERALL),
        )
        return [w for w in found if w is not None]

    @contextmanager
    def guard(self, name: str, phone: str) -> Iterator[None]:
        """Wrap one identification attempt: refused up front when a bucket is full, and a 403
        from inside counts as a failure."""
        waits = self._waits(name, phone)
        if waits:
            raise HTTPException(
                status_code=429,
                detail="too many failed identification attempts: try again later",
                headers={"Retry-After": str(max(1, math.ceil(max(waits))))},
            )
        try:
            yield
        except HTTPException as error:
            if error.status_code == 403:
                self._by_name.record(_name_key(name))
                self._by_phone.record(_phone_key(phone))
                self._overall.record(OVERALL)
            raise
