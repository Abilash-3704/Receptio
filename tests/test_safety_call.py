"""Seam 1 for the safety layer: a text-mode Call through the real agent core and the real backend,
scripted LLM. The detector is code, so most scripts below are empty: an empty script proves the
LLM was never consulted. Assertions: what the Caller hears and what is stored."""

from typing import Any

import httpx
import pytest

from agent.call_state import Offer
from agent.llm import Say, UseTool
from agent.safety import EMERGENCY_SCRIPT, EMERGENCY_SCRIPT_VERSION, URGENT_HANDOFF_SPEECH
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import TextCall, open_text_call
from tests.support import IST, availability, confirm, local


def escalations(backend: httpx.Client) -> list[dict[str, Any]]:
    return backend.get("/escalations").json()


def set_clinic(backend: httpx.Client, mode: str) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": mode}).status_code == 200


def mid_booking_call(backend: httpx.Client) -> tuple[TextCall, Offer]:
    """A Call with a live Hold on a Cleaning; also returns the held start time."""
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "date": "2026-11-03"}),
            Say("I can do {offered[0]}. Which suits?"),
            UseTool("place_hold", {"option": 1}),
            Say("Held. Shall I read it back?"),
        ]
    )
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow")
    call.hear("The first one")
    hold = call._state.hold  # pyright: ignore[reportPrivateUsage]
    assert hold is not None
    return call, hold.offer


def hold_blocks_time(backend: httpx.Client, offer: Offer) -> bool:
    start = offer.start.astimezone(IST)
    day = start.date().isoformat()
    free = local(availability(backend, "Cleaning", day, provider=offer.provider))
    return start.strftime("%Y-%m-%d %H:%M") not in free


def test_an_emergency_hears_only_the_fixed_script_and_starts_a_handoff(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "open")
    call = open_text_call(backend, llm=ScriptedLLM([]))  # the LLM is never asked

    heard = call.hear("I can't breathe and my face is swelling")

    assert heard == [EMERGENCY_SCRIPT]
    [stored] = escalations(backend)
    assert (stored["call_id"], stored["kind"]) == (call.call_id, "handoff")
    assert stored["reason"].startswith("Emergency")


def test_the_emergency_script_is_the_spec_text_and_versioned() -> None:
    assert EMERGENCY_SCRIPT == (
        "This sounds like a medical emergency. Please end this call and call 112 right now, or "
        "go to your nearest emergency room. I'm alerting our clinic staff as well."
    )
    assert EMERGENCY_SCRIPT_VERSION == "emergency-script-v1"


def test_an_emergency_outside_clinic_hours_hears_the_script_then_a_callback_request_is_recorded(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "closed")
    call = open_text_call(backend, llm=ScriptedLLM([]))

    heard = call.hear("My dad has collapsed, call me on 98450 12345")

    assert heard == [EMERGENCY_SCRIPT]
    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "9845012345")
    assert stored["reason"].startswith("Emergency")


def test_an_emergency_is_recorded_even_when_the_caller_gave_no_number(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "closed")
    call = open_text_call(backend, llm=ScriptedLLM([]))

    heard = call.hear("I can't stop the bleeding")

    assert heard == [EMERGENCY_SCRIPT]  # nothing else is said on this turn, not even a question
    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", None)


def test_an_emergency_mid_booking_abandons_the_flow_and_releases_the_hold(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "open")
    call, offer = mid_booking_call(backend)
    assert hold_blocks_time(backend, offer)

    heard = call.hear("Wait, I can't breathe and my face is swelling badly")

    assert heard == [EMERGENCY_SCRIPT]
    assert not hold_blocks_time(backend, offer)
    assert call.hear("ok go ahead and book it") == []  # the LLM is not asked: script exhausted
    assert backend.get("/escalations").json()[0]["reason"].startswith("Emergency")
    assert [t.name for t in call.tool_calls] == ["check_availability", "place_hold"]


