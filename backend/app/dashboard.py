# ruff: noqa: E501  (the HTML templates below are long lines of markup)
"""The staff dashboard: a Call list, a per-Call trace viewer and the summary metrics.

Server-rendered HTML (dark theme, inline SVG chart) plus a JSON API over the same data, no
JavaScript. Both are staff routes
(backend/app/auth.py: any route not listed there as public or service needs the staff login).
`backend.app.trace.load_trace` is the one read model of a single Call; the list and the metrics
read the Call tables directly.

The page shows personal data in full (the transcript, tool arguments), because staff need it. So:
- everything that came from a Caller is HTML-escaped, and the page loads no script, image or
  anything else (Content-Security-Policy default-src 'none');
- responses are `Cache-Control: no-store`.
Costs are shown with a placeholder-price flag whenever the price behind them was never configured;
latency is `total_from_speech_end_ms`, the budget's own measure."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.clock import CLINIC_TZ
from backend.app.db import (
    AppointmentRecord,
    CallRecord,
    CallTurn,
    CallTurnTiming,
    EscalationRecord,
    Patient,
    Provider,
    Service,
)
from backend.app.metrics import compute_metrics
from backend.app.trace import load_trace, percentile

MAX_PAGE = 200
CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


async def protect_responses(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """App-wide middleware: nothing the backend answers is cached (most of it is personal data),
    and the dashboard's pages are locked down against script injection."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/dashboard"):
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


router = APIRouter(prefix="/dashboard")


def _session(request: Request) -> Any:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(_session)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE)]
CallId = Annotated[int, Path(ge=1, le=2**31 - 1)]  # a Postgres integer
Offset = Annotated[int, Query(ge=0, le=10**9)]


def call_summaries(session: Session, limit: int, offset: int) -> list[dict[str, Any]]:
    """Newest Call first. No transcript: the list carries no personal data."""
    calls = session.scalars(
        select(CallRecord).order_by(CallRecord.id.desc()).limit(limit).offset(offset)
    ).all()
    ids = [c.id for c in calls]
    turns = {
        row[0]: row[1]
        for row in session.execute(
            select(CallTurn.call_id, func.count())
            .where(CallTurn.call_id.in_(ids))
            .group_by(CallTurn.call_id)
        ).all()
    }
    escalations = {
        row[0]: row[1]
        for row in session.execute(
            select(EscalationRecord.call_id, func.count())
            .where(EscalationRecord.call_id.in_(ids))
            .group_by(EscalationRecord.call_id)
        ).all()
    }
    return [
        {
            "id": c.id,
            "mode": c.mode,
            "status": c.status,
            "outcome": c.outcome,
            "outcome_reason": c.outcome_reason,
            "started_at": c.started_at,
            "duration_seconds": None
            if c.ended_at is None
            else round((c.ended_at - c.started_at).total_seconds()),
            "turns": turns.get(c.id, 0),
            "escalations": escalations.get(c.id, 0),
            "cost_usd": None if c.cost is None else c.cost.get("total_usd"),
            "placeholder_prices": bool(c.cost and c.cost.get("placeholder_prices")),
        }
        for c in calls
    ]


@router.get("/api/metrics")
def api_metrics(session: SessionDep) -> dict[str, Any]:
    return compute_metrics(session)


@router.get("/api/calls")
def api_calls(session: SessionDep, limit: Limit = 50, offset: Offset = 0) -> list[dict[str, Any]]:
    return jsonable_encoder(call_summaries(session, limit, offset))


def _trace(request: Request, session: Session, call_id: int) -> dict[str, Any]:
    try:
        return load_trace(session, call_id, request.app.state.prices)
    except LookupError:
        raise HTTPException(status_code=404, detail="call not found") from None


@router.get("/api/calls/{call_id}")
def api_call(request: Request, call_id: CallId, session: SessionDep) -> dict[str, Any]:
    return jsonable_encoder(_trace(request, session, call_id))


# ---- HTML -------------------------------------------------------------------------------------

