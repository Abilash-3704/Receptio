"""Failure injection, voice side (ticket 19): speech-recognition dropout, speech-synthesis failure
and mid-Call disconnect, through the voice driver with stand-ins for the providers. No LiveKit, no
Deepgram, no Cartesia: what these show is the agent's handling, not how the real providers fail."""

import asyncio
from datetime import timedelta
from typing import Any, cast

import httpx

from agent.failure_injection import FlakyBackend
from agent.llm import Say, Step, UseTool
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call
from backend.app.scheduling import HOLD_DURATION
from evals.state import read_final_state
from tests.support import START_OF_TEST, FakeClock, availability
from voice.driver import VoiceTurnDriver
from voice.failure_injection import FakeSpeaker
from voice.resilience import TTS_APOLOGY, AsrDropoutWatch, VoiceResilience
from voice.timing import TurnTimings

TO_HOLD: list[Step] = [
    UseTool("check_availability", {"service": "Cleaning", "when": "2026-11-03"}),
    Say("I can do {offered[0]}. Does that suit you?"),
    UseTool("record_patient", {"name": "Priya Sharma", "phone": "+91 98765 43210"}),
    UseTool("place_hold", {"option": 1}),
    UseTool("read_back", {}),
]


def driver_for(
    backend: httpx.Client, steps: list[Step], clock: FakeClock | None = None
) -> VoiceTurnDriver:
    call = open_text_call(backend, llm=ScriptedLLM(steps), clock=clock or FakeClock(START_OF_TEST))
    return VoiceTurnDriver(call, TurnTimings(call.call_id, emit=lambda _: None))


def collect(driver: VoiceTurnDriver, text: str) -> list[str]:
    async def run() -> list[str]:
        return [s async for s in driver.reply(text)]

    return asyncio.run(run())


def escalations(backend: httpx.Client) -> list[dict[str, Any]]:
    return backend.get("/escalations").json()


def call_record(backend: httpx.Client, driver: VoiceTurnDriver) -> dict[str, Any]:
    return backend.get(f"/calls/{driver.call_id}").json()


# --- speech-recognition dropout ---


def test_a_first_dropout_is_an_apology_and_a_request_to_repeat() -> None:
    watch = AsrDropoutWatch(min_speech_seconds=1.0, grace_seconds=4.0)
    watch.on_speech_start(10.0)
    watch.on_speech_end(13.0)  # three seconds of speech, and no text

    assert not watch.due(15.0)
    assert watch.due(17.0)
    assert not watch.due(18.0)  # once per utterance


def test_a_noise_or_a_recognised_utterance_is_not_a_dropout() -> None:
    watch = AsrDropoutWatch(min_speech_seconds=1.0, grace_seconds=4.0)
    watch.on_speech_start(1.0)
    watch.on_speech_end(1.4)  # a cough
    watch.on_speech_start(5.0)
    watch.on_speech_end(8.0)
    watch.on_text()  # recognised in time

    assert not watch.due(60.0)


def test_two_dropouts_in_a_row_apologise_then_offer_staff_and_a_yes_reaches_them(
    backend: httpx.Client,
) -> None:
    assert backend.put("/demo/clinic-override", json={"mode": "open"}).status_code == 200
    driver = driver_for(backend, [])  # the LLM is never asked
    speaker = FakeSpeaker()
    voice = VoiceResilience(driver, speaker.speak, speaker.close)

    asyncio.run(voice.on_asr_dropout())
    asyncio.run(voice.on_asr_dropout())
    heard = collect(driver, "Yes please")

    assert "didn't catch that" in speaker.spoken[0]
    assert "staff" in speaker.spoken[1]
    [stored] = escalations(backend)
    assert stored["kind"] == "handoff" and "could not hear" in stored["reason"]
    assert "staff" in " ".join(heard)
    turns = [t["text"] for t in call_record(backend, driver)["transcript"] if t["speaker"] == "agent"]
    assert speaker.spoken[0] in turns  # what the Caller heard is in the stored Call


def test_a_heard_turn_resets_the_dropout_count(backend: httpx.Client) -> None:
    driver = driver_for(backend, [Say("We open at nine.")])
    speaker = FakeSpeaker()
    voice = VoiceResilience(driver, speaker.speak, speaker.close)
    asyncio.run(voice.on_asr_dropout())
    collect(driver, "When do you open?")

    asyncio.run(voice.on_asr_dropout())

    assert all("staff" not in s for s in speaker.spoken)  # a first dropout again, not a second
    assert escalations(backend) == []


