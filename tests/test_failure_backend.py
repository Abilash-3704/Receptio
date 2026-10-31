"""Failure injection, backend side (ticket 19): a text-mode Call through the real agent core and
real backend, with the backend client wrapped so chosen requests fail. Assertions: what the Caller
hears, what the database holds, and that no failure produces a second or a half-made Appointment."""

from datetime import timedelta
from typing import Any, cast

import httpx

from agent.failure_injection import FlakyBackend
from agent.llm import Say, UseTool
from agent.safety import EMERGENCY_REASON, EMERGENCY_SCRIPT
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import TextCall, open_text_call
from backend.app.scheduling import HOLD_DURATION
from evals.state import FinalState, read_final_state
from tests.support import START_OF_TEST, FakeClock, availability

PATIENT = UseTool("record_patient", {"name": "Priya Sharma", "phone": "+91 98765 43210"})


def final_state(backend: httpx.Client, now: Any = START_OF_TEST) -> FinalState:
    return read_final_state(cast(Any, backend).app.state.session_factory, now)


def flaky(backend: httpx.Client) -> tuple[FlakyBackend, httpx.Client]:
    wrapper = FlakyBackend(backend)
    return wrapper, cast(httpx.Client, wrapper)


def escalations(backend: httpx.Client) -> list[dict[str, Any]]:
    return backend.get("/escalations").json()


def priya(backend: httpx.Client) -> list[Any]:
    return [a for a in final_state(backend).confirmed if a.patient == "Priya Sharma"]


def booking_to_read_back(client: httpx.Client, *tail: Say | UseTool) -> TextCall:
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "when": "2026-11-03"}),
            Say("I can do {offered[0]}. Does that suit you?"),
            PATIENT,
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back", {}),
            *tail,
        ]
    )
    call = open_text_call(client, llm=llm)
    call.hear("A cleaning tomorrow please")
    call.hear("That works, I'm Priya Sharma, +91 98765 43210")
    return call


def booking_with_confirm(client: httpx.Client) -> TextCall:
    return booking_to_read_back(client, UseTool("confirm_appointment", {}), Say("All booked."))


def test_a_confirm_that_times_out_after_the_backend_committed_returns_the_same_appointment(
    backend: httpx.Client,
) -> None:
    wrapper, client = flaky(backend)
    wrapper.fail("POST", r"/holds/\d+/confirm", "timeout_after_commit", times=1)
    call = booking_with_confirm(client)

    heard = call.hear("Yes, that's right")

    after = final_state(backend)
    [appointment] = priya(backend)
    assert wrapper.count("POST", r"/holds/\d+/confirm") == 2  # one retry, same Idempotency-Key
    assert heard[-1] == "All booked."
    assert [m.appointment_id for m in after.messages] == [appointment.id]
    assert after.live_holds == 0


def test_a_slow_backend_that_answers_the_retry_costs_the_caller_only_an_apology(
    backend: httpx.Client,
) -> None:
    wrapper, client = flaky(backend)
    wrapper.fail("GET", "/availability", "timeout", times=1)
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "when": "2026-11-03"}),
            Say("I can do {offered[0]}. Does that suit you?"),
        ]
    )
    call = open_text_call(client, llm=llm)

    heard = call.hear("A cleaning tomorrow please")

    assert heard[0] == "Sorry for the wait."
    assert "Tuesday 3 November" in heard[1]
    assert escalations(backend) == []


def test_a_backend_that_stays_down_gets_an_apology_an_offer_and_then_staff(
    backend: httpx.Client,
) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": "closed"}).status_code == 200
    wrapper, client = flaky(backend)
    wrapper.fail("GET", "/availability", "unavailable", times=2)
    llm = ScriptedLLM([UseTool("check_availability", {"service": "Cleaning", "when": "tomorrow"})])
    call = open_text_call(client, llm=llm)
    before = final_state(backend)

    offer = call.hear("A cleaning tomorrow please")
    asked = call.hear("Yes please, call me back")
    done = call.hear("It's 98450 12345")

    assert wrapper.count("GET", "/availability") == 2  # exactly one retry
    assert "sorry" in offer[0].lower() and "staff" in offer[0]
    assert "number" in asked[0]
    assert "call you back" in done[0]
    [stored] = escalations(backend)
    assert stored["kind"] == "callback_request"
    assert "check_availability" in stored["reason"]
    assert final_state(backend).confirmed == before.confirmed


def test_a_confirm_that_never_answers_is_not_claimed_as_confirmed(backend: httpx.Client) -> None:
    wrapper, client = flaky(backend)
    wrapper.fail("POST", r"/holds/\d+/confirm", "timeout", times=2)
    call = booking_with_confirm(client)

    heard = call.hear("Yes, that's right")

    assert wrapper.count("POST", r"/holds/\d+/confirm") == 2
    assert "booked" not in " ".join(heard).lower() and "confirmed" not in " ".join(heard).lower()
    assert "couldn't tell whether that went through" in heard[-1]
    after = final_state(backend)
    assert priya(backend) == []
    assert "Priya Sharma" not in after.patients
    assert after.messages == []