STYLE = """
:root{--bg:#222221;--card:#2c2d2f;--inset:#242424;--line:#3a3b3d;--fg:#f4f4ef;--muted:#a8a8a0;
--green:#b5f08f;--leaf:#66cc00;--purple:#d9b3ff;--red:#ff8a7a;--grey:#b0b0a8}
*{box-sizing:border-box}
body{font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0 auto;max-width:1280px;
padding:20px 28px 48px;color:var(--fg);background:var(--bg)}
a{color:var(--green)}
header.top{display:flex;align-items:center;gap:28px;flex-wrap:wrap;margin-bottom:28px}
.brand{font-size:26px;font-weight:600;display:flex;align-items:center;gap:10px}
nav{display:flex;gap:24px;flex:1}
nav a{color:var(--muted);text-decoration:none;padding:6px 0;border-bottom:3px solid transparent}
nav a.on{color:var(--fg);font-weight:600;border-color:var(--leaf)}
.pill{border:1px solid var(--line);border-radius:999px;padding:5px 14px;font-size:13px;
white-space:nowrap}
h1{font-size:40px;font-weight:500;margin:.2em 0 .1em}h2{font-size:21px;margin:0 0 16px}
.sub{color:var(--muted);margin:0 0 24px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;
margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:24px;padding:26px}
.tile{border-radius:18px;padding:20px}
.label{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted)}
.big{font-size:44px;font-weight:500;margin:6px 0 2px}
.ok{color:var(--green);font-size:13px;font-weight:600}.bad{color:var(--red);font-size:13px;
font-weight:600}.note{color:var(--muted);font-size:13px}
.grid2{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:24px}
.stack{display:grid;gap:16px;align-content:start}
@media(max-width:900px){.grid2{grid-template-columns:1fr}body{padding:16px}}
.reason{margin-bottom:14px}.reason div.r{display:flex;justify-content:space-between}
.bar{height:10px;border-radius:6px;background:#3a3b3d;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;background:var(--green)}
table{border-collapse:collapse;width:100%}
th{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
text-align:left;padding:8px 10px;border-bottom:1px solid var(--grey)}
td{padding:12px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.chip{display:inline-block;border-radius:999px;padding:3px 12px;font-size:13px;font-weight:600;
background:#3a3b3d;color:var(--fg)}
.chip.completed{background:#3a4a3a;color:var(--green)}.chip.escalated{background:var(--purple);
color:#222}.chip.dropped{background:var(--red);color:#222}.chip.open{background:#3a3b3d}
.flag{background:#5a4300;color:#ffe9a8;border-radius:4px;padding:0 6px;font-size:.78em;
margin-left:6px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin-bottom:18px}
.legend span:before{content:"";display:inline-block;width:11px;height:11px;border-radius:3px;
margin-right:6px;background:var(--c)}
.tl{margin-bottom:22px}.tl .hd{display:flex;justify-content:space-between;gap:12px;
font-weight:600}.tl .hd span.q{font-weight:400;overflow-wrap:anywhere}
.track{position:relative;display:flex;height:26px;border-radius:13px;background:#3a3b3d;
overflow:hidden;margin:8px 0 6px}
.track b{display:block;height:100%}.mark{position:absolute;top:0;bottom:0;width:2px;background:#fff}
.tool{background:var(--inset);border:1px solid var(--line);border-radius:14px;padding:12px 16px;
margin-bottom:10px}
.tool .n{font-family:ui-monospace,Menlo,monospace}
pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0 0;font-size:.82em;color:var(--muted)}
.turn{padding:10px 14px;margin:8px 0;border-radius:14px;background:var(--inset)}
.turn.agent{background:#3a4238;margin-right:12%}.turn.caller{margin-left:12%;background:#3a3b3d}
.turn .who{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted)}
.turn.agent .who{color:var(--green)}
.cost td:last-child{text-align:right}.cost tr.total td{font-weight:700;border:0}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0}dd{margin:0}
.wk{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.wk .btn{border:1px solid var(--line);border-radius:999px;padding:6px 16px;text-decoration:none;
color:var(--fg)}.wk .btn.on{background:var(--green);color:#222;border-color:var(--green)}
.cal{display:grid;grid-template-columns:52px repeat(7,minmax(110px,1fr));min-width:860px}
.calwrap{overflow-x:auto}
.cal .dh{padding:8px 6px;text-align:center;border-bottom:1px solid var(--grey);font-weight:600}
.cal .dh.today{color:var(--green)}.cal .dh small{display:block;color:var(--muted);font-weight:400}
.cal .hrs{position:relative}.cal .hrs span{position:absolute;right:8px;font-size:11px;
color:var(--muted);transform:translateY(-50%)}
.cal .col{position:relative;border-left:1px solid var(--line);
background:repeating-linear-gradient(to bottom,transparent 0,transparent 47px,var(--line) 47px,var(--line) 48px)}
.cal .col.today{background-color:#2a2f27}
.appt{position:absolute;border-radius:8px;padding:3px 6px;font-size:12px;line-height:1.25;
overflow:hidden;color:#1d1d1d;border:1px solid rgba(0,0,0,.35)}
.appt b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;
vertical-align:-1px}
"""

