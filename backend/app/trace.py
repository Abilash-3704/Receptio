"""Read a stored Call back whole: transcript, tool calls, LLM calls, latency, cost, Escalations.

This is the one read model of a Call. The trace viewer and the eval harness both call
`load_trace`; it is served only by the staff-login dashboard (backend/app/dashboard.py),
because it carries personal data in full.

Cost per turn: a Caller turn carries the Claude cost of answering it (measured tokens x price); in
a voice Call an agent turn carries the Cartesia cost of its characters. Deepgram has no per-turn
figure, only the Call's estimate. The Call's own `cost` was fixed when it ended."""

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.cost import Prices
from backend.app.db import (
    CallLlmCall,
    CallRecord,
    CallToolCall,
    CallTurn,
    CallTurnTiming,
    EscalationRecord,
)


def percentile(values: list[int], p: float) -> int:
    """Nearest rank, as in voice/summary.py: every figure is a latency that was observed."""
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def load_trace(session: Session, call_id: int, prices: Prices | None = None) -> dict[str, Any]:
    """`prices` is only needed to price the characters of an agent turn in a voice Call; without
    it that per-turn figure is None."""
    call = session.get(CallRecord, call_id)
    if call is None:
        raise LookupError(f"no Call {call_id}")

    turn_rows = session.scalars(
        select(CallTurn).where(CallTurn.call_id == call_id).order_by(CallTurn.seq)
    ).all()
    tools = session.scalars(
        select(CallToolCall).where(CallToolCall.call_id == call_id).order_by(CallToolCall.id)
    ).all()
    llm_calls = session.scalars(
        select(CallLlmCall).where(CallLlmCall.call_id == call_id).order_by(CallLlmCall.id)
    ).all()
    timings = session.scalars(
        select(CallTurnTiming).where(CallTurnTiming.call_id == call_id).order_by(CallTurnTiming.id)
    ).all()
    escalations = session.scalars(
        select(EscalationRecord)
        .where(EscalationRecord.call_id == call_id)
        .order_by(EscalationRecord.id)
    ).all()

    turns: list[dict[str, Any]] = []
    for turn in turn_rows:
        mine = [c for c in llm_calls if c.turn_seq == turn.seq]
        timing = next((t for t in timings if t.caller_turn_seq == turn.seq), None)
        claude_usd = (
            None if any(c.cost_usd is None for c in mine) else sum(c.cost_usd or 0.0 for c in mine)
        )
        cartesia_usd = (
            prices.cartesia_cost(len(turn.text))
            if prices is not None and call.mode == "voice" and turn.speaker == "agent"
            else None
        )
        turns.append(
            {
                "seq": turn.seq,
                "speaker": turn.speaker,
                "text": turn.text,
                "at": turn.created_at,
                "tool_calls": [
                    {
                        "name": t.name,
                        "args": t.args,
                        "ok": t.ok,
                        "refused": t.refused,
                        "result": t.result,
                    }
                    for t in tools
                    if t.turn_seq == turn.seq
                ],
                "llm_calls": [
                    {
                        "model": c.model,
                        "input_tokens": c.input_tokens,
                        "output_tokens": c.output_tokens,
                        "duration_ms": c.duration_ms,
                        "cost_usd": c.cost_usd,
                        "price_placeholder": c.price_placeholder,
                    }
                    for c in mine
                ],
                "latency": None
                if timing is None
                else {
                    "asr_final_ms": timing.asr_final_ms,
                    "llm_first_token_ms": timing.llm_first_token_ms,
                    "tts_first_audio_ms": timing.tts_first_audio_ms,
                    "total_ms": timing.total_ms,
                    "total_from_speech_end_ms": timing.total_from_speech_end_ms,
                    "interrupted": timing.interrupted,
                },
                "cost": {"claude_usd": claude_usd, "cartesia_usd": cartesia_usd},
            }
        )
    totals = [t.total_ms for t in timings if t.total_ms is not None]
    latency = (
        {
            "turns": len(totals),
            "p50_ms": percentile(totals, 50),
            "p95_ms": percentile(totals, 95),
            "max_ms": max(totals),
        }
        if totals
        else None
    )
    return {
        "call": {
            "id": call.id,
            "mode": call.mode,
            "status": call.status,
            "outcome": call.outcome,
            "outcome_reason": call.outcome_reason,
            "started_at": call.started_at,
            "ended_at": call.ended_at,
            "cost": call.cost,
            "latency": latency,
        },
        "turns": turns,
        "escalations": [
            {"kind": e.kind, "reason": e.reason, "created_at": e.created_at} for e in escalations
        ],
    }
