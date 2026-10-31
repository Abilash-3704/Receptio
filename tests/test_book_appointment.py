"""Seam 1: a text-mode Call through the real agent core and the real clinic backend, with a
scripted LLM. Assertions: what the Caller hears, what the database holds, which tools were asked.
Never prompt wording."""

from typing import Any, cast

import httpx
import pytest

from agent.llm import Say, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call
from evals.state import FinalState, read_final_state


def final_state(backend: httpx.Client) -> FinalState:
    return read_final_state(cast(Any, backend).app.state.session_factory)


def test_a_booking_call_ends_with_a_confirmed_appointment_for_the_patient(
    backend: httpx.Client,
) -> None:
    before = final_state(backend)
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "date": "2026-11-03"}),
            Say("I can do {offered[0]}, {offered[1]} or {offered[2]}. Which suits?"),
            UseTool("record_patient", {"name": "Priya Sharma", "phone": "+91 98765 43210"}),
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back", {}),
            UseTool("confirm_appointment", {}),
            Say("You're all booked."),
        ]
    )
    call = open_text_call(backend, llm=llm)

    offer = call.hear("Hi, I'd like a cleaning for my mother tomorrow")
    read_back = call.hear("The first one. Her name is Priya Sharma, number 98765 43210")
    done = call.hear("Yes, that's right")
    call.end()

    assert len(offer) == 1 and "Tuesday 3 November" in offer[0]
    assert "Priya Sharma" in read_back[-1] and "Cleaning" in read_back[-1]
    assert done == ["You're all booked."]
    after = final_state(backend)
    [appointment] = [a for a in after.confirmed if a.patient == "Priya Sharma"]
    assert (appointment.service, appointment.patient_phone) == ("Cleaning", "+919876543210")
    assert appointment.read_back_recorded
    assert len(after.confirmed) == len(before.confirmed) + 1
    assert after.patients == [*before.patients, "Priya Sharma"]
    [message] = after.messages
    assert (message.appointment_id, message.to_phone) == (appointment.id, "+919876543210")


def book_up_to_read_back(*after_read_back: Say | UseTool) -> ScriptedLLM:
    return ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "date": "2026-11-03"}),
            Say("I can do {offered[0]}, {offered[1]} or {offered[2]}."),
            UseTool("record_patient", {"name": "Priya Sharma", "phone": "+91 98765 43210"}),
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back", {}),
            *after_read_back,
        ]
    )


def answer_to_read_back(backend: httpx.Client, answer: str) -> tuple[bool, FinalState]:
    """Run a booking up to the read-back, have the Caller answer, and let the LLM try to confirm.
    Returns whether the confirm went through, and the final database state."""
    llm = book_up_to_read_back(UseTool("confirm_appointment", {}), Say("Done."))
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow please")
    call.hear("The first one, for Priya Sharma, +91 98765 43210")
    call.hear(answer)
    return call.tool_calls[-1].ok, final_state(backend)


@pytest.mark.parametrize(
    "answer",
    [
        "uh-huh",
        "mm, I guess",
        "maybe",
        "Yes, but make it Wednesday instead",
        "yes? no wait, what was the time again",
        "No, not that one",
        "I said yesterday, yes, that works for her",
    ],
)
def test_only_an_explicit_yes_counts_as_agreeing_to_the_read_back(
    backend: httpx.Client, answer: str
) -> None:
    confirmed, state = answer_to_read_back(backend, answer)

    assert not confirmed
    assert [a for a in state.confirmed if a.patient == "Priya Sharma"] == []


@pytest.mark.parametrize(
    "answer", ["Yes", "yes please", "Yeah, that's right", "Correct.", "Yes, that works, thanks!"]
)
def test_a_plain_yes_to_the_read_back_lets_the_confirm_through(
    backend: httpx.Client, answer: str
) -> None:
    confirmed, state = answer_to_read_back(backend, answer)

    assert confirmed
    assert [a.patient for a in state.confirmed if a.patient == "Priya Sharma"] == ["Priya Sharma"]


def test_a_time_the_llm_makes_up_never_reaches_the_caller(backend: httpx.Client) -> None:
    llm = ScriptedLLM(
        [
            Say("Sure, 9:30 AM tomorrow is free."),  # invented: Availability was never asked
            UseTool("check_availability", {"service": "Cleaning", "date": "2026-11-03"}),
            Say("I can do {offered[0]} or {offered[1]}."),
        ]
    )
    call = open_text_call(backend, llm=llm)

    heard = call.hear("A cleaning tomorrow please")

    assert len(heard) == 1 and "9:30" not in heard[0]
    assert [t.name for t in call.tool_calls] == ["check_availability"]


def test_an_llm_that_keeps_inventing_times_leaves_the_caller_with_none(
    backend: httpx.Client,
) -> None:
    llm = ScriptedLLM([Say("How about 4:15 PM?")] * 8)
    call = open_text_call(backend, llm=llm)

    heard = call.hear("A cleaning tomorrow please")

    assert heard and all("4:15" not in line for line in heard)


def test_a_call_dropped_after_the_read_back_creates_no_patient_and_no_message(
    backend: httpx.Client,
) -> None:
    before = final_state(backend)
    call = open_text_call(backend, llm=book_up_to_read_back())
    call.hear("A cleaning tomorrow please")
    call.hear("The first one, for Priya Sharma, +91 98765 43210")
    call.end()  # the Caller hangs up without answering

    after = final_state(backend)
    assert after.patients == before.patients
    assert after.confirmed == before.confirmed
    assert after.messages == []


def test_a_yes_before_any_read_back_does_not_count(backend: httpx.Client) -> None:
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "date": "2026-11-03"}),
            Say("I can do {offered[0]}."),
            UseTool("record_patient", {"name": "Priya Sharma", "phone": "+91 98765 43210"}),
            UseTool("place_hold", {"option": 1}),
            Say("I've held that."),
            UseTool("confirm_appointment", {}),
            Say("Done."),
        ]
    )
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow please")
    call.hear("Yes, the first one. Priya Sharma, +91 98765 43210")  # a yes, but to the offer

    call.hear("Yes")

    assert (call.tool_calls[-1].name, call.tool_calls[-1].refused) == ("confirm_appointment", True)
    assert [a for a in final_state(backend).confirmed if a.patient == "Priya Sharma"] == []


def test_the_llm_cannot_confirm_before_the_caller_has_answered_the_read_back(
    backend: httpx.Client,
) -> None:
    before = final_state(backend)
    llm = book_up_to_read_back(UseTool("confirm_appointment", {}), Say("All booked!"))
    call = open_text_call(backend, llm=llm)

    call.hear("A cleaning tomorrow please")
    call.hear("The first one, for Priya Sharma, +91 98765 43210")
    call.hear("Hmm, let me think")  # not a yes

    confirm_attempt = call.tool_calls[-1]
    assert (confirm_attempt.name, confirm_attempt.refused) == ("confirm_appointment", True)
    after = final_state(backend)
    assert after.confirmed == before.confirmed
    assert after.held == before.held + 1  # the Hold is still there, nothing was confirmed
    assert after.messages == []