PLACEHOLDER_NOTE = "placeholder price: not a real provider price"
TARGET_P95_MS = 1500
TARGET_P50_MS = 800
COMPLETION_TARGET = 0.9
LEAF = (
    '<svg width="30" height="30" viewBox="0 0 24 24" aria-hidden="true"><path fill="#66cc00" '
    'd="M20 3C9 3 4 9 4 15c0 1.2.3 2.3.8 3.2L3 21l2 .5 1.5-2.4C7.5 19.7 8.700 20 10 20c7 0 10-7 10-17z"/></svg>'
)


def _nav(active: str, latest_call: int | None = None) -> str:
    sched = f'<a class="{"on" if active == "schedule" else ""}" href="/dashboard/appointments">Schedule</a>'
    trace = f'<a class="{"on" if active == "trace" else ""}" href="/dashboard/calls/{latest_call}">Call trace</a>' if latest_call else ""
    return (
        f'<header class="top"><div class="brand">{LEAF}Receptio</div><nav>'
        f'<a class="{"on" if active == "overview" else ""}" href="/dashboard">Overview</a>'
        f"{sched}{trace}</nav>"
    )


def _page(title: str, body: str) -> HTMLResponse:
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head><body>{body}</body></html>"
    )
    return HTMLResponse(html)


def _flag(scope: str, **extra: str | int) -> str:
    hooks = "".join(f' data-{k.replace("_", "-")}="{escape(str(v))}"' for k, v in extra.items())
    return (
        f'<span class="flag" data-flag="placeholder-price" data-scope="{scope}"{hooks} '
        f'title="{PLACEHOLDER_NOTE}">placeholder price</span>'
    )


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def _ms(value: int | None) -> str:
    return "n/a" if value is None else f"{value} ms"


def _secs(value: int | None) -> str:
    return "n/a" if value is None else f"{value // 60}:{value % 60:02d}"


def latency_by_day(session: Session) -> list[dict[str, Any]]:
    """p50 and p95 of the from-speech-end latency per clinic-local day, oldest first, last 7 days."""
    rows = session.execute(
        select(CallRecord.started_at, CallTurnTiming.total_from_speech_end_ms)
        .join(CallRecord, CallRecord.id == CallTurnTiming.call_id)
        .where(CallTurnTiming.total_from_speech_end_ms.is_not(None))
    ).all()
    days: dict[str, list[int]] = {}
    for started_at, ms in rows:
        days.setdefault(started_at.astimezone(CLINIC_TZ).date().isoformat(), []).append(ms)
    return [
        {"day": d, "p50": percentile(v, 50), "p95": percentile(v, 95)}
        for d, v in sorted(days.items())[-7:]
    ]


def slowest_turns(session: Session, call_ids: list[int]) -> dict[int, int]:
    return {
        row[0]: row[1]
        for row in session.execute(
            select(CallTurnTiming.call_id, func.max(CallTurnTiming.total_from_speech_end_ms))
            .where(CallTurnTiming.call_id.in_(call_ids))
            .group_by(CallTurnTiming.call_id)
        ).all()
        if row[1] is not None
    }


