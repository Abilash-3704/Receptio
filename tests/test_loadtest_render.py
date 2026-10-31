"""The load report states what it is: a stand-in run, with its sample sizes and conditions, and
no claim about provider latency."""

from loadtest.pgstats import PgStats
from loadtest.report import RunSummary, render_standin_report
from loadtest.standins import StandInLatency


def summary() -> RunSummary:
    return RunSummary(
        calls=20,
        calls_by_outcome={"completed": 19, "dropped": 1},
        timing_samples=55,
        total_from_speech_end_ms={"n": 55, "p50": 2400, "p95": 4100, "p99": 4500, "max": 4600},
        stages={"total_ms": {"n": 55, "p50": 1900, "p95": 3600, "p99": 4000, "max": 4100}},
        tool_calls=70,
        tool_failures=3,
        tool_failures_by_name={"place_hold": 3},
    )


def test_the_report_is_labelled_a_stand_in_run_and_claims_nothing_about_providers() -> None:
    text = render_standin_report(
        title="Run A",
        conditions={"Concurrent Calls": "20", "Database": "Postgres 17 (podman)"},
        latency=StandInLatency(),
        admission={"admitted": 20},
        load=summary(),
        baseline=summary(),
        overhead_ms={"load": {"n": 55, "p50": 12, "p95": 40, "p99": 55, "max": 60}},
        token_ms={"n": 20, "p50": 30, "p95": 80, "p99": 90, "max": 95},
        pg=PgStats(samples=100, peak_connections=14, peak_lock_waiters=2, deadlocks=0),
        errors=[],
        fallbacks=3,
        alerts=[],
    )

    assert "STAND-IN RUN" in text
    assert "no claim" in text.lower() and "Deepgram" in text
    assert "55" in text and "2400" in text  # sample size and a percentile are in the table
    assert "Concurrent Calls" in text and "Postgres 17" in text
    assert "place_hold" in text
