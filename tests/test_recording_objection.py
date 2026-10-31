"""A Caller who objects to recording is offered a Callback Request instead of a Call, and nothing
more of the Call is recorded. Decided in code before the LLM (an empty script proves it)."""

from typing import Any

import httpx
import pytest

from agent.llm import Say
from agent.recording import objects_to_recording
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import DISCLOSURE, open_text_call


def stored_turns(backend: httpx.Client, call_id: int) -> list[dict[str, str]]:
    return backend.get(f"/calls/{call_id}").json()["transcript"]


def escalations(backend: httpx.Client) -> list[dict[str, Any]]:
    return backend.get("/escalations").json()


@pytest.mark.parametrize(
    "text",
    [
        "I don't want to be recorded",
        "I do not consent to the call being recorded",
        "please stop recording",
        "Stop the recording",
        "I object to the recording",
        "I'm not comfortable with this being recorded",
        "don't record me",
        "no recording please",
        "I would rather not be recorded",
    ],
)
def test_objections_are_recognised(text: str) -> None:
    assert objects_to_recording(text)


@pytest.mark.parametrize(
    "text",
    [
        "Is this call recorded?",
        "That's fine, I know it may be recorded",
        "I'd like a cleaning on Tuesday",
        "I want to record a voicemail for my dentist",
        "my medical records are in the file",
    ],
)
def test_ordinary_mentions_are_not_objections(text: str) -> None:
    assert not objects_to_recording(text)


def test_an_objection_leads_to_a_callback_request_even_during_clinic_hours(
    backend: httpx.Client,
) -> None:
    backend.put("/demo/clinic-override", json={"mode": "open"})
    call = open_text_call(backend, llm=ScriptedLLM([]))  # the LLM is never asked

    asked = call.hear("I don't want to be recorded")
    assert escalations(backend) == []
    assert "number" in asked[0]
    heard = call.hear("call me on 98450 12345")

    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "9845012345")
    assert "objected" in stored["reason"] and "recorded" in stored["reason"]
    assert "call you back" in heard[0]


def test_nothing_is_recorded_after_the_objection(backend: httpx.Client) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([]))
    call.hear("hello, I'd rather not be recorded")
    call.hear("98450 12345")

    # Only what was said before the objection (the disclosure) is stored: not the objection,
    # not the phone number, not the agent's replies.
    assert stored_turns(backend, call.call_id) == [{"speaker": "agent", "text": DISCLOSURE}]
    assert backend.get(f"/calls/{call.call_id}").json()["status"] in ("open", "ended")


def test_a_booking_conversation_is_recorded_normally(backend: httpx.Client) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([Say("Sure.")]))

    call.hear("Is this call recorded?")

    assert [t["speaker"] for t in stored_turns(backend, call.call_id)] == [
        "agent",
        "caller",
        "agent",
    ]


def test_an_emergency_is_still_handled_when_the_caller_also_objects(
    backend: httpx.Client,
) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([]))

    heard = call.hear("I don't want to be recorded but my father is not breathing")

    assert "112" in heard[0]
    [stored] = escalations(backend)
    assert "emergency" in stored["reason"].lower()
