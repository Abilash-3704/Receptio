"""Seam: `compute_metrics` over stored Calls. Seeded rows, hand-worked expected figures (literals
below, not recomputed from the code). The clinic day is Asia/Kolkata."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import CallRecord, CallTurnTiming, EscalationRecord
from backend.app.metrics import compute_metrics

IST = ZoneInfo("Asia/Kolkata")
HUMAN = "caller asked for a human"
EMERGENCY = "emergency: chest pain"


def cost(total: float, placeholder: bool = True, complete: bool = True) -> dict[str, Any]:
    return {"total_usd": total, "placeholder_prices": placeholder, "total_complete": complete}


def add_call(
    session: Session,
    started: datetime,
    seconds: int | None,
    outcome: str | None,
    call_cost: dict[str, Any] | None,
    reasons: tuple[str, ...] = (),
) -> int:
    ended = None if seconds is None else started + timedelta(seconds=seconds)
    call = CallRecord(
        started_at=started,
        ended_at=ended,
        status="open" if ended is None else "ended",
        outcome=outcome,
        cost=call_cost,
    )
    session.add(call)
    session.flush()
    for reason in reasons:
        session.add(
            EscalationRecord(
                call_id=call.id,
                kind="handoff",
                reason=reason,
                caller_phone=None,
                created_at=started,
            )
        )
    return call.id


def seed(factory: sessionmaker[Session], placeholder: bool = True) -> None:
    with factory() as session:
        day1, day2 = (
            datetime(2026, 11, 2, 9, 0, tzinfo=IST),
            datetime(2026, 11, 3, 9, 0, tzinfo=IST),
        )
        add_call(session, day1, 120, "completed", cost(0.06, placeholder))
        b = add_call(session, day1, 180, "escalated", cost(0.09, placeholder), (HUMAN, EMERGENCY))
        add_call(session, day2, 60, "dropped", cost(0.03, placeholder))
        add_call(session, day2 + timedelta(hours=2), None, None, None)  # still open
        # 00:30 IST on the 2nd is still 1 November in UTC: it belongs to the clinic's day.
        add_call(
            session,
            datetime(2026, 11, 1, 19, 0, tzinfo=ZoneInfo("UTC")),
            60,
            "completed",
            cost(0.02, placeholder),
        )
        add_call(session, day1, 60, "escalated", cost(0.02, placeholder, complete=False), (HUMAN,))
        # Twenty observed latencies; the p95 by nearest rank is the 19th, not the 5000 outlier.
        values = [*range(800, 2700, 100), 5000]
        for ms in values:
            session.add(CallTurnTiming(call_id=b, total_from_speech_end_ms=ms, total_ms=ms - 50))
        session.add(CallTurnTiming(call_id=b, total_from_speech_end_ms=None, total_ms=9999))
        session.commit()


def test_calls_per_day_use_the_clinic_day(backend: Any) -> None:
    seed(backend.app.state.session_factory)
    with backend.app.state.session_factory() as session:
        metrics = compute_metrics(session)

    assert metrics["calls_per_day"] == [
        {"day": "2026-11-02", "calls": 4},
        {"day": "2026-11-03", "calls": 2},
    ]


def test_completion_rate_counts_ended_calls_only(backend: Any) -> None:
    seed(backend.app.state.session_factory)
    with backend.app.state.session_factory() as session:
        metrics = compute_metrics(session)

    assert metrics["completion"] == {
        "ended": 5,
        "completed": 2,
        "escalated": 2,
        "dropped": 1,
        "rate": 0.4,
    }


def test_p95_latency_is_measured_from_speech_end_by_nearest_rank(backend: Any) -> None:
    seed(backend.app.state.session_factory)
    with backend.app.state.session_factory() as session:
        metrics = compute_metrics(session)

    assert metrics["latency"] == {
        "metric": "total_from_speech_end_ms",
        "samples": 20,
        "p95_ms": 2600,
    }


def test_cost_per_minute_is_total_cost_over_total_minutes_and_carries_the_placeholder_flag(
    backend: Any,
) -> None:
    seed(backend.app.state.session_factory)
    with backend.app.state.session_factory() as session:
        metrics = compute_metrics(session)

    # 0.22 USD over 120+180+60+60+60 = 480 s = 8 minutes
    assert metrics["cost_per_minute"] == {
        "usd": 0.0275,
        "minutes": 8.0,
        "calls": 5,
        "placeholder_prices": True,
        "incomplete_calls": 1,
    }


def test_the_placeholder_flag_is_off_when_every_call_was_priced_for_real(backend: Any) -> None:
    seed(backend.app.state.session_factory, placeholder=False)
    with backend.app.state.session_factory() as session:
        assert compute_metrics(session)["cost_per_minute"]["placeholder_prices"] is False


def test_escalation_reasons_are_counted_most_frequent_first(backend: Any) -> None:
    seed(backend.app.state.session_factory)
    with backend.app.state.session_factory() as session:
        metrics = compute_metrics(session)

    assert metrics["escalation_reasons"] == [
        {"reason": HUMAN, "count": 2},
        {"reason": EMERGENCY, "count": 1},
    ]


def test_with_no_calls_every_figure_says_there_is_no_data(backend: Any) -> None:
    with backend.app.state.session_factory() as session:
        metrics = compute_metrics(session)

    assert metrics["calls_per_day"] == []
    assert metrics["completion"]["rate"] is None
    assert metrics["latency"]["p95_ms"] is None
    assert metrics["cost_per_minute"]["usd"] is None
    assert metrics["escalation_reasons"] == []
