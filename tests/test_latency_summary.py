"""p50/p95 over structured turn-timing logs, with synthetic logs only (no providers)."""

import json
import subprocess
import sys
from pathlib import Path

from voice.summary import percentile, render, summarise


def turn(total: int | None, **extra: object) -> str:
    record: dict[str, object] = {
        "event": "turn_timing",
        "call_id": 1,
        "turn": 1,
        "asr_final_ms": 100,
        "llm_first_token_ms": 400,
        "tts_first_audio_ms": None if total is None else total - 400,
        "total_ms": total,
        "total_from_speech_end_ms": None if total is None else total + 100,
        "interrupted": total is None,
    }
    record.update(extra)
    return json.dumps(record)


def test_nearest_rank_percentiles_on_a_known_sample() -> None:
    values = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    assert percentile(values, 50) == 500
    assert percentile(values, 95) == 1000
    assert percentile([40, 10, 30, 20], 50) == 20  # unsorted input
    assert percentile([42], 95) == 42


def test_summary_reports_p50_p95_and_counts_over_a_log() -> None:
    lines = [turn(t) for t in range(100, 2100, 100)]  # 20 turns: 100..2000
    summary = summarise(lines)

    assert summary.turns == 20
    assert summary.completed == 20
    total = summary.stages["total_ms"]
    assert (total.p50, total.p95) == (1000, 1900)  # ranks 10 and 19 of 20
    assert summary.stages["asr_final_ms"].p50 == 100
    assert summary.enough_for_report is True


def test_interrupted_turns_and_foreign_lines_are_counted_but_not_timed() -> None:
    lines = [
        turn(500),
        turn(None),
        "",
        "not json at all",
        json.dumps({"event": "something_else", "total_ms": 99999}),
        turn(700),
    ]
    summary = summarise(lines)

    assert summary.turns == 3
    assert summary.completed == 2
    assert summary.interrupted == 1
    assert summary.skipped_lines == 2  # the malformed one and the foreign event; blanks ignored
    assert summary.stages["total_ms"].p95 == 700
    assert summary.enough_for_report is False  # fewer than 20 completed turns


def test_an_empty_log_summarises_to_nothing_and_renders_a_plain_statement() -> None:
    summary = summarise([])
    assert summary.turns == 0
    assert "no turns" in render(summary).lower()


def test_render_says_when_the_sample_is_too_small_to_trust() -> None:
    text = render(summarise([turn(500), turn(900)]))
    assert "2 completed turns" in text
    assert "fewer than 20" in text
    assert "total_ms" in text


def test_the_script_reads_log_files_and_exits_nonzero_below_the_minimum(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    log.write_text("\n".join(turn(t) for t in (300, 600, 900)) + "\n")
    script = Path(__file__).resolve().parent.parent / "scripts" / "latency_summary"

    ok = subprocess.run(
        [sys.executable, str(script), str(log)], capture_output=True, text=True, check=False
    )
    assert ok.returncode == 0
    assert "3 completed turns" in ok.stdout

    short = subprocess.run(
        [sys.executable, str(script), str(log), "--min-turns", "20"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert short.returncode == 1
