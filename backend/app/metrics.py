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

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.clock import CLINIC_TZ
from backend.app.db import CallRecord, CallTurnTiming, EscalationRecord
from backend.app.trace import percentile

LATENCY_METRIC = "total_from_speech_end_ms"


def compute_metrics(session: Session) -> dict[str, Any]:
    calls = session.scalars(select(CallRecord).order_by(CallRecord.id)).all()

    per_day = Counter(c.started_at.astimezone(CLINIC_TZ).date().isoformat() for c in calls)

    ended = [c for c in calls if c.outcome is not None and c.ended_at is not None]
    outcomes = Counter(c.outcome for c in ended)
    completion: dict[str, Any] = {
        "ended": len(ended),
        "completed": outcomes["completed"],
        "escalated": outcomes["escalated"],
        "dropped": outcomes["dropped"],
        "rate": round(outcomes["completed"] / len(ended), 4) if ended else None,
    }

    latencies = [
        ms
        for ms in session.scalars(select(CallTurnTiming.total_from_speech_end_ms))
        if ms is not None
    ]
    latency: dict[str, Any] = {
        "metric": LATENCY_METRIC,
        "samples": len(latencies),
        "p95_ms": percentile(latencies, 95) if latencies else None,
    }

    costed = [c for c in ended if c.cost is not None and c.ended_at is not None]
    seconds = sum((c.ended_at - c.started_at).total_seconds() for c in costed if c.ended_at)
    total_usd = sum(float(c.cost["total_usd"]) for c in costed if c.cost)
    cost_per_minute: dict[str, Any] = {
        "usd": round(total_usd / (seconds / 60), 6) if seconds > 0 else None,
        "minutes": round(seconds / 60, 4),
        "calls": len(costed),
        "placeholder_prices": any(c.cost and c.cost.get("placeholder_prices") for c in costed),
        "incomplete_calls": sum(1 for c in costed if c.cost and not c.cost.get("total_complete")),
    }

    reasons = Counter(session.scalars(select(EscalationRecord.reason)))
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
