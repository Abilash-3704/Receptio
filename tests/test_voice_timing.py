"""Per-turn timing record: the pure logic behind the voice loop's latency log (no LiveKit)."""

import json
from typing import Any

from voice.timing import TurnTimings


def make() -> tuple[TurnTimings, list[dict[str, Any]]]:
    lines: list[dict[str, Any]] = []
    timings = TurnTimings(call_id=7, emit=lambda line: lines.append(json.loads(line)))
    return timings, lines


def test_a_completed_turn_logs_cumulative_stage_times_from_end_of_speech() -> None:
    timings, lines = make()
    timings.on_speech_end(10.000)
    timings.on_asr_final(10.250)
    timings.on_llm_first_token(10.900)
    timings.on_tts_first_audio(11.150)

    assert len(lines) == 1
    line = lines[0]
    assert line["event"] == "turn_timing"
    assert line["call_id"] == 7
    assert line["turn"] == 1
    assert line["asr_final_ms"] == 250
    assert line["llm_first_token_ms"] == 900
    assert line["tts_first_audio_ms"] == 1150
    assert line["total_ms"] == 1150  # end of speech to first agent audio
    assert line["since_asr_final_ms"] == 900  # ASR final to first agent audio
    assert line["interrupted"] is False


def test_turns_are_numbered_and_logged_once_each() -> None:
    timings, lines = make()
    for start in (0.0, 5.0):
        timings.on_speech_end(start)
        timings.on_asr_final(start + 0.1)
        timings.on_llm_first_token(start + 0.5)
        timings.on_tts_first_audio(start + 0.7)
        timings.on_tts_first_audio(start + 0.8)  # later audio frames of the same reply: ignored

    assert [line["turn"] for line in lines] == [1, 2]
    assert [line["total_ms"] for line in lines] == [700, 700]


def test_the_last_asr_final_before_the_llm_answers_is_the_one_timed() -> None:
    timings, lines = make()
    timings.on_speech_end(1.0)
    timings.on_asr_final(1.2)
    timings.on_asr_final(1.4)  # the recogniser sent a second final for the same turn
    timings.on_llm_first_token(1.9)
    timings.on_asr_final(2.5)  # after the LLM started: not part of this turn's timing
    timings.on_tts_first_audio(2.0)

    assert lines[0]["asr_final_ms"] == 400


def test_audio_with_no_caller_turn_pending_is_not_a_turn() -> None:
    timings, lines = make()
    timings.on_tts_first_audio(1.0)  # the disclosure, spoken before the Caller says anything
    assert lines == []


def test_a_turn_cut_off_by_the_caller_before_any_audio_is_logged_as_interrupted() -> None:
    timings, lines = make()
    timings.on_speech_end(1.0)
    timings.on_asr_final(1.3)
    timings.on_llm_first_token(1.8)
    timings.on_user_speech_start(2.0)  # the Caller talks again before the agent made a sound

    assert len(lines) == 1
    assert lines[0]["interrupted"] is True
    assert lines[0]["asr_final_ms"] == 300
    assert lines[0]["llm_first_token_ms"] == 800
    assert lines[0]["tts_first_audio_ms"] is None
    assert lines[0]["total_ms"] is None


def test_speech_start_after_a_completed_turn_logs_nothing_more() -> None:
    timings, lines = make()
    timings.on_speech_end(0.0)
    timings.on_asr_final(0.1)
    timings.on_llm_first_token(0.2)
    timings.on_tts_first_audio(0.3)
    timings.on_user_speech_start(5.0)  # barge-in over the reply, or the next question
    timings.close()

    assert len(lines) == 1


def test_missing_end_of_speech_falls_back_to_the_asr_final() -> None:
    timings, lines = make()
    timings.on_asr_final(3.0)
    timings.on_llm_first_token(3.4)
    timings.on_tts_first_audio(3.6)

    assert lines[0]["asr_final_ms"] == 0
    assert lines[0]["total_ms"] == 600


def test_closing_mid_turn_logs_the_unfinished_turn() -> None:
    timings, lines = make()
    timings.on_speech_end(1.0)
    timings.on_asr_final(1.1)
    timings.close()

    assert lines[0]["total_ms"] is None
    assert lines[0]["interrupted"] is True
