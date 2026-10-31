"""The baseline file format, and the comparison that ticket 15's CI gate calls.

A baseline is a small JSON file per mode (`evals/baselines/scripted.json`):

    {"schema_version": 1, "mode": "scripted", "metrics": {"completion_rate": 1.0, ...}}

It holds the value of every gated metric (those with a direction: higher or lower is better) from
a known-good run. `compare_to_baseline` lists each metric that got worse than the baseline, and each
target the new run misses. An empty list means the gate passes. Cost, latency and the escalation
rate are tracked in the report, not gated."""

from typing import Any

from evals.report import SCHEMA_VERSION, Report

Baseline = dict[str, Any]


def baseline_from(report: Report) -> Baseline:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": report["mode"],
        "metrics": {
            key: m["value"]
            for key, m in report["metrics"].items()
            if m["direction"] != "info" and m["value"] is not None
        },
    }


def compare_to_baseline(report: Report, baseline: Baseline, tolerance: float = 0.0) -> list[str]:
    """Regressions against the baseline. `tolerance` is the slack allowed for noisy (simulated)
    runs; scripted runs are deterministic and use zero."""
    if baseline["schema_version"] != SCHEMA_VERSION:
        return [f"baseline schema {baseline['schema_version']} != report schema {SCHEMA_VERSION}"]
    if baseline["mode"] != report["mode"]:
        return [f"baseline is for mode {baseline['mode']!r}, report is {report['mode']!r}"]
    problems: list[str] = []
    for key, was in baseline["metrics"].items():
        m = report["metrics"].get(key)
        if m is None or m["value"] is None:
            problems.append(f"{key}: no longer measured (baseline {was})")
            continue
        now, worse = m["value"], False
        if m["direction"] == "higher":
            worse = now < was - tolerance
        elif m["direction"] == "lower":
            worse = now > was + tolerance
        if worse:
            problems.append(f"{key}: {now:.4f} is worse than the baseline {was:.4f}")
    for key, m in report["metrics"].items():
        if m["status"] == "fail":
            problems.append(
                f"{key}: {m['value']:.4f} misses its target ({m['comparator']} {m['target']})"
            )
    return problems
