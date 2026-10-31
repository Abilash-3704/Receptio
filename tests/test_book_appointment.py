"""Seam 1: a text-mode Call through the real agent core and the real clinic backend, with a
scripted LLM. Assertions: what the Caller hears, what the database holds, which tools were asked.
Never prompt wording."""

from typing import Any, cast

import httpx

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
