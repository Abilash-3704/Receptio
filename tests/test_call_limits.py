"""The Call duration cap through the agent core (text mode), with an injected clock: a warning at
4:30 that offers a Callback Request, and a spoken close at 5:00. The Call is never cut off
silently. Voice uses the same `TextCall`, so these hold there too."""

from datetime import timedelta
from typing import Any

import httpx

from agent.call_limits import CallLimits
from agent.config import AgentSettings
from agent.llm import Say
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import TextCall, open_text_call
from tests.support import FakeClock

LIMITS = CallLimits(max_seconds=300, warning_seconds=270)


def start(backend: httpx.Client, clock: FakeClock, *steps: Say) -> TextCall:
    return open_text_call(backend, llm=ScriptedLLM(list(steps)), clock=clock, limits=LIMITS)


def escalations(backend: httpx.Client) -> list[dict[str, Any]]:
    return backend.get("/escalations").json()


def test_the_limits_default_to_the_specified_values_and_are_configuration() -> None:
    settings = AgentSettings()
    assert (settings.call_max_seconds, settings.call_warning_seconds) == (300, 270)
    assert CallLimits.from_settings(settings) == LIMITS
    custom = AgentSettings(call_max_seconds=60, call_warning_seconds=50)
    assert CallLimits.from_settings(custom) == CallLimits(60, 50)


def test_no_warning_before_four_thirty(backend: httpx.Client, clock: FakeClock) -> None:
    call = start(backend, clock, Say("Sure."))
    clock.advance(timedelta(seconds=269))

    assert call.hear("What are your hours?") == ["Sure."]


def test_at_four_thirty_the_reply_carries_a_warning_that_offers_a_callback_request(
    backend: httpx.Client, clock: FakeClock
) -> None:
    call = start(backend, clock, Say("We open at nine."))
    clock.advance(timedelta(seconds=270))

    heard = call.hear("What are your hours?")

    assert heard[0] == "We open at nine."
    warning = " ".join(heard[1:]).lower()
    assert "30 seconds" in warning and "call you back" in warning
    assert call.transcript[-1].text == heard[-1]  # the warning is part of the stored Call


def test_the_warning_is_given_once(backend: httpx.Client, clock: FakeClock) -> None:
    call = start(backend, clock, Say("One."), Say("Two."))
    clock.advance(timedelta(seconds=271))
    assert len(call.hear("hello")) > 1

    clock.advance(timedelta(seconds=10))

    assert call.hear("and?") == ["Two."]


def test_accepting_the_offered_callback_request_records_one_even_during_clinic_hours(
    backend: httpx.Client, clock: FakeClock
) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": "open"}).status_code == 200
    call = start(backend, clock, Say("We open at nine."))
    clock.advance(timedelta(seconds=275))
    call.hear("What are your hours?")

    asked = call.hear("yes please")
    assert "number" in asked[0]
    heard = call.hear("98450 12345")

    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "9845012345")
    assert "time limit" in stored["reason"]
    assert "call you back" in heard[0]


def test_at_five_minutes_the_call_ends_with_a_spoken_close_not_silence(
    backend: httpx.Client, clock: FakeClock
) -> None:
    call = start(backend, clock)  # an empty script: the LLM must not be consulted
    clock.advance(timedelta(seconds=300))

    heard = call.hear("Are you there?")

    assert len(heard) == 1 and "time limit" in heard[0].lower()
    assert call.expired
    assert call.hear("hello?") == heard  # still answered, never silent


def test_a_silent_caller_still_hears_the_warning_and_then_the_close(
    backend: httpx.Client, clock: FakeClock
) -> None:
    call = start(backend, clock)

    assert call.time_check() == []
    clock.advance(timedelta(seconds=270))
    [warning] = call.time_check()
    assert "30 seconds" in warning
    assert call.time_check() == []  # not repeated
    clock.advance(timedelta(seconds=30))
    [closing] = call.time_check()
    assert "time limit" in closing.lower()
    assert call.time_check() == []
    assert call.expired


def test_a_call_whose_warning_was_skipped_still_says_the_close(
    backend: httpx.Client, clock: FakeClock
) -> None:
    call = start(backend, clock)
    clock.advance(timedelta(seconds=400))

    [closing] = call.time_check()

    assert "time limit" in closing.lower()


def test_no_warning_once_the_call_is_with_staff(backend: httpx.Client, clock: FakeClock) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": "open"}).status_code == 200
    call = start(backend, clock)
    call.hear("I want to talk to a human")
    clock.advance(timedelta(seconds=280))

    assert call.time_check() == []
    assert call.hear("hello") == []  # staff have the Call, as before


def test_a_call_without_limits_is_unbounded(backend: httpx.Client, clock: FakeClock) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([Say("Fine.")]), clock=clock)
    clock.advance(timedelta(hours=1))

    assert call.hear("hi") == ["Fine."]
    assert call.time_check() == []
