"""The voice turn driver: what the voice pipeline does with the agent core for one Caller turn.
Same TextCall as text mode, scripted LLM, real backend; no LiveKit and no network."""

import asyncio
import json
from typing import Any

import httpx

from agent.llm import Say
from agent.safety import EMERGENCY_SCRIPT
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import DISCLOSURE, open_text_call
from voice.driver import VoiceTurnDriver
from voice.timing import TurnTimings


class Ticking:
    """A clock that advances 0.25 s each time it is read."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.25
        return self.now


def collect(driver: VoiceTurnDriver, text: str) -> list[str]:
    async def run() -> list[str]:
        return [sentence async for sentence in driver.reply(text)]

    return asyncio.run(run())


def make(
    backend: httpx.Client, steps: list[Say]
) -> tuple[VoiceTurnDriver, TurnTimings, list[dict[str, Any]]]:
    lines: list[dict[str, Any]] = []
    call = open_text_call(backend, llm=ScriptedLLM(list(steps)))
    timings = TurnTimings(call.call_id, emit=lambda line: lines.append(json.loads(line)))
    return VoiceTurnDriver(call, timings, Ticking()), timings, lines


def test_a_turn_streams_the_agents_sentences_and_marks_the_first_text(
    backend: httpx.Client,
) -> None:
    driver, timings, lines = make(backend, [Say("We open at nine. Shall I book you in?")])
    timings.on_speech_end(0.0)
    timings.on_asr_final(0.1)

    spoken = collect(driver, "When do you open?")
    timings.on_tts_first_audio(5.0)

    assert spoken == ["We open at nine.", "Shall I book you in?"]
    assert lines[0]["llm_first_token_ms"] == 250  # clock read once, when the first sentence left


def test_the_safety_detector_still_runs_first_in_voice(backend: httpx.Client) -> None:
    # An empty script: any LLM call would raise, so passing proves the LLM was never consulted.
    driver, _, _ = make(backend, [])

    spoken = collect(driver, "My father collapsed and he is not breathing")

    assert " ".join(spoken) == EMERGENCY_SCRIPT  # the fixed script, word for word
    escalations: list[dict[str, Any]] = backend.get("/escalations").json()
    assert len(escalations) == 1


def test_the_disclosure_is_the_first_stored_turn_and_ending_closes_the_call(
    backend: httpx.Client,
) -> None:
    driver, _, _ = make(backend, [Say("Hello.")])
    collect(driver, "Hi")
    driver.end()

    record: dict[str, Any] = backend.get(f"/calls/{driver.call_id}").json()
    first = record["transcript"][0]
    assert (first["speaker"], first["text"]) == ("agent", DISCLOSURE)
    assert record["status"] == "ended"
