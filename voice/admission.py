"""Talking to the clinic backend's cost guard from the voice side. No LiveKit import.

`admit` runs before a token is issued and fails closed: if the backend cannot say yes, there is
no Call. `settle_admission` reports a finished Call's length so unused minutes go back to the
daily budget; it never raises, because the Call is already over."""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger("receptio.voice")

UNAVAILABLE_MESSAGE = "The demo can't take calls right now. Please try again in a moment."


@dataclass(frozen=True)
class Admission:
    admitted: bool
    status_code: int  # 200 admitted, 429 refused by a cap, 503 the guard could not be asked
    reason: str = ""
    message: str = ""


def admit(backend: httpx.Client, room: str, browser: str, ip: str) -> Admission:
    try:
        response = backend.post(
            "/capacity/admissions", json={"room": room, "browser": browser, "ip": ip}
        )
    except httpx.HTTPError:
        logger.exception("cost guard unreachable; refusing the Call")
        return Admission(False, 503, "unavailable", UNAVAILABLE_MESSAGE)
    if response.status_code == 201:
        return Admission(True, 200)
    if response.status_code == 429:
        body = response.json()
        return Admission(False, 429, body["reason"], body["message"])
    logger.error("cost guard answered %s; refusing the Call", response.status_code)
    return Admission(False, 503, "unavailable", UNAVAILABLE_MESSAGE)


def settle_admission(backend: httpx.Client, room: str, used_seconds: int) -> None:
    try:
        backend.post(
            f"/capacity/admissions/{room}/settle", json={"used_seconds": max(0, int(used_seconds))}
        )
    except httpx.HTTPError:
        logger.exception("could not settle the admission for %s; it stays charged in full", room)
