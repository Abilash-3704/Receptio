"""Alerts over persisted Calls: latency spikes, tool failure rate, dropped Calls.

`evaluate_alerts` reads ticket 17's tables for a recent window and returns the rules that are
firing; it holds no state and sends nothing. `emit` writes each as one structured JSON log line
(logger `receptio.alerts`, WARNING) and returns the JSON document a script or a future dashboard
shows. There is no external paging service on purpose: run
`python -m backend.app.alerts` from cron (or a loop) and let the exit code and the log line carry
the alert; it exits 1 when anything fires.

Every threshold is configuration (BackendSettings, `ALERT_*`). Each rule also has a minimum
sample size, because a p95 of three turns or a failure rate of two tool calls is noise, not a
spike.

What is measured, exactly:
- latency: `total_from_speech_end_ms` of voice turns (end of Caller speech to first agent audio),
  p50 and p95 by nearest rank. Turns with no audio (interrupted) have no total and are left out.
  A turn timing has no timestamp of its own, so it belongs to the window by its Call's start.
- tool failures: tool calls with `ok = false` that the state machine did NOT refuse. A refusal
  (`refused = true`) is a guard doing its job, such as declining to book before a read-back, and
  is not a failure.
- dropped Calls: share of Calls that ended in the window with outcome `dropped`.

An alert carries numbers and tool names only: no transcript text and no personal data."""

import argparse
import json
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.clock import system_clock
from backend.app.config import BackendSettings
from backend.app.db import CallRecord, CallToolCall, CallTurnTiming, create_db_engine
from backend.app.trace import percentile

logger = logging.getLogger("receptio.alerts")


@dataclass(frozen=True)
class Alert:
    rule: str
    severity: str  # critical or warning
    message: str
    value: float
    threshold: float
    sample_size: int
    window_minutes: int
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _latency(session: Session, settings: BackendSettings, since: datetime) -> list[Alert]:
    totals = [
        t
        for t in session.scalars(
            select(CallTurnTiming.total_from_speech_end_ms)
            .join(CallRecord, CallRecord.id == CallTurnTiming.call_id)
            .where(
                CallRecord.started_at >= since,
                CallTurnTiming.total_from_speech_end_ms.is_not(None),
            )
        )
        if t is not None
    ]
    if len(totals) < settings.alert_min_turns:
        return []
    found: list[Alert] = []
    for rule, severity, p, limit in (
        ("latency_p95", "critical", 95, settings.alert_latency_p95_ms),
        ("latency_p50", "warning", 50, settings.alert_latency_p50_ms),
    ):
        value = percentile(totals, p)
        if value > limit:
            found.append(
                Alert(
                    rule,
                    severity,
                    f"p{p} of speech end to first agent audio is {value} ms (limit {limit} ms)",
                    value,
                    limit,
                    len(totals),
                    settings.alert_window_minutes,
                    {"metric": "total_from_speech_end_ms"},
                )
            )
    return found


def _tool_failures(session: Session, settings: BackendSettings, since: datetime) -> list[Alert]:
    rows = session.execute(
        select(CallToolCall.name, CallToolCall.ok, CallToolCall.refused).where(
            CallToolCall.created_at >= since
        )
    ).all()
    # A refusal by the state machine is not the tool failing: leave it out of both counts.
    counted = [(name, ok) for name, ok, refused in rows if not refused]
    if len(counted) < settings.alert_min_tool_calls:
        return []
    failures = Counter(name for name, ok in counted if not ok)
    rate = sum(failures.values()) / len(counted)
    if rate <= settings.alert_tool_failure_rate:
        return []
    worst = max(failures.items(), key=lambda item: (item[1], item[0]))[0]
    return [
        Alert(
            "tool_failure_rate",
            "critical",
            f"{rate:.0%} of tool calls failed (limit {settings.alert_tool_failure_rate:.0%}); "
            f"worst: {worst}",
            rate,
            settings.alert_tool_failure_rate,
            len(counted),
            settings.alert_window_minutes,
            {"worst_tool": worst, "failures_by_tool": dict(sorted(failures.items()))},
        )
    ]


def _dropped(session: Session, settings: BackendSettings, since: datetime) -> list[Alert]:
    outcomes = list(
        session.scalars(
            select(CallRecord.outcome).where(
                CallRecord.status == "ended", CallRecord.ended_at >= since
            )
        )
    )
    if len(outcomes) < settings.alert_min_calls:
        return []
    rate = outcomes.count("dropped") / len(outcomes)
    if rate <= settings.alert_dropped_call_rate:
        return []
    return [
        Alert(
            "dropped_call_rate",
            "warning",
            f"{rate:.0%} of Calls ended dropped (limit {settings.alert_dropped_call_rate:.0%})",
            rate,
            settings.alert_dropped_call_rate,
            len(outcomes),
            settings.alert_window_minutes,
            {},
        )
    ]


def evaluate_alerts(session: Session, settings: BackendSettings, now: datetime) -> list[Alert]:
    since = now - timedelta(minutes=settings.alert_window_minutes)
    return (
        _latency(session, settings, since)
        + _tool_failures(session, settings, since)
        + _dropped(session, settings, since)
    )


def emit(alerts: Sequence[Alert]) -> str:
    """Log each alert as one JSON line and return the whole result as JSON."""
    for alert in alerts:
        logger.warning(json.dumps({"event": "alert", **alert.as_dict()}))
    return json.dumps({"firing": bool(alerts), "alerts": [a.as_dict() for a in alerts]})


def main(
    argv: Sequence[str] | None = None,
    session_factory: Callable[[], Session] | None = None,
    now: datetime | None = None,
) -> int:
    """Evaluate, print the JSON, log each alert. 0 when quiet, 1 when something fires."""
    parser = argparse.ArgumentParser(description="Evaluate Receptio alerts over recent Calls.")
    parser.add_argument("--window-minutes", type=int)
    parser.add_argument("--min-turns", type=int)
    args = parser.parse_args(argv)
    overrides: dict[str, int] = {}
    if args.window_minutes is not None:
        overrides["alert_window_minutes"] = args.window_minutes
    if args.min_turns is not None:
        overrides["alert_min_turns"] = args.min_turns
    settings = BackendSettings().model_copy(update=overrides)
    if session_factory is None:
        session_factory = sessionmaker(create_db_engine(settings), expire_on_commit=False)
    with session_factory() as session:
        alerts = evaluate_alerts(session, settings, now or system_clock())
    print(emit(alerts))
    return 1 if alerts else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    raise SystemExit(main())
