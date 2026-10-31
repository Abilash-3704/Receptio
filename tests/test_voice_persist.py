"""Voice latency lines are stored with the Call as well as logged: one path to the same database
the text-mode Calls use. No LiveKit, no network."""

import json
from collections.abc import Callable
from typing import Any

import httpx

from agent.llm import Say
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call
from voice.persist import storing
from voice.timing import TurnTimings

Trace = Callable[[int], dict[str, Any]]


def line(turn: int, total: int | None, caller_turn: int | None) -> str:
    return json.dumps(
        {
            "event": "turn_timing",
            "call_id": 1,
            "turn": turn,
            "caller_turn": caller_turn,
            "asr_final_ms": -20,
            "llm_first_token_ms": 300,
            "tts_first_audio_ms": None if total is None else total - 300,
            "total_ms": total,
            "total_from_speech_end_ms": None if total is None else total + 20,
            "interrupted": total is None,
        }
    )


def test_each_timing_line_is_stored_against_its_caller_turn(
    backend: httpx.Client, trace: Trace
) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([Say("Hi.")]), mode="voice")
    call.hear("Hello")
    logged: list[str] = []

    emit = storing(backend, call.call_id, logged.append)
    emit(line(1, 900, caller_turn=2))

    assert logged == [line(1, 900, caller_turn=2)]  # still logged as before
    turn = trace(call.call_id)["turns"][1]
    assert turn["latency"]["total_ms"] == 900
    assert turn["latency"]["interrupted"] is False


def test_an_interrupted_turn_is_stored_without_audio_figures(
    backend: httpx.Client, trace: Trace
) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([Say("Hi.")]), mode="voice")
    call.hear("Hello")

    storing(backend, call.call_id, lambda _: None)(line(1, None, caller_turn=2))

    latency = trace(call.call_id)["turns"][1]["latency"]
    assert (latency["total_ms"], latency["interrupted"]) == (None, True)


def test_a_failed_store_never_breaks_the_voice_loop(backend: httpx.Client) -> None:
    logged: list[str] = []

    emit = storing(backend, 999_999, logged.append)  # no such Call: the backend answers 404
    emit(line(1, 900, caller_turn=2))

    assert logged == [line(1, 900, caller_turn=2)]


def test_a_real_turn_timings_object_can_feed_the_sink(backend: httpx.Client, trace: Trace) -> None:
    call = open_text_call(backend, llm=ScriptedLLM([Say("Hi.")]), mode="voice")
    call.hear("Hello")
    timings = TurnTimings(call.call_id, emit=storing(backend, call.call_id, lambda _: None))
    timings.on_speech_end(0.0)
    timings.on_asr_final(0.1)
    timings.on_core_turn(call.caller_turn_seq)
    timings.on_llm_first_token(0.5)
    timings.on_tts_first_audio(0.9)

    assert trace(call.call_id)["turns"][1]["latency"]["total_ms"] == 800
