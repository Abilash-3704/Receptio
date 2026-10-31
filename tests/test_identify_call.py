"""Seam 1 for identification, reschedule and cancel: a text-mode Call through the real agent core
and the real backend, with a scripted LLM. The scripts ask for tools; code decides what is allowed.
Assertions: what the Caller hears, and what the backend holds afterwards."""

from datetime import timedelta
from typing import Any

import httpx
import pytest

from agent.llm import Say, Step, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import TextCall, open_text_call
from tests.support import FakeClock
from tests.test_confirm_cancel_reschedule import booked, status_of

ASHA = {"name": "Asha Menon", "phone": "+91 98450 12345"}
IDENTIFY_ASHA = UseTool("identify_patient", {"name": ASHA["name"], "phone": ASHA["phone"]})
BAD_PHONE = UseTool("identify_patient", {"name": ASHA["name"], "phone": "+91 98450 99999"})


def appointments_of(backend: httpx.Client, name: str, phone: str) -> list[dict[str, Any]]:
    response = backend.post(
        "/appointments/lookup", json={"patient_name": name, "patient_phone": phone}
    )
    assert response.status_code == 200
    return response.json()


def asha_appointments(backend: httpx.Client) -> list[dict[str, Any]]:
    return appointments_of(backend, ASHA["name"], ASHA["phone"])


def call_with(backend: httpx.Client, script: list[Step]) -> TextCall:
    return open_text_call(backend, llm=ScriptedLLM(script))


def book_filling(backend: httpx.Client, start: str = "2026-11-02 14:00") -> dict[str, Any]:
    return booked(backend, "Filling", "Dr. Anita Rao", start, **ASHA)


def escalations(backend: httpx.Client) -> list[dict[str, Any]]:
    return backend.get("/escalations").json()


def test_nothing_about_an_appointment_is_heard_before_name_and_phone_both_match(
    backend: httpx.Client,
) -> None:
    book_filling(backend)
    call = call_with(
        backend,
        [
            # The model blurts out a detail before identification: code withholds it.
            Say("Your Filling is on Monday 2 November at 2:00 PM."),
            Say("Could I have your full name and phone number first?"),
            BAD_PHONE,
            Say("That doesn't match what we have. Could you try again?"),
        ],
    )

    first = call.hear("I want to cancel my appointment")
    second = call.hear(f"{ASHA['name']}, {ASHA['phone']}")

    assert first == ["Could I have your full name and phone number first?"]
    assert second == ["That doesn't match what we have. Could you try again?"]
    failed = call.tool_calls[0]
    assert not failed.ok and "Filling" not in str(failed.data) and "2:00" not in str(failed.data)


def test_two_failed_identifications_lead_to_a_callback_request(backend: httpx.Client) -> None:
    appointment = book_filling(backend)
    backend.put("/demo/clinic-override", json={"mode": "closed"})
    call = call_with(backend, [BAD_PHONE, Say("That doesn't match. Once more?"), BAD_PHONE])

    call.hear("Cancel my appointment, Asha Menon, 98450 99999")
    call.hear("It's 98450 99999")
    heard = call.hear("+91 98450 12347")  # the number to call back

    [stored] = escalations(backend)
    assert stored["kind"] == "callback_request"
    assert "identify_patient" in stored["reason"] and "twice" in stored["reason"]
    assert stored["caller_phone"] == "+919845012347"
    assert "call you back" in heard[-1]
    assert [a["id"] for a in asha_appointments(backend)] == [appointment["id"]]


def test_two_failed_identifications_during_clinic_hours_are_still_a_callback_request(
    backend: httpx.Client,
) -> None:
    book_filling(backend)
    backend.put("/demo/clinic-override", json={"mode": "open"})
    call = call_with(backend, [BAD_PHONE, Say("That doesn't match. Once more?"), BAD_PHONE])

    call.hear("Cancel my appointment, Asha Menon, 98450 99999")
    call.hear("It's 98450 99999")
    heard = call.hear("+91 98450 12347")  # the number to call back

    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "+919845012347")
    assert "call you back" in heard[-1]


