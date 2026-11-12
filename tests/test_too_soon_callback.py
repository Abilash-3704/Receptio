"""Seam 1: a Caller who wants something sooner than the 2-hour notice allows is offered a
Callback Request, and it is recorded only if they take it up. Ticket 07's Escalation does the
recording (the backend decides Handoff or Callback Request from Clinic Hours)."""

from datetime import datetime

import httpx

from agent.llm import Say, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call
from tests.support import IST, FakeClock

TOO_SOON = UseTool("check_availability", {"service": "Cleaning", "when": "in an hour"})


def test_a_caller_who_accepts_the_offer_gets_a_callback_request(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.now = datetime(2026, 11, 2, 9, 30, tzinfo=IST)
    backend.put("/demo/clinic-override", json={"mode": "closed"}).raise_for_status()
    llm = ScriptedLLM(
        [TOO_SOON, Say("That is too soon to book. Shall I have staff call you back?")]
    )
    call = open_text_call(backend, llm=llm, clock=clock)
    call.hear("Can I come in an hour?")
    assert backend.get("/escalations").json() == []  # offered, not yet taken up

    asked = call.hear("Yes please")
    assert "number" in asked[0]
    call.hear("98450 12345")

    [stored] = backend.get("/escalations").json()
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "9845012345")
    assert "2 hours" in stored["reason"]


def test_during_clinic_hours_the_accepted_offer_is_still_a_callback_request_not_a_handoff(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.now = datetime(2026, 11, 2, 9, 30, tzinfo=IST)  # Monday, open
    backend.put("/demo/clinic-override", json={"mode": "open"}).raise_for_status()
    llm = ScriptedLLM(
        [TOO_SOON, Say("That is too soon to book. Shall I have staff call you back?")]
    )
    call = open_text_call(backend, llm=llm, clock=clock)
    call.hear("Can I come in an hour?")

    asked = call.hear("Yes please")
    assert "number" in asked[0]  # a callback needs a number to ring, even when staff are in
    heard = call.hear("98450 12345")

    [stored] = backend.get("/escalations").json()
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "9845012345")
    assert "call you back" in heard[-1]


def test_a_caller_who_takes_another_time_instead_is_not_escalated(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.now = datetime(2026, 11, 2, 9, 30, tzinfo=IST)
    llm = ScriptedLLM(
        [
            TOO_SOON,
            Say("That is too soon to book. Would tomorrow work?"),
            UseTool("check_availability", {"service": "Cleaning", "when": "tomorrow"}),
            Say("I can do {offered[0]}."),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)
    call.hear("Can I come in an hour?")

    call.hear("Yes, tomorrow then")  # not a plain yes to the callback offer

    assert backend.get("/escalations").json() == []
