"""The voice turn driver: what the voice pipeline does with the agent core for one Caller turn.
Same TextCall as text mode, scripted LLM, real backend; no LiveKit and no network."""

import asyncio
import json
from typing import Any

import httpx

from agent.llm import Say, UseTool
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
    assert lines[0]["llm_first_token_ms"] == 150  # first text at 0.25 s, ASR final at 0.1 s


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


def test_the_timing_line_names_the_transcript_turn_it_belongs_to(backend: httpx.Client) -> None:
    driver, timings, lines = make(backend, [Say("Hello there.")])
    timings.on_speech_end(0.0)
    timings.on_asr_final(0.1)

    collect(driver, "Hi")
    timings.on_tts_first_audio(5.0)

    assert lines[0]["caller_turn"] == 2  # the disclosure is turn 1, the Caller's "Hi" turn 2


def test_driver_passes_the_core_turn_profile_to_the_timings() -> None:
    import asyncio
    from typing import Any

    from agent.text_mode import TurnProfile
    from voice.driver import VoiceTurnDriver

    seen: dict[str, Any] = {}

    class Timings:
        def on_core_turn(self, seq: int) -> None: ...
        def on_llm_first_token(self, t: float) -> None: ...
        def on_core_start(self) -> None: ...
        def on_answer_text(self, t: float) -> None: ...
        def on_core_done(self) -> None: ...
        def on_core_profile(self, **kw: Any) -> None:
            seen.update(kw)

    class Call:
        call_id = 1
        caller_turn_seq = 2
        last_turn_profile = TurnProfile(llm_steps=2, llm_ms=1500, tool_ms=200, tools=["read_back"])

        def hear(self, text: str, on_speak: Any = None) -> list[str]:
            return ["Hello there."]

    async def run() -> None:
        driver = VoiceTurnDriver(Call(), Timings())  # type: ignore[arg-type]
        async for _ in driver.reply("hi"):
            pass

    asyncio.run(run())

    assert seen == {"llm_steps": 2, "llm_ms": 1500, "tool_ms": 200, "tools": ["read_back"]}


def test_the_filler_is_handed_over_as_its_own_segment_before_the_answer(
    backend: httpx.Client,
) -> None:
    from agent.llm import UseTool

    llm = ScriptedLLM(
        [UseTool("search_faq", {"question": "when are you open"}), Say("We open at nine.")]
    )
    call = open_text_call(backend, llm=llm)
    driver = VoiceTurnDriver(call, TurnTimings(call.call_id, emit=lambda _line: None))

    async def run() -> list[list[str]]:
        return [segment async for segment in driver.segments("When do you open?")]

    assert asyncio.run(run()) == [["Let me check that for you."], ["We open at nine."]]


def test_a_filler_turn_is_written_when_the_answer_is_ready_with_both_times(
    backend: httpx.Client,
) -> None:
    llm = ScriptedLLM([UseTool("search_faq", {"question": "hours"}), Say("We open at nine.")])
    call = open_text_call(backend, llm=llm)
    lines: list[dict[str, Any]] = []
    timings = TurnTimings(call.call_id, emit=lambda line: lines.append(json.loads(line)))
    driver = VoiceTurnDriver(call, timings, Ticking())
    timings.on_asr_final(0.0)

    async def run() -> None:
        async for segment in driver.segments("When do you open?"):
            if len(segment) and segment[0].startswith("Let me"):
                timings.on_tts_first_audio(0.5)  # the filler is heard; the core is still working

    asyncio.run(run())

    assert len(lines) == 1
    assert lines[0]["total_ms"] == 500
    assert lines[0]["answer_ready_ms"] > lines[0]["total_ms"]
