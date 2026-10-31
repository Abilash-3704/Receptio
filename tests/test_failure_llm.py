"""Failure injection, LLM side (ticket 19): the LLM is wrapped so it fails on cue. The safety
detector, the escalation rules and the Caller's answer to an offer are code, so none of them needs
the LLM: `FailingLLM.requests` counts how often it was asked. No provider is called."""

from typing import Any, cast

import anthropic
import httpx
import pytest

from agent.anthropic_llm import AnthropicLLM
from agent.failure_injection import FailingLLM
from agent.llm import LLMError, LLMTurn, Say, UseTool
from agent.safety import EMERGENCY_SCRIPT, URGENT_REASON
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call
from evals.state import read_final_state
from tests.support import START_OF_TEST


def escalations(backend: httpx.Client) -> list[dict[str, Any]]:
    return backend.get("/escalations").json()


def test_an_llm_error_once_is_retried_after_an_apology(backend: httpx.Client) -> None:
    llm = FailingLLM(ScriptedLLM([Say("We open at nine.")]), fail_times=1)
    call = open_text_call(backend, llm=llm)

    heard = call.hear("When do you open?")

    assert heard == ["Sorry for the wait.", "We open at nine."]
    assert llm.requests == 2  # one retry, no more


def test_an_llm_that_stays_down_gets_an_apology_and_an_offer_of_staff(
    backend: httpx.Client,
) -> None:
    llm = FailingLLM(fail_times=None)
    call = open_text_call(backend, llm=llm)

    heard = call.hear("When do you open?")

    assert llm.requests == 2
    assert len(heard) == 1 and "sorry" in heard[0].lower() and "staff" in heard[0]
    assert escalations(backend) == []


def test_a_yes_to_the_offer_reaches_staff_without_the_llm(backend: httpx.Client) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": "open"}).status_code == 200
    llm = FailingLLM(fail_times=None)
    call = open_text_call(backend, llm=llm)
    call.hear("When do you open?")
    asked = llm.requests

    heard = call.hear("Yes please")

    assert llm.requests == asked
    [stored] = escalations(backend)
    assert (stored["kind"], stored["reason"]) == (
        "handoff",
        "the assistant's language model was unavailable",
    )
    assert "staff" in heard[0]


def test_an_llm_outage_in_the_middle_of_a_booking_frees_the_hold_when_staff_take_over(
    backend: httpx.Client,
) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": "open"}).status_code == 200
    scripted = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "when": "2026-11-03"}),
            Say("I can do {offered[0]}."),
            UseTool("place_hold", {"option": 1}),
            Say("Held."),
        ]
    )
    llm = FailingLLM(scripted, fail_times=0)
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow")
    call.hear("The first one")
    llm.__init__(fail_times=None)  # type: ignore[misc]  # the outage starts here

    call.hear("Is it confirmed?")
    call.hear("Yes please")

    factory = cast(Any, backend).app.state.session_factory
    assert read_final_state(factory, START_OF_TEST).live_holds == 0
    assert len(escalations(backend)) == 1


def test_a_caller_who_declines_the_offer_is_served_again_when_the_llm_is_back(
    backend: httpx.Client,
) -> None:
    llm = FailingLLM(ScriptedLLM([Say("We open at nine.")]), fail_times=2)
    call = open_text_call(backend, llm=llm)
    call.hear("When do you open?")

    heard = call.hear("No thanks, just tell me when you open")

    assert heard == ["We open at nine."]
    assert escalations(backend) == []


def test_an_emergency_gets_the_script_and_a_record_with_the_llm_down(
    backend: httpx.Client,
) -> None:
    llm = FailingLLM(fail_times=None)
    call = open_text_call(backend, llm=llm)

    heard = call.hear("My father collapsed and he is not breathing")

    assert " ".join(heard) == EMERGENCY_SCRIPT
    assert llm.requests == 0
    [stored] = escalations(backend)
    assert stored["reason"].startswith("Emergency")


def test_an_urgent_case_is_triaged_to_staff_with_the_llm_down(backend: httpx.Client) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": "open"}).status_code == 200
    llm = FailingLLM(fail_times=None)
    call = open_text_call(backend, llm=llm)

    heard = call.hear("I knocked out a tooth and it's urgent, my number is 98450 12345")

    assert llm.requests == 0
    [stored] = escalations(backend)
    assert stored["reason"] == URGENT_REASON
    assert "staff" in heard[0]


class RaisingMessages:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def create(self, **_: Any) -> Any:
        raise self._error


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.invalid")),
        anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.invalid")),
    ],
)
def test_the_real_adapter_turns_provider_errors_into_llm_errors(error: Exception) -> None:
    client = type("Client", (), {"messages": RaisingMessages(error)})()
    llm = AnthropicLLM(client, "a-model", 5.0)

    with pytest.raises(LLMError):
        llm.next_step(LLMTurn({}, [], []))
