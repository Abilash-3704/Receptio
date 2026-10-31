"""Talking to the clinic backend's cost guard from the voice side. No LiveKit import.

`admit` runs before a token is issued and fails closed: if the backend cannot say yes, there is
no Call. `settle_admission` reports a finished Call's length so unused minutes go back to the
daily budget; it never raises, because the Call is already over."""

import logging
from dataclasses import dataclass

import httpx

from agent.call_limits import CallLimits
from agent.llm import LLM
from agent.text_mode import TextCall, open_text_call

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


def explain_refusal(status_code: int) -> str | None:
    """Why the backend refused the worker, in words an operator can act on. None if it did not."""
    if status_code == 503:
        return (
            "the backend answered 503: it has no BACKEND_SERVICE_TOKEN (unset, or shorter than 16 "
            "characters), so it lets no worker in. Set BACKEND_SERVICE_TOKEN in .env and restart "
            "the backend and the voice worker."
        )
    if status_code in (401, 403):
        return (
            f"the backend answered {status_code}: BACKEND_SERVICE_TOKEN here does not match the "
            "backend's. Give the backend and the voice worker the same value."
        )
    return None


def open_call_or_explain(
    backend: httpx.Client, llm: LLM | None = None, limits: CallLimits | None = None
) -> TextCall | None:
    """Open the Call's record, or log one clear line and return None when the backend refuses the
    worker's credentials (a configuration fault, not something a retry fixes). Any other failure
    still raises."""
    try:
        return open_text_call(backend, llm=llm, limits=limits, mode="voice")
    except httpx.HTTPStatusError as error:
        reason = explain_refusal(error.response.status_code)
        if reason is None:
            raise
        logger.error("cannot start the Call: %s", reason)
        return None


def settle_admission(backend: httpx.Client, room: str, used_seconds: int) -> None:
    try:
        response = backend.post(
            f"/capacity/admissions/{room}/settle", json={"used_seconds": max(0, int(used_seconds))}
        )
    except httpx.HTTPError:
        logger.exception("could not settle the admission for %s; it stays charged in full", room)
        return
    reason = explain_refusal(response.status_code)
    if reason is not None:
        logger.error(
            "could not settle the admission for %s; it stays charged in full: %s", room, reason
        )
