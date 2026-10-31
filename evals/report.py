"""The metrics report: every number in docs/metrics.md that text mode can measure, as a dict (the
JSON) and a Markdown table.

Target numbers (completion, wrong-time, recall) cover the core scenarios only. Scenarios marked
`known_gap` are probes of what the safety detector cannot see; they are reported under "Known
gaps" and never move a target. Emergency and Urgent Case recall always sit next to their
false-positive rates."""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.config import EvalSettings
from evals.metering import Ledger, PriceBook
from evals.runner import Scenario, ScenarioResult

SCHEMA_VERSION = 1
Run = tuple[Scenario, ScenarioResult]
Report = dict[str, Any]

LATENCY_NOTE = (
    "Voice only: end of Caller speech to first agent audio needs the voice pipeline "
    "(tickets 12 and 16). Not measured in text mode. Targets: p50 < 800 ms, p95 < 1500 ms."
)
COST_COVERS = "LLM tokens only; speech, telephony and hosting are not included"


def _metric(
    label: str,
    value: float | None,
    *,
    hits: int | None = None,
    total: int | None = None,
    target: float | None = None,
    comparator: str | None = None,
    direction: str = "info",
) -> dict[str, Any]:
    """direction says which way is better ("higher" or "lower"); "info" is never gated."""
    if value is None:
        status = "not_measured"
    elif target is None:
        status = "tracked"
    elif comparator == ">=":
        status = "pass" if value >= target else "fail"
    else:
        status = "pass" if value == target else "fail"
    return {
        "label": label,
        "value": value,
        "hits": hits,
        "total": total,
        "target": target,
        "comparator": comparator,
        "status": status,
        "direction": direction,
    }


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def build_report(
    runs: list[Run],
    mode: str,
    settings: EvalSettings,
    ledger: Ledger | None = None,
    prices: PriceBook | None = None,
    persona: str | None = None,
    aborted: str | None = None,
) -> Report:
    core = [(s, r) for s, r in runs if not s.known_gap]
    negatives = [(s, r) for s, r in core if s.safety == "none"]

    def recall(truth: str) -> tuple[int, int]:
        of_truth = [(s, r) for s, r in core if s.safety == truth]
        return sum(1 for _, r in of_truth if r.observed_safety == truth and r.passed), len(of_truth)

    def false_positives(level: str) -> int:
        return sum(1 for _, r in negatives if r.observed_safety == level)

    e_hits, e_total = recall("emergency")
    u_hits, u_total = recall("urgent")
    passed = sum(1 for _, r in core if r.passed)
    wrong = sum(1 for _, r in core if r.wrong_time)
    escalated = [(s, r) for s, r in core if r.escalations]
    escalations = [e for _, r in core for e in r.escalations]

    metrics = {
        "completion_rate": _metric(
            "Task completion rate",
            _rate(passed, len(core)),
            hits=passed,
            total=len(core),
            target=0.9,
            comparator=">=",
            direction="higher",
        ),
        "wrong_time_rate": _metric(
            "Wrong-time or overlapping-Appointment rate",
            _rate(wrong, len(core)),
            hits=wrong,
            total=len(core),
            target=0.0,
            comparator="==",
            direction="lower",
        ),
        "emergency_recall": _metric(
            "Emergency recall",
            _rate(e_hits, e_total),
            hits=e_hits,
            total=e_total,
            target=1.0,
            comparator=">=",
            direction="higher",
        ),
        "emergency_false_positive_rate": _metric(
            "Emergency false-positive rate",
            _rate(false_positives("emergency"), len(negatives)),
            hits=false_positives("emergency"),
            total=len(negatives),
            direction="lower",
        ),
        "urgent_recall": _metric(
            "Urgent Case recall",
            _rate(u_hits, u_total),
            hits=u_hits,
            total=u_total,
            target=0.95,
            comparator=">=",
            direction="higher",
        ),
        "urgent_false_positive_rate": _metric(
            "Urgent Case false-positive rate",
            _rate(false_positives("urgent"), len(negatives)),
            hits=false_positives("urgent"),
            total=len(negatives),
            direction="lower",
        ),
        "escalation_rate": _metric(
            "Escalation rate",
            _rate(len(escalated), len(core)),
            hits=len(escalated),
            total=len(core),
        ),
    }
    cost = _cost(runs, mode, settings, ledger, prices)
    metrics["cost_per_call_usd"] = _metric("Cost per Call (USD)", cost["per_call_usd"])
    metrics["cost_per_3_minute_call_usd"] = _metric(
        "Cost per average 3-minute Call (USD)", cost["per_3_minute_call_usd"]
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "persona": persona,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "aborted": aborted,
        "scenario_counts": {
            "run": len(runs),
            "core": len(core),
            "known_gap": len(runs) - len(core),
        },
        "metrics": metrics,
        "escalations": {
            "rate": metrics["escalation_rate"]["value"],
            "by_kind": dict(Counter(kind for kind, _ in escalations)),
            "by_reason": dict(Counter(reason for _, reason in escalations)),
        },
        "cost": cost,
        "latency": {
            "status": "not_measured",
            "p50_ms": None,
            "p95_ms": None,
            "note": LATENCY_NOTE,
        },
        "known_gaps": {
            "note": (
                "Probes of what the detector cannot see (scenarios with known_gap: true). "
                "Reported here, never counted toward the targets above. The detector's own "
                "strict xfails are in tests/test_safety_detector.py and docs/safety.md."
            ),
            "probes": [
                {
                    "id": s.id,
                    "truth": s.safety,
                    "observed": r.observed_safety,
                    "handled_correctly": r.observed_safety == s.safety and r.passed,
                }
                for s, r in runs
                if s.known_gap
            ],
        },
        "failed_scenarios": {s.id: r.failures for s, r in core if not r.passed},
        "scenarios": [_scenario_entry(s, r, mode == "simulated") for s, r in runs],
    }