# --- speech-synthesis failure ---


def test_a_synthesis_failure_that_clears_on_retry_costs_the_caller_an_apology(
    backend: httpx.Client,
) -> None:
    driver = driver_for(backend, [])
    speaker = FakeSpeaker(fail_times=1)
    voice = VoiceResilience(driver, speaker.speak, speaker.close)

    asyncio.run(voice.on_tts_failure())

    assert speaker.spoken == [TTS_APOLOGY]
    assert speaker.attempts == 2 and speaker.closed == 0
    assert escalations(backend) == []


def test_a_synthesis_failure_that_persists_records_a_callback_request_and_ends_the_call(
    backend: httpx.Client, clock: FakeClock
) -> None:
    driver = driver_for(backend, TO_HOLD, clock)
    collect(driver, "A cleaning tomorrow please")
    collect(driver, "That works, I'm Priya Sharma, +91 98765 43210")
    speaker = FakeSpeaker(fail_times=None)
    voice = VoiceResilience(driver, speaker.speak, speaker.close)

    asyncio.run(voice.on_tts_failure())

    assert speaker.attempts == 2 and speaker.closed == 1  # one retry, then the Call is closed
    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "+919876543210")
    assert "voice failed" in stored["reason"]
    factory = cast(Any, backend).app.state.session_factory
    assert read_final_state(factory, clock()).live_holds == 0  # the Hold went back
    assert voice.voice_lost
    assert asyncio.run(voice.speak_reliably("anything more")) is False
    assert speaker.attempts == 2  # not tried again


# --- mid-Call disconnect ---


def test_a_disconnect_mid_booking_is_dropped_with_a_callback_request_and_no_stuck_hold(
    backend: httpx.Client, clock: FakeClock
) -> None:
    free = [s["start"] for s in availability(backend, "Cleaning", "2026-11-03")]
    driver = driver_for(backend, TO_HOLD, clock)
    collect(driver, "A cleaning tomorrow please")
    collect(driver, "That works, I'm Priya Sharma, +91 98765 43210")

    driver.end()  # the Caller's connection dropped

    record = call_record(backend, driver)
    assert record["outcome"] == "dropped"  # the Callback Request does not change what happened
    assert record["outcome_reason"] == "caller left before confirming: Hold read back, no answer"
    [stored] = escalations(backend)
    assert (stored["kind"], stored["caller_phone"]) == ("callback_request", "+919876543210")
    assert stored["reason"].startswith("call dropped before finishing")
    factory = cast(Any, backend).app.state.session_factory
    assert read_final_state(factory, clock()).confirmed == read_final_state(factory).confirmed
    clock.advance(HOLD_DURATION + timedelta(seconds=1))
    assert read_final_state(factory, clock()).live_holds == 0
    assert [s["start"] for s in availability(backend, "Cleaning", "2026-11-03")] == free


def test_a_disconnect_without_a_number_is_dropped_and_has_nobody_to_ring(
    backend: httpx.Client,
) -> None:
    driver = driver_for(backend, [TO_HOLD[0], TO_HOLD[1]])
    collect(driver, "A cleaning tomorrow please")

    driver.end()

    assert call_record(backend, driver)["outcome"] == "dropped"
    assert escalations(backend) == []


def test_a_disconnect_after_a_finished_booking_is_not_a_dropped_call(
    backend: httpx.Client,
) -> None:
    driver = driver_for(
        backend, [*TO_HOLD, UseTool("confirm_appointment", {}), Say("All booked.")]
    )
    collect(driver, "A cleaning tomorrow please")
    collect(driver, "That works, I'm Priya Sharma, +91 98765 43210")
    collect(driver, "Yes, that's right")

    driver.end()

    assert call_record(backend, driver)["outcome"] == "completed"
    assert escalations(backend) == []


def test_a_disconnect_while_the_backend_is_slow_still_records_the_outcome(
    backend: httpx.Client,
) -> None:
    wrapper = FlakyBackend(backend)
    call = open_text_call(cast(httpx.Client, wrapper), llm=ScriptedLLM([]))
    driver = VoiceTurnDriver(call, TurnTimings(call.call_id, emit=lambda _: None))
    wrapper.fail("POST", "/end", "timeout", times=1)

    driver.end()

    assert call_record(backend, driver)["outcome"] == "dropped"
    assert wrapper.count("POST", "/end") == 2