def test_a_hold_left_by_a_failed_confirm_does_not_block_availability_past_its_expiry(
    backend: httpx.Client, clock: FakeClock
) -> None:
    wrapper, client = flaky(backend)
    wrapper.fail("POST", r"/holds/\d+/confirm", "timeout", times=2)
    free = [s["start"] for s in availability(backend, "Cleaning", "2026-11-03")]
    call = booking_with_confirm(client)
    call.hear("Yes, that's right")
    held_now = [s["start"] for s in availability(backend, "Cleaning", "2026-11-03")]
    assert held_now != free  # the Hold blocks its time while it is live

    clock.advance(HOLD_DURATION + timedelta(seconds=1))

    assert final_state(backend, clock()).live_holds == 0
    assert [s["start"] for s in availability(backend, "Cleaning", "2026-11-03")] == free


def test_after_a_failed_confirm_a_yes_to_the_offer_passes_the_call_to_staff_and_frees_the_hold(
    backend: httpx.Client,
) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": "open"}).status_code == 200
    wrapper, client = flaky(backend)
    wrapper.fail("POST", r"/holds/\d+/confirm", "timeout", times=2)
    call = booking_with_confirm(client)
    call.hear("Yes, that's right")

    heard = call.hear("Yes please")

    [stored] = escalations(backend)
    assert stored["kind"] == "handoff"
    assert "confirm_appointment" in stored["reason"]
    assert "staff" in heard[0]
    assert final_state(backend).live_holds == 0


def test_a_confirm_whose_replies_were_all_lost_never_makes_a_second_appointment(
    backend: httpx.Client,
) -> None:
    # The backend committed on both tries and the agent never heard. After the outage a third
    # confirm, on the Caller's next turn, must still return the same Appointment.
    wrapper, client = flaky(backend)
    wrapper.fail("POST", r"/holds/\d+/confirm", "timeout_after_commit", times=2)
    call = booking_to_read_back(
        client,
        UseTool("confirm_appointment", {}),
        UseTool("confirm_appointment", {}),
        Say("All booked."),
    )
    heard = call.hear("Yes, that's right")
    assert "couldn't tell whether that went through" in heard[-1]
    [mine] = priya(backend)  # it did commit, once

    again = call.hear("No, please just try again")

    assert wrapper.count("POST", r"/holds/\d+/confirm") == 3
    assert again[-1] == "All booked."
    assert [a.id for a in priya(backend)] == [mine.id]


def test_a_read_back_agreement_that_cannot_be_stored_is_not_a_yes(backend: httpx.Client) -> None:
    wrapper, client = flaky(backend)
    wrapper.fail("POST", r"/holds/\d+/read-back", "unavailable", times=2)
    call = booking_with_confirm(client)

    heard = call.hear("Yes, that's right")

    assert "sorry" in heard[0].lower() and "staff" in heard[0]
    assert call.tool_calls[-1].name == "read_back"  # confirm was never even tried
    assert priya(backend) == []


def test_a_confirmation_message_that_fails_does_not_unmake_the_appointment(
    backend: httpx.Client,
) -> None:
    wrapper, client = flaky(backend)
    wrapper.fail("POST", "/confirmation-message", "unavailable", times=None)
    call = booking_with_confirm(client)

    heard = call.hear("Yes, that's right")

    assert heard == ["All booked."]
    assert len(priya(backend)) == 1


def test_staff_that_cannot_be_reached_are_not_pretended_to_be(backend: httpx.Client) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": "open"}).status_code == 200
    wrapper, client = flaky(backend)
    call = open_text_call(client, llm=ScriptedLLM([]))
    wrapper.fail("POST", "/escalations", "unavailable", times=None)

    heard = call.hear("Let me talk to a human")

    assert "can't reach our staff system" in heard[0]
    assert escalations(backend) == []
    wrapper.heal()
    retried = call.hear("Yes please")
    assert len(escalations(backend)) == 1 and "staff" in retried[0]


def test_an_emergency_is_still_spoken_when_the_whole_backend_is_down(
    backend: httpx.Client,
) -> None:
    wrapper, client = flaky(backend)
    call = open_text_call(client, llm=ScriptedLLM([]))
    wrapper.fail("GET", ".*", "connect_error", times=None)
    wrapper.fail("POST", ".*", "connect_error", times=None)

    heard = call.hear("My father collapsed and he is not breathing")

    assert " ".join(heard) == EMERGENCY_SCRIPT
    assert escalations(backend) == []  # nothing could be recorded yet
    wrapper.heal()
    call.hear("He is still not breathing")
    [stored] = escalations(backend)  # recorded as soon as the backend is back
    assert stored["reason"] == EMERGENCY_REASON
