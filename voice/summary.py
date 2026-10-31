"""p50 / p95 over turn-timing log lines (the JSON lines written by voice.timing).

Percentiles use the nearest-rank method (the smallest value with at least p% of the sample at or
below it), so every reported number is a latency that was actually observed."""

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field

STAGES = (
    "asr_final_ms",
    "llm_first_token_ms",
    "tts_first_audio_ms",
    "total_ms",
    "since_asr_final_ms",
)
MIN_TURNS = 20  # the ticket's bar for a latency summary


def percentile(values: list[int], p: float) -> int:
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


@dataclass(frozen=True)
class StageStats:
    n: int
    p50: int
    p95: int


@dataclass
class Summary:
    turns: int = 0
    completed: int = 0
    interrupted: int = 0
    skipped_lines: int = 0
    stages: dict[str, StageStats] = field(default_factory=lambda: {})

    @property
    def enough_for_report(self) -> bool:
        return self.completed >= MIN_TURNS


def summarise(lines: Iterable[str]) -> Summary:
    summary = Summary()
    samples: dict[str, list[int]] = {stage: [] for stage in STAGES}
    for raw in lines:
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            summary.skipped_lines += 1
            continue
        if not isinstance(record, dict) or record.get("event") != "turn_timing":  # pyright: ignore[reportUnknownMemberType]
            summary.skipped_lines += 1
            continue
        summary.turns += 1
        if record.get("total_ms") is None:  # pyright: ignore[reportUnknownMemberType]
            summary.interrupted += 1
            continue
        summary.completed += 1
        for stage in STAGES:
            value = record.get(stage)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(value, int | float):
                samples[stage].append(round(value))
    for stage, values in samples.items():
        if values:
            summary.stages[stage] = StageStats(
                len(values), percentile(values, 50), percentile(values, 95)
            )
    return summary


def render(summary: Summary) -> str:
    if summary.turns == 0:
        return "No turns found in the log: nothing to summarise.\n"
    out = [
        f"{summary.completed} completed turns "
        f"({summary.interrupted} cut off before any agent audio, "
        f"{summary.skipped_lines} other lines skipped)",
        "stage                  n     p50 ms    p95 ms",
    ]
    for stage in STAGES:
        stats = summary.stages.get(stage)
        if stats:
            out.append(f"{stage:<20} {stats.n:>4} {stats.p50:>9} {stats.p95:>9}")
    out.append("total_ms = end of speech to first agent audio; target p50 < 800, p95 < 1500.")
    if not summary.enough_for_report:
        out.append(
            f"NOTE: fewer than {MIN_TURNS} completed turns; too few to report p50/p95 as a result."
        )
    return "\n".join(out) + "\n"
