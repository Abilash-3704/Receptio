"""Seam: a text-mode Call through the real agent core and backend, then the outcome stored when the
Call ends. Dropped means the Caller left (or never spoke) mid-flow with nothing confirmed."""

import httpx

from agent.llm import Say, Step, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import TextCall, open_text_call
from tests.support import FakeClock

PATIENT = UseTool("record_patient", {"name": "Priya Sharma", "phone": "+91 98765 43210"})
TO_READ_BACK: list[Step] = [
    UseTool("check_availability", {"service": "Cleaning", "when": "2026-11-03"}),
    Say("I can do {offered[0]}. Does that suit you?"),
    PATIENT,
    UseTool("place_hold", {"option": 1}),
    UseTool("read_back", {}),
]


def stored(backend: httpx.Client, call: TextCall) -> tuple[str, str | None]:
    record = backend.get(f"/calls/{call.call_id}").json()
    return record["outcome"], record["outcome_reason"]


def booking_up_to_read_back(backend: httpx.Client, clock: FakeClock, tail: list[Step]) -> TextCall:
    call = open_text_call(backend, llm=ScriptedLLM([*TO_READ_BACK, *tail]), clock=clock)
    call.hear("A cleaning tomorrow please")
    call.hear("That works, I'm Priya Sharma, +91 98765 43210")
    return call


def test_a_caller_who_never_speaks_is_a_dropped_call(backend: httpx.Client) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([]))

    call.end()

    assert stored(backend, call) == ("dropped", "caller silent: no speech")


def test_hanging_up_after_the_read_back_without_a_yes_is_a_dropped_call(
    backend: httpx.Client, clock: FakeClock
) -> None:
    call = booking_up_to_read_back(backend, clock, [])

    call.end()

    assert stored(backend, call) == (
        "dropped",
        "caller left before confirming: Hold read back, no answer",
    )


def test_hanging_up_while_times_are_on_offer_is_a_dropped_call(
    backend: httpx.Client, clock: FakeClock
) -> None:
    llm = ScriptedLLM(TO_READ_BACK[:2])
    call = open_text_call(backend, llm=llm, clock=clock)
    call.hear("A cleaning tomorrow please")

    call.end()

    assert stored(backend, call) == (
        "dropped",
        "caller left before confirming: times offered, no Hold",
    )


def test_a_confirmed_booking_is_a_completed_call(backend: httpx.Client, clock: FakeClock) -> None:
    call = booking_up_to_read_back(
        backend, clock, [UseTool("confirm_appointment", {}), Say("You're booked.")]
    )
    call.hear("Yes")

    call.end()

    assert stored(backend, call) == ("completed", None)


def test_an_answered_question_then_hanging_up_is_a_completed_call(backend: httpx.Client) -> None:
    llm = ScriptedLLM([UseTool("search_faq", {"question": "where are you"}), Say("Bengaluru.")])
    call = open_text_call(backend, llm=llm)
    call.hear("Where are you?")

    call.end()

    assert stored(backend, call) == ("completed", None)


def test_a_call_passed_to_staff_is_escalated_with_the_reason(backend: httpx.Client) -> None:
    backend.put("/demo/clinic-override", json={"mode": "open"})
    call = open_text_call(backend, llm=ScriptedLLM([]))
    call.hear("I want to talk to a human")

    call.end()

    assert stored(backend, call) == ("escalated", "caller asked for a human")


def test_with_auto_read_back_place_hold_is_followed_by_the_read_back_without_asking_the_llm(
    backend: httpx.Client, clock: FakeClock
) -> None:
    # No UseTool("read_back") in the script: asking the LLM for another step would exhaust it.
    llm = ScriptedLLM([*TO_READ_BACK[:-1], UseTool("confirm_appointment", {}), Say("Booked.")])
    call = open_text_call(backend, llm=llm, clock=clock, auto_read_back=True)
    call.hear("A cleaning tomorrow please")

    spoken = call.hear("That works, I'm Priya Sharma, +91 98765 43210")

    assert "Let me read that back" in spoken[-1] and "Priya Sharma" in spoken[-1]
    assert [t.name for t in call.tool_calls][-2:] == ["place_hold", "read_back"]
    assert call.last_turn_profile.llm_steps == 2  # record_patient, place_hold: no third round trip
    # The state machine is waiting for the Caller's answer, so the confirm is still gated on it.
    assert call.hear("Yes, that's right") == ["Booked."]


def test_a_tool_call_written_out_as_text_is_never_spoken_and_the_llm_is_told(
    backend: httpx.Client, clock: FakeClock
) -> None:
    llm = ScriptedLLM(
        [
            Say('(tool call) {{"tool": "place_hold", "args": {{"option": 2}}}}'),
            Say("Which service would you like?"),
        ]
    )
    call = open_text_call(backend, llm=llm, clock=clock)

    spoken = call.hear("Hello")

    assert spoken == ["Which service would you like?"]
    assert all("tool" not in t.text.lower() for t in call.transcript[1:])


def test_in_voice_the_code_says_a_fixed_phrase_while_a_lookup_runs_and_the_model_says_nothing_then(
    backend: httpx.Client,
) -> None:
    llm = ScriptedLLM([UseTool("search_faq", {"question": "hours"}), Say("We open at nine.")])
    call = open_text_call(backend, llm=llm)
    heard: list[str] = []

    spoken = call.hear("When do you open?", heard.append)

    assert heard == ["Let me check that for you.", "We open at nine."]  # in this order, live
    assert spoken == heard  # and stored: what the Caller heard is what the transcript holds


def test_without_a_voice_pipeline_there_is_no_filler(backend: httpx.Client) -> None:
    llm = ScriptedLLM([UseTool("search_faq", {"question": "hours"}), Say("We open at nine.")])
    call = open_text_call(backend, llm=llm)

    assert call.hear("When do you open?") == ["We open at nine."]


def test_a_tool_that_speaks_for_itself_gets_no_filler(
    backend: httpx.Client, clock: FakeClock
) -> None:
    llm = ScriptedLLM([*TO_READ_BACK[:-1]])  # ends with place_hold, then the chained read_back
    call = open_text_call(backend, llm=llm, clock=clock, auto_read_back=True)
    call.hear("A cleaning tomorrow please")
    heard: list[str] = []

    call.hear("That works, I'm Priya Sharma, +91 98765 43210", heard.append)

    assert all("check" not in text.lower() for text in heard[:-1])  # only the read-back is said
    assert heard[-1].startswith("Let me read that back")


def test_recording_the_patient_gets_a_filler_too(backend: httpx.Client, clock: FakeClock) -> None:
    llm = ScriptedLLM([*TO_READ_BACK[:3], Say("Thanks.")])
    call = open_text_call(backend, llm=llm, clock=clock)
    call.hear("A cleaning tomorrow please")
    heard: list[str] = []

    call.hear("That works, I'm Priya Sharma, +91 98765 43210", heard.append)

    assert heard[0] == "One moment."