def test_an_emergency_cannot_be_confirmed_after_the_read_back_was_agreed(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "open")
    llm = ScriptedLLM(
        [
            UseTool("check_availability", {"service": "Cleaning", "date": "2026-11-03"}),
            Say("I can do {offered[0]}."),
            UseTool("record_patient", {"name": "Asha Menon", "phone": "+91 98450 12345"}),
            UseTool("place_hold", {"option": 1}),
            UseTool("read_back", {}),
            Say("Thank you, one moment."),
        ]
    )
    call = open_text_call(backend, llm=llm)
    call.hear("A cleaning tomorrow")
    call.hear("Asha Menon, 98450 12345, the first")
    call.hear("Yes")  # the Caller agreed to the read-back
    hold = call._state.hold  # pyright: ignore[reportPrivateUsage]
    assert hold is not None and call._state.read_back_agreed  # pyright: ignore

    heard = call.hear("Oh no my husband has stopped breathing")

    assert heard == [EMERGENCY_SCRIPT]
    assert backend.get("/escalations").json()
    assert confirm(backend, hold.id).status_code == 409  # released: it can no longer be confirmed


def test_an_urgent_case_gets_an_escalation_and_no_112_script(backend: httpx.Client) -> None:
    set_clinic(backend, "open")
    call = open_text_call(backend, llm=ScriptedLLM([]))

    heard = call.hear(
        "A tooth got knocked out playing cricket an hour ago. It's no big deal, "
        "I just want an appointment."
    )

    assert heard == [URGENT_HANDOFF_SPEECH]
    assert "112" not in heard[0]
    [stored] = escalations(backend)
    assert (stored["kind"], stored["reason"].startswith("Urgent Case")) == ("handoff", True)
    assert call.tool_calls == []


def test_an_urgent_case_outside_clinic_hours_becomes_a_callback_request(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "closed")
    call = open_text_call(backend, llm=ScriptedLLM([]))

    asked = call.hear("I'm in severe pain")
    assert "number" in asked[0] and EMERGENCY_SCRIPT not in asked
    heard = call.hear("It's 98450 12345")

    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "9845012345")
    assert stored["reason"].startswith("Urgent Case")
    assert "call you back" in heard[0]


def test_an_urgent_case_mid_booking_releases_the_hold_and_books_nothing(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "open")
    call, offer = mid_booking_call(backend)

    call.hear("Actually my tooth is cracked and the pain is unbearable")

    assert not hold_blocks_time(backend, offer)
    assert call.hear("so can I take the 10 o'clock?") == []
    assert [t.name for t in call.tool_calls] == ["check_availability", "place_hold"]


def test_an_emergency_after_a_plain_handoff_still_hears_the_script(backend: httpx.Client) -> None:
    set_clinic(backend, "open")
    call = open_text_call(backend, llm=ScriptedLLM([]))
    call.hear("Let me talk to a human")
    assert call.hear("hello?") == []  # with staff now

    heard = call.hear("I can't breathe")

    assert heard == [EMERGENCY_SCRIPT]
    assert [e["reason"].startswith("Emergency") for e in escalations(backend)] == [False, True]


def test_an_emergency_while_a_callback_number_is_awaited_hears_the_script(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "closed")
    call = open_text_call(backend, llm=ScriptedLLM([]))
    call.hear("I want a real person")  # asks for a number

    heard = call.hear("I'm choking")

    assert heard == [EMERGENCY_SCRIPT]
    assert escalations(backend)[-1]["reason"].startswith("Emergency")


def test_a_repeated_emergency_repeats_the_script_but_escalates_once(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "open")
    call = open_text_call(backend, llm=ScriptedLLM([]))
    call.hear("I can't breathe")

    assert call.hear("I still can't breathe") == [EMERGENCY_SCRIPT]
    assert len(escalations(backend)) == 1


def test_an_urgent_caller_can_still_give_the_number_after_more_pain_talk(
    backend: httpx.Client,
) -> None:
    set_clinic(backend, "closed")
    call = open_text_call(backend, llm=ScriptedLLM([]))
    call.hear("My tooth got knocked out")
    call.hear("It really hurts, the pain is unbearable")  # not a number, and not a new Escalation

    call.hear("98450 12345")

    [stored] = escalations(backend)
    assert stored["caller_phone"] == "9845012345"


@pytest.mark.parametrize(
    "text",
    ["I have an emergency meeting at 5, can we do 6", "My teeth are a bit sensitive to cold"],
)
def test_ordinary_talk_reaches_the_llm_and_starts_no_escalation(
    backend: httpx.Client, text: str
) -> None:
    set_clinic(backend, "open")
    call = open_text_call(backend, llm=ScriptedLLM([Say("Of course, what service?")]))

    assert call.hear(text) == ["Of course, what service?"]
    assert escalations(backend) == []