def test_a_cancel_is_read_back_and_only_happens_after_an_explicit_yes(
    backend: httpx.Client,
) -> None:
    appointment = book_filling(backend)
    call = call_with(
        backend,
        [
            IDENTIFY_ASHA,
            UseTool("request_cancel"),
            UseTool("cancel_appointment"),  # the model jumps ahead of the Caller's answer
            UseTool("request_cancel"),  # a hedge is not a yes: ask again
            UseTool("cancel_appointment"),
            Say("Done, it's cancelled."),
        ],
    )

    read_back = call.hear("Cancel my appointment please, Asha Menon, +91 98450 12345")
    assert "cancel" in read_back[0].lower() and "Filling" in read_back[0]
    assert "2:00 PM" in read_back[0]
    assert [a["id"] for a in asha_appointments(backend)] == [appointment["id"]]

    asked_again = call.hear("Hmm, wait a second")
    assert "cancel" in asked_again[0].lower()
    assert [a["id"] for a in asha_appointments(backend)] == [appointment["id"]]
    assert call.tool_calls[2].refused

    assert call.hear("Yes, that's right") == ["Done, it's cancelled."]
    assert asha_appointments(backend) == []
    assert status_of(backend, appointment["id"]) == "cancelled"


def test_a_no_to_the_cancel_read_back_keeps_the_appointment(backend: httpx.Client) -> None:
    appointment = book_filling(backend)
    call = call_with(
        backend,
        [
            IDENTIFY_ASHA,
            UseTool("request_cancel"),
            UseTool("cancel_appointment"),
            Say("Alright, I'll leave it as it is."),
            UseTool("cancel_appointment"),  # a later "yes" to something else must not count
            Say("Sorry, I still need you to confirm the cancellation."),
        ],
    )
    call.hear("Cancel my appointment. Asha Menon, +91 98450 12345")

    call.hear("No, keep it")
    call.hear("Yes, that's all I wanted to know")

    assert [a["id"] for a in asha_appointments(backend)] == [appointment["id"]]


def test_the_agent_asks_which_appointment_when_more_than_one_matches(
    backend: httpx.Client,
) -> None:
    first = book_filling(backend, "2026-11-02 14:00")
    second = book_filling(backend, "2026-11-03 14:00")
    call = call_with(
        backend,
        [
            IDENTIFY_ASHA,
            Say("I see two: {appointments[0]} and {appointments[1]}. Which one?"),
            UseTool("request_cancel"),  # no appointment chosen yet
            Say("Which one did you mean?"),
            UseTool("select_appointment", {"option": 2}),
            UseTool("request_cancel"),
            UseTool("cancel_appointment"),
            Say("Cancelled."),
        ],
    )

    asked = call.hear("Cancel my appointment. Asha Menon, +91 98450 12345")
    assert "Monday 2 November" in asked[0] and "Tuesday 3 November" in asked[0]
    assert call.hear("Just cancel it") == ["Which one did you mean?"]
    assert call.tool_calls[1].refused or not call.tool_calls[1].ok
    read_back = call.hear("The second one")
    assert "Tuesday 3 November" in read_back[0] and "Monday" not in read_back[0]
    call.hear("Yes")

    assert [a["id"] for a in asha_appointments(backend)] == [first["id"]]
    assert status_of(backend, second["id"]) == "cancelled"


def test_a_reschedule_is_read_back_then_moves_the_appointment_on_yes(
    backend: httpx.Client,
) -> None:
    original = book_filling(backend)
    call = call_with(
        backend,
        [
            IDENTIFY_ASHA,
            UseTool("start_reschedule"),
            Say("Which day would you like to move it to?"),
            UseTool("check_availability", {"service": "Filling", "date": "2026-11-04"}),
            Say("I can do {offered[0]} or {offered[1]}. Which suits?"),
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back"),
            UseTool("confirm_appointment"),
            Say("All moved."),
        ],
    )
    call.hear("I need to move my appointment. Asha Menon, +91 98450 12345")
    call.hear("Wednesday please")
    read_back = call.hear("The first")
    assert "move" in read_back[0].lower() and "Monday 2 November at 2:00 PM" in read_back[0]
    # Nothing has changed while the Caller is still deciding.
    assert [a["id"] for a in asha_appointments(backend)] == [original["id"]]

    assert call.hear("Yes, that's right") == ["All moved."]

    [moved] = asha_appointments(backend)
    assert moved["id"] != original["id"] and moved["start"].startswith("2026-11-04")
    assert status_of(backend, original["id"]) == "cancelled"
    assert status_of(backend, moved["id"]) == "confirmed"


