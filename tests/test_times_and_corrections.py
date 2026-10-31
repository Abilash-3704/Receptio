"""Seam 1: text-mode Calls through the real agent core and clinic backend with a scripted LLM, for
how Callers speak about times: corrections, ambiguous times, the booking window, a preferred
Provider, and a Hold that goes away before the yes (docs/tricky-calls.md scenarios 1-4 and 13)."""

from datetime import datetime, timedelta
from typing import Any, cast

import httpx

from agent.llm import Say, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import TextCall, open_text_call
from agent.time_guard import clock_times_in
from evals.state import FinalState, read_final_state
from tests.support import IST, FakeClock

PATIENT = UseTool("record_patient", {"name": "Priya Sharma", "phone": "+91 98765 43210"})


def final_state(backend: httpx.Client) -> FinalState:
    return read_final_state(cast(Any, backend).app.state.session_factory)


def offered(call: TextCall, index: int = -1) -> list[str]:
    checks = [t for t in call.tool_calls if t.name == "check_availability"]
    return checks[index].data.get("offered", [])


def test_a_correction_releases_the_old_hold_and_books_only_the_new_day(
    backend: httpx.Client, clock: FakeClock
) -> None:
    before = final_state(backend)
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "when": "Tuesday afternoon"}),
            Say("I can do {offered[0]} or {offered[1]}."),
            PATIENT,
            UseTool("place_hold", {"option": 1}),
            Say("I'm holding that for you."),
            UseTool("check_availability", {"service": "Cleaning", "when": "Thursday afternoon"}),
            Say("For Thursday I can do {offered[0]} or {offered[1]}."),
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back", {}),
            UseTool("confirm_appointment", {}),
            Say("Booked."),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)

    call.hear("A cleaning on Tuesday afternoon")
    call.hear("The first, for Priya Sharma, 98765 43210")
    assert final_state(backend).held == before.held + 1
    call.hear("Actually, Thursday instead")
    assert final_state(backend).held == before.held  # the Tuesday Hold is gone
    spoken = call.hear("The first one")
    call.hear("Yes")

    assert "Thursday 5 November" in spoken[-1] and "Tuesday" not in spoken[-1]
    after = final_state(backend)
    [appointment] = [a for a in after.confirmed if a.patient == "Priya Sharma"]
    assert appointment.starts_at.date().isoformat() == "2026-11-05"
    assert after.held == before.held


def test_a_yes_that_changes_the_time_is_not_agreement_and_the_new_time_needs_its_own_yes(
    backend: httpx.Client, clock: FakeClock
) -> None:
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "when": "Tuesday at 3"}),
            Say("I have {offered[0]}."),
            PATIENT,
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back", {}),
            UseTool("check_availability", {"service": "Cleaning", "when": "Tuesday at 4"}),
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back", {}),
            UseTool("confirm_appointment", {}),  # the earlier "yes" must not count
            Say("Please say yes to confirm."),
            UseTool("confirm_appointment", {}),
            Say("Booked."),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)
    call.hear("Cleaning Tuesday at 3")
    call.hear("Priya Sharma, 98765 43210")

    call.hear("Yes, wait, make it 4 not 3")  # answered by a fresh read-back for 4 o'clock
    call.hear("Hmm, ok")  # not a yes
    assert [t.refused for t in call.tool_calls if t.name == "confirm_appointment"] == [True]
    assert "Priya Sharma" not in [a.patient for a in final_state(backend).confirmed]
    call.hear("Yes")

    [appointment] = [a for a in final_state(backend).confirmed if a.patient == "Priya Sharma"]
    assert appointment.starts_at.hour == 16