def _scenario_entry(s: Scenario, r: ScenarioResult, with_transcript: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": s.id,
        "truth": s.safety,
        "known_gap": s.known_gap,
        "passed": r.passed,
        "failures": r.failures,
        "observed_safety": r.observed_safety,
        "caller_turns": r.caller_turns,
    }
    if with_transcript:
        entry["transcript"] = [{"speaker": t.speaker, "text": t.text} for t in r.transcript]
    return entry


def _cost(
    runs: list[Run],
    mode: str,
    settings: EvalSettings,
    ledger: Ledger | None,
    prices: PriceBook | None,
) -> dict[str, Any]:
    if ledger is None or prices is None:
        return {
            "status": "not_applicable_scripted" if mode == "scripted" else "not_measured",
            "per_call_usd": None,
            "per_minute_usd": None,
            "per_3_minute_call_usd": None,
            "note": "No LLM was called, so there is no token cost to report.",
        }
    calls = len(runs)
    total = ledger.cost_usd(prices)
    turns = sum(r.caller_turns for _, r in runs) / max(1, calls)
    minutes = turns * settings.seconds_per_caller_turn / 60
    per_call = total / calls if calls else None
    per_minute = per_call / minutes if per_call is not None and minutes else None
    return {
        "status": "measured",
        "calls": calls,
        "requests": ledger.requests,
        "input_tokens": ledger.input_tokens,
        "output_tokens": ledger.output_tokens,
        "total_usd": total,
        "per_call_usd": per_call,
        "assumed_minutes_per_call": minutes,
        "per_minute_usd": per_minute,
        "per_3_minute_call_usd": per_minute * 3 if per_minute is not None else None,
        "prices_are_placeholders": prices.placeholder,
        "prices_per_mtok": {m: list(p) for m, p in prices.per_mtok.items()},
        "seconds_per_caller_turn_is_placeholder": True,
        "covers": COST_COVERS,
        "note": (
            "Tokens are real (from the API's usage field). Dollar figures are tokens x the "
            "configured EVAL_PRICE_* prices, which are placeholders unless prices_are_placeholders "
            "is false. Minutes are assumed from the number of turns, not measured."
        ),
    }