def test_a_reschedule_that_falls_through_keeps_the_original(
    backend: httpx.Client, clock: FakeClock
) -> None:
    original = book_filling(backend)
    call = call_with(
        backend,
        [
            IDENTIFY_ASHA,
            UseTool("start_reschedule"),
            Say("Which day would you like to move it to?"),
            UseTool("check_availability", {"service": "Filling", "date": "2026-11-04"}),
            Say("I can do {offered[0]}. Does that suit?"),
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back"),
            UseTool("confirm_appointment"),
            Say("Sorry, that time slipped away. Your current appointment is unchanged."),
        ],
    )
    call.hear("Move my appointment. Asha Menon, +91 98450 12345")
    call.hear("Wednesday")
    call.hear("That one")
    clock.advance(timedelta(minutes=30))  # the Hold expires while the Caller dithers

    heard = call.hear("Yes")

    assert heard == ["Sorry, that time slipped away. Your current appointment is unchanged."]
    assert [a["id"] for a in asha_appointments(backend)] == [original["id"]]
    assert not call.tool_calls[-1].ok  # no Hold left to confirm, so no new Appointment


def test_a_reschedule_cannot_be_confirmed_without_the_read_back_yes(
    backend: httpx.Client,
) -> None:
    original = book_filling(backend)
    call = call_with(
        backend,
        [
            IDENTIFY_ASHA,
            UseTool("start_reschedule"),
            Say("Which day would you like to move it to?"),
            UseTool("check_availability", {"service": "Filling", "date": "2026-11-04"}),
            Say("I can do {offered[0]}."),
            UseTool("place_hold", {"option": 1}),
            UseTool("confirm_appointment"),  # no read-back, no yes
            Say("I need you to hear the details first."),
        ],
    )
    call.hear("Move my appointment. Asha Menon, +91 98450 12345")
    call.hear("Wednesday")

    call.hear("The first one")

    assert call.tool_calls[-1].refused
    assert [a["id"] for a in asha_appointments(backend)] == [original["id"]]


def answer_to_cancel_read_back(backend: httpx.Client, answer: str) -> bool:
    """Read the cancellation back, have the Caller answer, let the LLM try to cancel. Returns
    whether the Appointment was cancelled."""
    appointment = book_filling(backend)
    call = call_with(
        backend,
        [
            IDENTIFY_ASHA,
            UseTool("request_cancel"),
            UseTool("cancel_appointment"),
            Say("Done, it's cancelled."),
            Say("Sorry, could you confirm the cancellation?"),
        ],
    )
    call.hear("Cancel my appointment. Asha Menon, +91 98450 12345")
    call.hear(answer)
    return status_of(backend, appointment["id"]) == "cancelled"


@pytest.mark.parametrize(
    "answer",
    [
        "Yes, cancel it",
        "Yes please cancel it",
        "yes cancel",
        "Yes, go ahead and cancel",
        "Yeah, cancel that",
        "Yes, please cancel it, thanks!",
    ],
)
def test_a_short_yes_that_names_the_cancel_counts_as_agreeing(
    backend: httpx.Client, answer: str
) -> None:
    assert answer_to_cancel_read_back(backend, answer)


@pytest.mark.parametrize(
    "answer",
    [
        "Yes, but...",
        "yes but only if it is free",
        "uh-huh",
        "yes?",
        "I guess",
        "I guess, cancel it",
        "maybe yes",
        "maybe yes, cancel it",
        "Yes, but not that one",
        "yes... actually no",
        "Yes, cancel it, no wait",
        "Yes, cancel it?",
        "Yes, don't cancel it",
        "Yes, cancel it tomorrow",
        "cancel it",  # no affirmative: not a yes
        "Yes, book it",  # the action must match the question asked
    ],
)
def test_a_hedged_or_conditional_answer_does_not_cancel(backend: httpx.Client, answer: str) -> None:
    assert not answer_to_cancel_read_back(backend, answer)