def _latency_chart(days: list[dict[str, Any]]) -> str:
    if not days:
        return '<p class="note" data-field="no-latency">No timed turns yet.</p>'
    w, h, left, top, bottom = 640, 260, 52, 14, 30
    ymax = max(2000, (max(d["p95"] for d in days) // 500 + 1) * 500)
    plot_h = h - top - bottom

    def y(v: float) -> float:
        return top + plot_h * (1 - v / ymax)

    def x(i: int) -> float:
        return left + (w - left - 10) * (i / max(1, len(days) - 1) if len(days) > 1 else 0.5)

    grid = "".join(
        f'<line x1="{left}" x2="{w - 10}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#3a3b3d"/>'
        f'<text x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#a8a8a0" font-size="11">{v}</text>'
        for v in range(0, ymax + 1, 500)
    )
    targets = "".join(
        f'<line x1="{left}" x2="{w - 10}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#a8a8a0" '
        f'stroke-dasharray="5 4"/>'
        for v in (TARGET_P50_MS, TARGET_P95_MS)
    )
    labels = "".join(
        f'<text x="{x(i):.1f}" y="{h - 8}" text-anchor="middle" fill="#a8a8a0" font-size="11">'
        f"{escape(d['day'][5:])}</text>"
        for i, d in enumerate(days)
    )

    def line(key: str, color: str) -> str:
        pts = " ".join(f"{x(i):.1f},{y(d[key]):.1f}" for i, d in enumerate(days))
        dots = "".join(
            f'<circle cx="{x(i):.1f}" cy="{y(d[key]):.1f}" r="4" fill="{color}"><title>'
            f"{escape(d['day'])} {key}: {d[key]} ms</title></circle>"
            for i, d in enumerate(days)
        )
        return f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="3"/>{dots}'

    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Response latency by day">'
        f'{grid}{targets}{line("p95", "#b5f08f")}{line("p50", "#d9b3ff")}{labels}</svg>'
    )


OUTCOME_LABEL = {"completed": "Completed", "escalated": "Escalated", "dropped": "Dropped"}


def _chip(outcome: str | None, status: str) -> str:
    key = outcome or "open"
    return f'<span class="chip {escape(key)}">{escape(OUTCOME_LABEL.get(key, outcome or status))}</span>'


def _call_row(c: dict[str, Any], slowest: int | None) -> str:
    seconds = "" if c["duration_seconds"] is None else _secs(c["duration_seconds"])
    flag = _flag("call", call_id=c["id"]) if c["placeholder_prices"] else ""
    started = c["started_at"].astimezone(CLINIC_TZ).strftime("%Y-%m-%d %H:%M")
    link = f'<a data-link="call-{c["id"]}" href="/dashboard/calls/{c["id"]}">#{c["id"]}</a>'
    slow = "" if slowest is None else f"{slowest / 1000:.2f} s"
    return (
        f'<tr data-row="call" data-call-id="{c["id"]}"><td>{escape(started)}</td><td>{link}</td>'
        f"<td>{escape(c['mode'])}</td><td>{_chip(c['outcome'], c['status'])}</td>"
        f"<td>{seconds}</td><td>{slow}</td><td>{c['turns']}</td><td>{c['escalations']}</td>"
        f"<td>{_usd(c['cost_usd'])}{flag}</td></tr>"
    )


def _tile(label: str, value: str, note: str, state: str = "note", **hook: str) -> str:
    attr = "".join(f' data-{k}="{v}"' for k, v in hook.items())
    return (
        f'<div class="card tile"><div class="label">{label}</div>'
        f'<div class="big"{attr}>{value}</div><div class="{state}">{note}</div></div>'
    )


def render_index(
    metrics: dict[str, Any],
    calls: list[dict[str, Any]],
    limit: int,
    offset: int,
    days: list[dict[str, Any]] | None = None,
    slowest: dict[int, int] | None = None,
) -> HTMLResponse:
    days, slowest = days or [], slowest or {}
    done, latency, cost = metrics["completion"], metrics["latency"], metrics["cost_per_minute"]
    rate = "n/a" if done["rate"] is None else f"{done['rate'] * 100:.1f}%"
    rate_ok = done["rate"] is not None and done["rate"] >= COMPLETION_TARGET
    p95 = latency["p95_ms"]
    p95_ok = p95 is not None and p95 < TARGET_P95_MS
    per_day = "".join(
        f'<tr data-row="calls-per-day"><td>{escape(d["day"])}</td><td>{d["calls"]}</td></tr>'
        for d in metrics["calls_per_day"]
    )
    top = max((r["count"] for r in metrics["escalation_reasons"]), default=1)
    reasons = "".join(
        f'<div class="reason" data-row="escalation-reason"><div class="r"><span>{escape(r["reason"])}'
        f'</span><span>{r["count"]}</span></div><div class="bar"><i style="width:{r["count"] / top * 100:.0f}%">'
        "</i></div></div>"
        for r in metrics["escalation_reasons"]
    ) or '<p class="note">No escalations yet.</p>'
    cost_flag = _flag("metrics") if cost["placeholder_prices"] else ""
    incomplete = (
        f' <span data-field="incomplete-cost-calls">({cost["incomplete_calls"]} Call(s) left out '
        "an unpriced component)</span>"
        if cost["incomplete_calls"]
        else ""
    )
    rows = "".join(_call_row(c, slowest.get(c["id"])) for c in calls)
    older = (
        f'<a data-link="older" href="/dashboard?limit={limit}&amp;offset={offset + limit}">'
        "older</a>"
        if len(calls) == limit
        else ""
    )
    newer = (
        f'<a data-link="newer" href="/dashboard?limit={limit}&amp;offset={max(0, offset - limit)}">'
        "newer</a> "
        if offset
        else ""
    )
    tiles = "".join(
        [
            _tile(
                "Task completion", f"<span data-metric=\"completion-rate\">{rate}</span>",
                "Target 90% or higher", "ok" if rate_ok else "bad",
            ),
            _tile(
                "Calls ended", f'<span data-metric="calls-ended">{done["ended"]}</span>',
                f"{done['completed']} completed, {done['escalated']} escalated, "
                f"{done['dropped']} dropped",
            ),
            _tile(
                "P95 response latency",
                f'<span data-metric="p95-latency">{_ms(p95)}</span>',
                "Target under 1500 ms, from speech end", "ok" if p95_ok else "bad",
            ),
            _tile(
                "Latency samples", f'<span data-metric="latency-samples">{latency["samples"]}</span>',
                "Timed caller turns",
            ),
            _tile(
                "Cost per minute", f'<span data-metric="cost-per-minute">{_usd(cost["usd"])}</span>',
                f"Tracked, no fixed target {cost_flag}{incomplete}",
            ),
        ]
    )
    latest = calls[0]["id"] if calls else None
    body = f"""{_nav("overview", latest)}<span class="pill">All stored Calls</span></header>
<h1>How the receptionist is doing</h1>
<p class="sub">Each tile compares against the target in docs/metrics.md. Wrong-time rate and Emergency recall come from the eval suite, not from live Calls.</p>
<div class="tiles" id="metrics">{tiles}</div>
<div class="grid2">
<div class="card"><h2>Response latency by day</h2>
<div class="legend"><span style="--c:#b5f08f">p95</span><span style="--c:#d9b3ff">p50</span>
<span style="--c:#a8a8a0">Targets 800 / 1500 ms</span></div>{_latency_chart(days)}</div>
<div class="card"><h2>Why calls were escalated</h2><div id="escalation-reasons">{reasons}</div></div>
</div>
<div class="card" style="margin-bottom:24px"><h2>Calls per day</h2>
<table id="calls-per-day"><tr><th>Day</th><th>Calls</th></tr>{per_day}</table></div>
<div class="card"><h2>Recent calls</h2>
<table id="calls"><tr><th>Started (clinic time)</th><th>Call</th><th>Mode</th><th>Outcome</th>
<th>Length</th><th>Slowest turn</th><th>Turns</th><th>Escalations</th><th>Cost</th></tr>{rows}</table>
<p>{newer}{older}</p></div>"""
    return _page("Receptio dashboard", body)


SEGMENTS = (
    ("asr_final_ms", "Speech to text", "#b5f08f"),
    ("llm_first_token_ms", "Model", "#d9b3ff"),
    ("tts_first_audio_ms", "Text to speech", "#b0b0a8"),
)


def _timeline(t: dict[str, Any], scale: int) -> str:
    lat = t["latency"]
    total = lat["total_from_speech_end_ms"]
    parts = [(max(0, lat[k] or 0), label, color) for k, label, color in SEGMENTS]
    bars = "".join(
        f'<b title="{label} {v} ms" style="width:{v / scale * 100:.2f}%;background:{color}"></b>'
        for v, label, color in parts
        if v
    )
    tags = ""
    if lat["interrupted"]:
        tags += ' <span class="chip escalated">Barge-in</span>'
    if total is not None and total > TARGET_P95_MS:
        tags += ' <span class="chip dropped">Over target</span>'
    detail = ", ".join(f"{label.lower()} {v} ms" for v, label, _ in parts)
    return (
        f'<div class="tl"><div class="hd"><span>Turn {t["seq"]} '
        f'<span class="q">"{escape(t["text"])}"</span>{tags}</span>'
        f'<span data-field="turn-latency" data-turn-seq="{t["seq"]}">{_ms(total)}</span></div>'
        f'<div class="track">{bars}<span class="mark" style="left:{TARGET_P50_MS / scale * 100:.2f}%">'
        f'</span></div><div class="note">{detail}</div></div>'
    )


def render_trace(trace: dict[str, Any]) -> HTMLResponse:
    call = trace["call"]
    cost = call["cost"]
    started = call["started_at"].astimezone(CLINIC_TZ).strftime("%Y-%m-%d %H:%M:%S")
    from_speech_end = [
        t["latency"]["total_from_speech_end_ms"]
        for t in trace["turns"]
        if t["latency"] and t["latency"]["total_from_speech_end_ms"] is not None
    ]
    p95 = percentile(from_speech_end, 95) if from_speech_end else None
    duration = (
        None
        if call["ended_at"] is None
        else round((call["ended_at"] - call["started_at"]).total_seconds())
    )
    cost_html = "n/a (the Call has not ended)"
    cost_rows = ""
    if cost is not None:
        flag = _flag("call") if cost["placeholder_prices"] else ""
        partial = "" if cost["total_complete"] else " (partial: a component has no price)"
        cost_html = (
            f'<span data-field="cost-total">{_usd(cost["total_usd"])}</span>{partial} {flag}'
        )
        names = {"deepgram": "Speech to text", "claude": "Model", "cartesia": "Text to speech"}
        cost_rows = "".join(
            f"<tr><td>{label}</td><td>{_usd(cost['components'][k]['usd'])}</td></tr>"
            for k, label in names.items()
            if cost["components"].get(k)
        )

    timed = [t for t in trace["turns"] if t["latency"] is not None and t["speaker"] == "caller"]
    scale = max([2000] + [t["latency"]["total_from_speech_end_ms"] or 0 for t in timed])
    timelines = "".join(_timeline(t, scale) for t in timed) or (
        '<p class="note">No timing was recorded for this Call.</p>'
    )

    turns: list[str] = []
    tool_cards: list[str] = []
    for t in trace["turns"]:
        extras: list[str] = []
        for tool in t["tool_calls"]:
            detail = json.dumps(
                {"args": tool["args"], "result": tool["result"]}, ensure_ascii=False, sort_keys=True
            )
            status = "refused" if tool["refused"] else ("ok" if tool["ok"] else "failed")
            tool_cards.append(
                f'<div class="tool" data-tool="{escape(tool["name"])}"><span class="n">'
                f'{escape(tool["name"])}</span> ({status})<pre>{escape(detail)}</pre></div>'
            )
        spent = t["cost"]["claude_usd"]
        if t["llm_calls"]:
            flagged = any(c["price_placeholder"] for c in t["llm_calls"])
            extras.append(
                f'<div class="note">Claude cost: <span data-field="turn-cost" data-turn-seq="{t["seq"]}">'
                f"{_usd(spent)}</span> {_flag('turn', turn_seq=t['seq']) if flagged else ''}</div>"
            )
        turns.append(
            f'<div class="turn {escape(t["speaker"])}" data-turn-seq="{t["seq"]}" '
            f'data-speaker="{escape(t["speaker"])}"><div class="who">{escape(t["speaker"])}</div>'
            f'<span data-field="turn-text">{escape(t["text"])}</span>{"".join(extras)}</div>'
        )
    escalations = "".join(
        f'<li data-row="escalation">{escape(e["kind"])}: {escape(e["reason"])}</li>'
        for e in trace["escalations"]
    ) or '<li class="note">None</li>'
    outcome = call["outcome"] or ""
    body = f"""{_nav("trace", call["id"])}<span class="pill">{escape(call["mode"])} Call</span></header>
<p><a data-link="back" href="/dashboard">Back to overview</a></p>
<h1>Call #{call["id"]}</h1>
<p>{_chip(call["outcome"], call["status"])} <span class="pill">Duration {_secs(duration)}</span>
<span class="pill">{sum(1 for t in trace["turns"] if t["speaker"] == "caller")} caller turns</span>
<span class="pill">Started {escape(started)}</span></p>
<dl style="display:none"><dt>Mode</dt><dd data-field="mode">{escape(call["mode"])}</dd>
<dt>Status</dt><dd data-field="status">{escape(call["status"])}</dd>
<dt>Outcome</dt><dd data-field="outcome">{escape(outcome)}</dd>
<dt>Reason</dt><dd data-field="outcome-reason">{escape(call["outcome_reason"] or "")}</dd></dl>
<p class="note">Outcome: {escape(outcome or call["status"])}{" — " + escape(call["outcome_reason"]) if call["outcome_reason"] else ""}.
p95 latency from speech end: <span data-field="call-latency-p95">{_ms(p95)}</span></p>
<div class="grid2">
<div class="stack">
<div class="card"><h2>Where each response spent its time</h2>
<div class="legend"><span style="--c:#b5f08f">Speech to text</span><span style="--c:#d9b3ff">Model</span>
<span style="--c:#b0b0a8">Text to speech</span><span style="--c:#fff">800 ms target</span></div>
{timelines}</div>
<div class="card"><h2>Transcript</h2>{"".join(turns)}</div>
</div>
<div class="stack">
<div class="card"><h2>Tool calls</h2>{"".join(tool_cards) or '<p class="note">None</p>'}</div>
<div class="card"><h2>Escalations</h2><ul>{escalations}</ul></div>
<div class="card"><h2>Cost of this call</h2><table class="cost">{cost_rows}
<tr class="total"><td>Total</td><td>{cost_html}</td></tr></table></div>
</div></div>"""
    return _page(f"Call #{call['id']}", body)


# ---- Schedule: every Confirmed Appointment, by week --------------------------------------------

PALETTE = ("#b5f08f", "#d9b3ff", "#ffc98a", "#8fd3f0", "#ff9fb0", "#e8e48a")
PX_PER_HOUR = 48
Week = Annotated[date | None, Query(description="any date in the week to show (Monday start)")]
ProviderFilter = Annotated[int | None, Query(ge=1, le=2**31 - 1)]


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def confirmed_appointments(
    session: Session, start: date, end: date, provider_id: int | None = None
) -> list[dict[str, Any]]:
    """Confirmed Appointments starting on a clinic-local day in [start, end), soonest first.
    Holds, cancelled and released Appointments are not real Appointments and are left out."""
    lo = datetime.combine(start, time.min, CLINIC_TZ).astimezone(UTC)
    hi = datetime.combine(end, time.min, CLINIC_TZ).astimezone(UTC)
    query = (
        select(AppointmentRecord, Provider.name, Service.name, Patient.full_name, Patient.phone)
        .join(Provider, Provider.id == AppointmentRecord.provider_id)
        .join(Service, Service.id == AppointmentRecord.service_id)
        .outerjoin(Patient, Patient.id == AppointmentRecord.patient_id)
        .where(
            AppointmentRecord.status == "confirmed",
            AppointmentRecord.starts_at >= lo,
            AppointmentRecord.starts_at < hi,
        )
        .order_by(AppointmentRecord.starts_at, AppointmentRecord.id)
    )
    if provider_id is not None:
        query = query.where(AppointmentRecord.provider_id == provider_id)
    return [
        {
            "id": a.id,
            "provider_id": a.provider_id,
            "provider": provider,
            "service": service,
            "patient": patient,
            "phone_ending": phone[-4:] if phone else None,
            "starts_at": a.starts_at,
            "ends_at": a.ends_at,
        }
        for a, provider, service, patient, phone in session.execute(query).all()
    ]


@router.get("/api/appointments")
def api_appointments(
    session: SessionDep, week: Week = None, provider: ProviderFilter = None
) -> list[dict[str, Any]]:
    monday = _monday(week or datetime.now(CLINIC_TZ).date())
    return jsonable_encoder(
        confirmed_appointments(session, monday, monday + timedelta(days=7), provider)
    )


def render_schedule(
    session: Session, monday: date, providers: list[Provider], provider_id: int | None, today: date
) -> HTMLResponse:
    appts = confirmed_appointments(session, monday, monday + timedelta(days=7), provider_id)
    colour = {p.id: PALETTE[i % len(PALETTE)] for i, p in enumerate(providers)}
    local = [
        (a, a["starts_at"].astimezone(CLINIC_TZ), a["ends_at"].astimezone(CLINIC_TZ)) for a in appts
    ]
    first = min([9] + [s.hour for _, s, _ in local])
    last = max([18] + [e.hour + (1 if e.minute else 0) for _, _, e in local])
    height = (last - first) * PX_PER_HOUR
    hours = "".join(
        f'<span style="top:{(h - first) * PX_PER_HOUR}px">{h:02d}:00</span>'
        for h in range(first, last + 1)
    )
    days = [monday + timedelta(days=i) for i in range(7)]
    heads = "".join(
        f'<div class="dh{" today" if d == today else ""}">{d.strftime("%a")}<small>{d.strftime("%d %b")}</small></div>'
        for d in days
    )
    cols = ""
    for d in days:
        blocks = ""
        for a, s, e in local:
            if s.date() != d:
                continue
            top = (s.hour + s.minute / 60 - first) * PX_PER_HOUR
            h = max(22, (e - s).total_seconds() / 3600 * PX_PER_HOUR - 2)
            same = [x for x in local if x[1].date() == d and x[1] < e and x[2] > s]
            lane, lanes = same.index((a, s, e)), len(same)
            blocks += (
                f'<div class="appt" data-row="appointment" data-appointment-id="{a["id"]}" '
                f'style="top:{top:.0f}px;height:{h:.0f}px;left:calc({lane / lanes * 100:.1f}% + 2px);'
                f'width:calc({100 / lanes:.1f}% - 4px);background:{colour[a["provider_id"]]}" '
                f'title="{escape(a["service"])} with {escape(a["provider"])}">'
                f'<b>{s.strftime("%H:%M")} {escape(a["patient"] or "Unknown")}</b>'
                f"{escape(a['service'])}, {escape(a['provider'])}</div>"
            )
        cols += f'<div class="col{" today" if d == today else ""}" style="height:{height}px">{blocks}</div>'
    grid = f'<div class="cal"><div class="dh"></div>{heads}<div class="hrs" style="height:{height}px">{hours}</div>{cols}</div>'

    rows = "".join(
        f'<tr data-row="agenda"><td>{s.strftime("%a %d %b")}</td><td>{s.strftime("%H:%M")}'
        f'-{e.strftime("%H:%M")}</td><td>{escape(a["patient"] or "Unknown")}'
        f'{"" if not a["phone_ending"] else " <span class=note>(ending " + escape(a["phone_ending"]) + ")</span>"}</td>'
        f'<td>{escape(a["service"])}</td><td><span class="dot" style="background:{colour[a["provider_id"]]}">'
        f"</span>{escape(a['provider'])}</td></tr>"
        for a, s, e in local
    ) or '<tr><td colspan="5" class="note">No confirmed appointments this week.</td></tr>'
    q = f"&amp;provider={provider_id}" if provider_id else ""
    prev_w, next_w = monday - timedelta(days=7), monday + timedelta(days=7)
    filters = '<a class="btn{}" href="/dashboard/appointments?week={}">All providers</a>'.format(
        "" if provider_id else " on", monday
    ) + "".join(
        f'<a class="btn{" on" if provider_id == p.id else ""}" href="/dashboard/appointments?week={monday}&amp;provider={p.id}">'
        f'<span class="dot" style="background:{colour[p.id]}"></span>{escape(p.name)}</a>'
        for p in providers
    )
    body = f"""{_nav("schedule")}<span class="pill">Clinic time (Asia/Kolkata)</span></header>
<h1>Schedule</h1>
<p class="sub">Confirmed Appointments only. Holds and cancelled Appointments are not shown.</p>
<div class="wk"><a class="btn" data-link="prev" href="/dashboard/appointments?week={prev_w}{q}">&larr; Previous</a>
<a class="btn" data-link="this-week" href="/dashboard/appointments{"?provider=" + str(provider_id) if provider_id else ""}">This week</a>
<a class="btn" data-link="next" href="/dashboard/appointments?week={next_w}{q}">Next &rarr;</a>
<strong data-field="week">{monday.strftime("%d %b")} to {days[-1].strftime("%d %b %Y")}</strong>
<span class="note" data-field="count">{len(local)} appointment(s)</span></div>
<div class="wk">{filters}</div>
<div class="card calwrap" style="padding:12px">{grid}</div>
<div class="card" style="margin-top:24px"><h2>Agenda</h2>
<table id="agenda"><tr><th>Day</th><th>Time</th><th>Patient</th><th>Service</th><th>Provider</th></tr>{rows}</table></div>"""
    return _page("Receptio schedule", body)


@router.get("/appointments", response_class=HTMLResponse)
def schedule(session: SessionDep, week: Week = None, provider: ProviderFilter = None) -> HTMLResponse:
    today = datetime.now(CLINIC_TZ).date()
    providers = list(session.scalars(select(Provider).order_by(Provider.id)))
    return render_schedule(session, _monday(week or today), providers, provider, today)


@router.get("", response_class=HTMLResponse)
def index(session: SessionDep, limit: Limit = 50, offset: Offset = 0) -> HTMLResponse:
    calls = call_summaries(session, limit, offset)
    return render_index(
        compute_metrics(session),
        calls,
        limit,
        offset,
        latency_by_day(session),
        slowest_turns(session, [c["id"] for c in calls]),
    )


@router.get("/calls/{call_id}", response_class=HTMLResponse)
def call_page(request: Request, call_id: CallId, session: SessionDep) -> HTMLResponse:
    return render_trace(_trace(request, session, call_id))
