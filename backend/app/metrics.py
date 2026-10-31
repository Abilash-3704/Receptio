"""Summary metrics over every stored Call. Deterministic: the same rows give the same figures.

- Calls per day: by the clinic's local day (Asia/Kolkata), not UTC.
- Completion rate: completed Calls over ended Calls. A Call still open has no outcome yet.
- p95 latency: of `total_from_speech_end_ms` (the figure the latency budget is stated in; it
  includes endpointing), nearest rank like backend/app/trace.py and voice/summary.py.
- Cost per minute: the summed `total_usd` of ended Calls over their summed minutes. It carries
  the placeholder-price flag: true when any Call's cost used a price nobody configured. A Call
  whose cost left out an unpriced component is counted in `incomplete_calls`, because its figure
  understates what it cost.
- Escalation reasons: how many Escalations gave each reason, most frequent first."""

from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.clock import CLINIC_TZ
from backend.app.db import CallRecord, CallTurnTiming, EscalationRecord
from backend.app.trace import percentile

LATENCY_METRIC = "total_from_speech_end_ms"


def compute_metrics(session: Session) -> dict[str, Any]:
    """Every figure is over every stored Call (a time bound would change what is reported), so
    the work is kept proportional to what is needed: only the columns used are read, never whole
    Call rows, counting happens in the database, and only ended Calls with a cost are read with
    their cost document."""
    started = session.scalars(select(CallRecord.started_at))
    per_day = Counter(t.astimezone(CLINIC_TZ).date().isoformat() for t in started)

    ended_where = (CallRecord.outcome.is_not(None), CallRecord.ended_at.is_not(None))
    outcomes = {
        outcome: n
        for outcome, n in session.execute(
            select(CallRecord.outcome, func.count())
            .where(*ended_where)
            .group_by(CallRecord.outcome)
        ).tuples()
    }
    ended = sum(outcomes.values())
    completion: dict[str, Any] = {
        "ended": ended,
        "completed": outcomes.get("completed", 0),
        "escalated": outcomes.get("escalated", 0),
        "dropped": outcomes.get("dropped", 0),
        "rate": round(outcomes.get("completed", 0) / ended, 4) if ended else None,
    }

    latencies = list(
        session.scalars(
            select(CallTurnTiming.total_from_speech_end_ms).where(
                CallTurnTiming.total_from_speech_end_ms.is_not(None)
            )
        )
    )
    latency: dict[str, Any] = {
        "metric": LATENCY_METRIC,
        "samples": len(latencies),
        "p95_ms": percentile(latencies, 95) if latencies else None,
    }

    costed = session.execute(
        select(CallRecord.started_at, CallRecord.ended_at, CallRecord.cost).where(
            *ended_where, CallRecord.cost.is_not(None)
        )
    ).all()
    costed = [row for row in costed if row[2] is not None]  # a JSON null is not a cost either
    seconds = sum((ended_at - started_at).total_seconds() for started_at, ended_at, _ in costed)
    total_usd = sum(float(cost["total_usd"]) for _, _, cost in costed)
    cost_per_minute: dict[str, Any] = {
        "usd": round(total_usd / (seconds / 60), 6) if seconds > 0 else None,
        "minutes": round(seconds / 60, 4),
        "calls": len(costed),
        "placeholder_prices": any(cost.get("placeholder_prices") for _, _, cost in costed),
        "incomplete_calls": sum(1 for _, _, cost in costed if not cost.get("total_complete")),
    }

    reasons = {
        reason: n
        for reason, n in session.execute(
            select(EscalationRecord.reason, func.count()).group_by(EscalationRecord.reason)
        ).tuples()
    }
    return {
        "calls_per_day": [{"day": d, "calls": n} for d, n in sorted(per_day.items())],
        "completion": completion,
        "latency": latency,
        "cost_per_minute": cost_per_minute,
        "escalation_reasons": [
            {"reason": r, "count": n}
            for r, n in sorted(reasons.items(), key=lambda x: (-x[1], x[0]))
        ],
    }