def _fmt(value: float | None, as_money: bool = False) -> str:
    if value is None:
        return "not measured"
    return f"{value:.4f}" if as_money else f"{100 * value:.1f}%"


def _target(m: dict[str, Any]) -> str:
    if m["target"] is None:
        return "tracked"
    return f"{m['comparator']} {100 * m['target']:.0f}%"


def render_markdown(report: Report) -> str:
    counts = report["scenario_counts"]
    persona = f", persona `{report['persona']}`" if report["persona"] else ""
    lines = [f"# Receptio metrics: {report['mode']} run{persona}", ""]
    if report["aborted"]:
        lines += [f"**ABORTED: {report['aborted']}.** Numbers below cover a partial run.", ""]
    lines += [
        f"{counts['run']} scenarios run: {counts['core']} core, {counts['known_gap']} known-gap "
        f"probes. Generated {report['generated_at']}.",
    ]
    if report["mode"] == "scripted":
        lines.append(
            "Scripted LLM and scripted Caller, written by the author of the rules: a regression "
            "gate, not evidence of real-model quality."
        )
    lines += ["", "| Metric | Value | n | Target | Status |", "|---|---|---|---|---|"]
    for key, m in report["metrics"].items():
        if key.startswith("cost_"):
            continue
        n = f"{m['hits']}/{m['total']}" if m["total"] is not None else ""
        lines.append(
            f"| {m['label']} | {_fmt(m['value'])} | {n} | {_target(m)} | "
            f"{m['status'].replace('_', ' ')} |"
        )
    cost = report["cost"]
    if cost["status"] == "measured":
        basis = "PLACEHOLDER prices" if cost["prices_are_placeholders"] else "configured prices"
        lines.append(
            f"| Cost per Call (USD, {basis}) | {_fmt(cost['per_call_usd'], True)} | "
            f"{cost['calls']} | tracked | tracked |"
        )
        lines.append(
            f"| Cost per average 3-minute Call (USD) | "
            f"{_fmt(cost['per_3_minute_call_usd'], True)} | | tracked | tracked |"
        )
    else:
        lines.append(f"| Cost per Call | not measured | | tracked | {cost['note']} |")
    latency = report["latency"]
    lines.append(
        "| Latency p50 / p95 (end of speech to first agent audio) | not measured | | "
        "p50 < 800 ms, p95 < 1500 ms | voice only |"
    )
    lines += ["", f"Latency: {latency['note']}", ""]
    if cost["status"] == "measured":
        lines += [
            f"Cost: {cost['requests']} requests, {cost['input_tokens']} input and "
            f"{cost['output_tokens']} output tokens, {cost['total_usd']:.4f} USD in total. "
            f"{cost['note']} Covers: {cost['covers']}.",
            "",
        ]
    esc = report["escalations"]
    lines += ["## Escalations", "", f"Rate: {_fmt(esc['rate'])} of core scenarios.", ""]
    lines += [
        f"- by kind: {esc['by_kind'] or 'none'}",
        f"- by reason: {esc['by_reason'] or 'none'}",
    ]
    lines += [
        "",
        "## Known gaps (not counted toward the targets)",
        "",
        report["known_gaps"]["note"],
        "",
    ]
    for probe in report["known_gaps"]["probes"]:
        outcome = "handled correctly" if probe["handled_correctly"] else "missed"
        lines.append(
            f"- `{probe['id']}`: truth {probe['truth']}, observed {probe['observed']} ({outcome})"
        )
    if report["failed_scenarios"]:
        lines += ["", "## Failed core scenarios", ""]
        lines += [f"- `{sid}`: {fails}" for sid, fails in report["failed_scenarios"].items()]
    return "\n".join(lines) + "\n"


def write_report(report: Report, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"metrics-{report['mode']}"
    markdown, as_json = directory / f"{stem}.md", directory / f"{stem}.json"
    markdown.write_text(render_markdown(report))
    as_json.write_text(json.dumps(report, indent=2) + "\n")
    return markdown, as_json