def test_next_tuesday_on_a_sunday_is_confirmed_aloud_before_availability_is_checked(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.now = datetime(2026, 11, 1, 11, 0, tzinfo=IST)  # Sunday
    check = UseTool("check_availability", {"service": "Cleaning", "when": "next Tuesday"})
    llm = ScriptedLLM(
        [
            check,
            check,  # asking again without the Caller's yes must not get through
            Say("Do you mean {date_awaiting_caller_yes}?"),
            check,
            Say("I can do {offered[0]} or {offered[1]}."),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)

    asked = call.hear("Next Tuesday please")
    assert [t.refused for t in call.tool_calls] == [True, True]
    assert "offered" not in call.tool_calls[0].data
    assert "Tuesday 3 November" in asked[0]

    answer = call.hear("Yes")

    assert call.tool_calls[-1].ok and not call.tool_calls[-1].refused
    assert all("Tuesday 3 November" in label for label in offered(call))
    assert "Tuesday 3 November" in answer[0]


def test_after_lunch_this_week_offers_only_afternoon_times_inside_clinic_hours(
    backend: httpx.Client, clock: FakeClock
) -> None:
    llm = ScriptedLLM(
        [
            UseTool(
                "check_availability",
                {"service": "Check-up", "when": "after lunch, sometime this week"},
            ),
            Say("I can do {offered[0]}, {offered[1]} or {offered[2]}."),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)

    call.hear("After lunch, sometime this week")

    labels = offered(call)
    assert len(labels) == 3
    for label in labels:
        [(hour, _)] = clock_times_in(label)
        assert 13 <= hour < 18
    assert len({label.split(" at ")[0] for label in labels}) > 1  # spread over several days


def test_a_request_sooner_than_two_hours_gets_a_callback_flag_and_no_booking(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.now = datetime(2026, 11, 2, 9, 30, tzinfo=IST)  # earliest bookable is 11:30
    before = final_state(backend)
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "when": "in an hour"}),
            Say("That is too soon to book."),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)

    call.hear("Can I come in an hour?")

    [check] = call.tool_calls
    assert check.data["reason"] == "too_soon" and check.data["offer_callback_request"] is True
    for label in check.data["offered"]:
        [(hour, minute)] = clock_times_in(label)
        assert (hour, minute) >= (11, 30) or "Monday" not in label
    assert final_state(backend) == before


def test_nothing_beyond_sixty_days_or_outside_clinic_hours_is_offered(
    backend: httpx.Client, clock: FakeClock
) -> None:
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "when": "in 70 days"}),
            UseTool("check_availability", {"service": "Cleaning", "when": "tomorrow at 8 pm"}),
            UseTool("check_availability", {"service": "Cleaning", "when": "Sunday"}),
            Say("Sorry, none of those are possible."),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)

    call.hear("Whenever")

    far, evening, sunday = call.tool_calls
    assert not far.ok and far.data["reason"] == "too_far" and "offered" not in far.data
    assert evening.data["reason"] == "no_availability" and evening.data["alternatives"]
    assert all("8:00 PM" not in label for label in evening.data["offered"])
    assert sunday.data["reason"] == "no_availability"
    assert all("Sunday" not in label for label in sunday.data["offered"])
    assert len(sunday.data["offered"]) > 0


def test_a_preferred_provider_is_used_when_free_and_not_forced_when_not(
    backend: httpx.Client, clock: FakeClock
) -> None:
    llm = ScriptedLLM(
        [
            UseTool(
                "check_availability",
                {"service": "Cleaning", "when": "Tuesday", "provider": "Meera Nair"},
            ),
            UseTool(
                "check_availability",
                {"service": "Cleaning", "when": "Wednesday", "provider": "Meera Nair"},
            ),
            Say("Meera is off that day, but Dr. Rao has {offered[0]}."),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)

    spoken = call.hear("A cleaning with Meera Nair")

    free, off = call.tool_calls
    assert free.data["preferred_provider_available"] is True
    assert all(label.endswith("with Meera Nair") for label in free.data["offered"])
    assert off.ok and off.data["preferred_provider_available"] is False
    assert off.data["offered"] and all("Meera Nair" not in x for x in off.data["offered"])
    assert "Dr. Anita Rao" in spoken[0]


def test_a_hold_that_expires_before_the_yes_is_not_confirmed_and_needs_a_fresh_read_back(
    backend: httpx.Client, clock: FakeClock
) -> None:
    before = final_state(backend)
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "when": "Tuesday at 2"}),
            Say("I have {offered[0]}."),
            PATIENT,
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back", {}),
            UseTool("confirm_appointment", {}),  # the Hold has expired by now
            UseTool("check_availability", {"service": "Cleaning", "when": "Tuesday at 2"}),
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back", {}),  # a fresh read-back: the old yes was for the old Hold
            UseTool("confirm_appointment", {}),
            Say("Booked."),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)
    call.hear("Cleaning Tuesday at 2")
    call.hear("Priya Sharma, 98765 43210")
    clock.advance(timedelta(minutes=6))

    call.hear("Yes")
    assert not call.tool_calls[3 + 1].ok  # the confirm after the expiry
    assert final_state(backend).confirmed == before.confirmed

    call.hear("Yes, please")  # answers the new read-back
    confirms = [t for t in call.tool_calls if t.name == "confirm_appointment"]
    assert [t.ok for t in confirms] == [False, True]
    assert len([a for a in final_state(backend).confirmed if a.patient == "Priya Sharma"]) == 1
