"""Seam 1 for Escalation: a text-mode Call through the real agent core and the real backend, with
a scripted LLM. Escalation is decided by code: the scripts below never ask for it, and an empty
script proves the LLM was not consulted. Assertions: what the Caller hears and what is stored."""

from typing import Any

import httpx
import pytest

from agent.llm import Say, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call


def escalations(backend: httpx.Client) -> list[dict[str, Any]]:
    return backend.get("/escalations").json()


def set_clinic(backend: httpx.Client, mode: str) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": mode}).status_code == 200


def test_asking_for_a_human_during_clinic_hours_starts_a_handoff(backend: httpx.Client) -> None:
    set_clinic(backend, "open")
    call = open_text_call(backend, llm=ScriptedLLM([]))  # the LLM is never asked

    heard = call.hear("I want to talk to a human")

    [stored] = escalations(backend)
    assert (stored["call_id"], stored["kind"]) == (call.call_id, "handoff")
    assert stored["reason"] == "caller asked for a human"
    assert len(heard) == 1 and "staff" in heard[0]


def test_asking_for_a_human_outside_clinic_hours_records_a_callback_request(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "closed")
    call = open_text_call(backend, llm=ScriptedLLM([]))

    asked = call.hear("Get me a real person right now")
    assert escalations(backend) == []  # waiting for a number to ring back
    assert "number" in asked[0]
    heard = call.hear("It's 98450 12345")

    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "9845012345")
    assert stored["reason"] == "caller asked for a human"
    assert "call you back" in heard[0]


def test_a_callback_request_uses_the_number_already_given_in_the_call(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "closed")
    llm = ScriptedLLM(
        [
            UseTool("record_patient", {"name": "Priya Sharma", "phone": "+91 98765 43210"}),
            Say("Ok."),
        ]
    )
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning for my mother, Priya Sharma, +91 98765 43210")

    call.hear("Actually just let me speak to someone")

    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "+919876543210")


def test_a_callback_request_is_still_recorded_when_no_number_is_given(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "closed")
    call = open_text_call(backend, llm=ScriptedLLM([]))

    call.hear("I need to speak to a manager")
    call.hear("no")
    call.hear("I won't give it")

    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", None)
    assert stored["reason"] == "caller asked for a human"


def test_asking_for_a_human_works_at_any_point_in_a_booking(backend: httpx.Client) -> None:
    set_clinic(backend, "open")
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "date": "2026-11-03"}),
            Say("I can do {offered[0]}. Which suits?"),
            UseTool("place_hold", {"option": 1}),
            Say("Held."),
        ]
    )
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow")
    call.hear("The first one")

    call.hear("This is taking too long, put me through to the receptionist")

    [stored] = escalations(backend)
    assert stored["kind"] == "handoff"


def test_after_an_escalation_the_agent_no_longer_handles_the_call(backend: httpx.Client) -> None:
    set_clinic(backend, "open")
    call = open_text_call(backend, llm=ScriptedLLM([]))
    call.hear("Let me talk to a human")

    assert call.hear("Hello? Can I book a cleaning?") == []
    assert len(escalations(backend)) == 1


@pytest.mark.parametrize(
    "text",
    [
        "I'd like to book a cleaning for my mother",
        "Can you check Tuesday morning?",
        "Yes, that's right",
    ],
)
def test_ordinary_requests_do_not_escalate(backend: httpx.Client, text: str) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([Say("Sure.")]))

    assert call.hear(text) == ["Sure."]
    assert escalations(backend) == []


def bad_hold() -> UseTool:
    return UseTool("place_hold", {"option": 9})  # never offered: always fails


def booking_start() -> list[Any]:
    return [
        UseTool("check_availability", {"service": "Cleaning", "date": "2026-11-03"}),
        Say("I can do {offered[0]}. Which suits?"),
    ]


def test_two_failures_on_the_same_step_escalate_with_a_reason(backend: httpx.Client) -> None:
    set_clinic(backend, "open")
    llm = ScriptedLLM([*booking_start(), bad_hold(), bad_hold()])  # no step after the second
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow")

    heard = call.hear("The ninth one")

    [stored] = escalations(backend)
    assert stored["kind"] == "handoff"
    assert "place_hold" in stored["reason"] and "twice" in stored["reason"]
    assert "staff" in heard[-1]


def test_two_failures_outside_clinic_hours_lead_to_a_callback_request(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "closed")
    llm = ScriptedLLM([*booking_start(), bad_hold(), bad_hold()])
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow")

    call.hear("The ninth one")
    call.hear("+91 98450 12345")

    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "+919845012345")


def test_one_failure_does_not_escalate(backend: httpx.Client) -> None:
    llm = ScriptedLLM([*booking_start(), bad_hold(), Say("Sorry, which of those?")])
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow")

    assert call.hear("The ninth one") == ["Sorry, which of those?"]
    assert escalations(backend) == []


def test_a_success_in_between_resets_the_failure_count(backend: httpx.Client) -> None:
    llm = ScriptedLLM(
        [
            *booking_start(),
            bad_hold(),
            UseTool("place_hold", {"option": 1}),
            bad_hold(),
            Say("Which one?"),
        ]
    )
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow")

    assert call.hear("Hmm") == ["Which one?"]
    assert escalations(backend) == []
