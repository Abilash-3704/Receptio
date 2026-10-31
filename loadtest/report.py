"""Summarise a load run from what the system PERSISTED (not from the runner's own bookkeeping):
per-turn timing percentiles, Call outcomes and tool outcomes for exactly the Calls of the run.

Percentiles are nearest rank, as everywhere else in the project, so each is a value that was
observed."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.alerts import Alert
from backend.app.db import CallRecord, CallToolCall, CallTurnTiming
from backend.app.trace import percentile
from loadtest.pgstats import PgStats
from loadtest.runner import LoadResult
from loadtest.standins import StandInLatency

STAGES = (
    "asr_final_ms",
    "llm_first_token_ms",
    "tts_first_audio_ms",
    "total_ms",
    "total_from_speech_end_ms",
)


def stats(values: list[int]) -> dict[str, int]:
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
    }


def float_stats(values: list[float]) -> dict[str, int]:
    return stats([round(v) for v in values])


@dataclass
class RunSummary:
    calls: int = 0
    calls_by_outcome: dict[str, int] = field(default_factory=lambda: {})
    timing_samples: int = 0
    interrupted_turns: int = 0
    total_from_speech_end_ms: dict[str, int] | None = None
    stages: dict[str, dict[str, int]] = field(default_factory=lambda: {})
    tool_calls: int = 0
    tool_failures: int = 0
    tool_refusals: int = 0
    tool_failures_by_name: dict[str, int] = field(default_factory=lambda: {})


def summarise_run(session_factory: Callable[[], Session], result: LoadResult) -> RunSummary:
    return summarise_calls(session_factory, [c.call_id for c in result.calls if c.call_id])


def calls_since(session_factory: Callable[[], Session], since_call_id: int) -> list[int]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(CallRecord.id).where(CallRecord.id > since_call_id).order_by(CallRecord.id)
            )
        )


def summarise_calls(session_factory: Callable[[], Session], ids: list[int]) -> RunSummary:
    summary = RunSummary()
    if not ids:
        return summary
    with session_factory() as session:
        calls = session.scalars(select(CallRecord).where(CallRecord.id.in_(ids))).all()
        summary.calls = len(calls)
        summary.calls_by_outcome = dict(Counter(c.outcome or "open" for c in calls))
        timings = session.scalars(
            select(CallTurnTiming).where(CallTurnTiming.call_id.in_(ids))
        ).all()
        summary.timing_samples = len(timings)
        summary.interrupted_turns = sum(1 for t in timings if t.interrupted)
        for stage in STAGES:
            values = [v for t in timings if (v := getattr(t, stage)) is not None]
            if values:
                summary.stages[stage] = stats(values)
        summary.total_from_speech_end_ms = summary.stages.get("total_from_speech_end_ms")
        tools = session.execute(
            select(CallToolCall.name, CallToolCall.ok, CallToolCall.refused).where(
                CallToolCall.call_id.in_(ids)
            )
        ).all()
        summary.tool_calls = len(tools)
        summary.tool_refusals = sum(1 for _, _, refused in tools if refused)
        failed = Counter(name for name, ok, refused in tools if not ok and not refused)
        summary.tool_failures = sum(failed.values())
        summary.tool_failures_by_name = dict(sorted(failed.items()))
    return summary


def failure_rate(summary: RunSummary) -> float:
    counted = summary.tool_calls - summary.tool_refusals
    return 0.0 if counted <= 0 else summary.tool_failures / counted


def _row(label: str, s: dict[str, int] | None) -> str:
    if s is None:
        return f"| {label} | 0 | - | - | - | - |"
    return f"| {label} | {s['n']} | {s['p50']} | {s['p95']} | {s['p99']} | {s['max']} |"


HEADER = "| Measure (ms) | n | p50 | p95 | p99 | max |\n|---|---|---|---|---|---|"

DISCLAIMER = (
    "**STAND-IN RUN.** ASR, LLM and TTS were waits drawn from the configured distribution below, "
    "not Deepgram, Cartesia or Claude. The web token endpoint, capacity admission, the agent "
    "core, the backend, Postgres and the persisted turn timings were real. Every latency in this "
    "report is the configured stand-in plus this system's own overhead, so it makes no claim "
    "about real provider latency or about latency of a real Call under load. The real-provider "
    "run has not been performed."
)


def render_standin_report(
    title: str,
    conditions: dict[str, str],
    latency: StandInLatency,
    admission: dict[str, int],
    load: RunSummary,
    baseline: RunSummary | None,
    overhead_ms: dict[str, dict[str, int]],
    token_ms: dict[str, int] | None,
    pg: PgStats | None,
    errors: list[str],
    fallbacks: int,
    alerts: list[Alert],
) -> str:
    lines = [f"### {title}", "", DISCLAIMER, "", "Conditions:", ""]
    lines += [f"- {key}: {value}" for key, value in conditions.items()]
    lines += [
        f"- Stand-in medians (ms): ASR {latency.asr_ms:g}, endpointing {latency.endpointing_ms:g}, "
        f"LLM step {latency.llm_step_ms:g} (a tool turn takes two or more), TTS first audio "
        f"{latency.tts_first_audio_ms:g}; log-normal sigma {latency.sigma:g}. "
        "Assumed, not measured.",
        "",
        "Admission (web `/token` through the backend's cost guard):",
        "",
    ]
    lines += [f"- {key}: {value}" for key, value in sorted(admission.items())]
    lines += [
        "",
        "Per-turn timing, read back from the persisted `call_turn_timings` rows "
        "(nearest-rank percentiles):",
        "",
        HEADER,
        _row("load: total_from_speech_end_ms", load.total_from_speech_end_ms),
    ]
    for stage, values in load.stages.items():
        if stage != "total_from_speech_end_ms":
            lines.append(_row(f"load: {stage}", values))
    if baseline is not None:
        lines.append(
            _row(
                "Calls alone, one at a time: total_from_speech_end_ms",
                baseline.total_from_speech_end_ms,
            )
        )
    for label, values in overhead_ms.items():
        lines.append(
            _row(f"{label}: our overhead per turn (core time minus stand-in LLM waits)", values)
        )
    lines.append(_row("token request (web, incl. admission)", token_ms))
    lines += [
        "",
        f"Calls stored: {load.calls}; outcomes: {load.calls_by_outcome}; turn timings stored: "
        f"{load.timing_samples} (interrupted: {load.interrupted_turns}).",
        f"Tool calls: {load.tool_calls}; failed (not refused): {load.tool_failures} "
        f"{load.tool_failures_by_name}; refused by the state machine: {load.tool_refusals}.",
        f"Stand-in LLM script fallbacks (script no longer fitted the Call): {fallbacks}.",
    ]
    if pg is not None:
        lines.append(
            f"Postgres: peak {pg.peak_connections} connections, peak {pg.peak_lock_waiters} "
            f"sessions waiting on a lock ({pg.samples} samples), deadlocks {pg.deadlocks}, "
            f"rollbacks {pg.rollbacks}, commits {pg.commits}."
        )
    lines.append(
        f"Errors in the virtual Calls: {len(errors)}." + "".join(f"\n  - {e}" for e in errors)
    )
    lines.append(
        "Alerts evaluated over the persisted data: "
        + ("none firing." if not alerts else "; ".join(f"{a.rule}: {a.message}" for a in alerts))
    )
    return "\n".join(lines) + "\n"
