"""The staff dashboard: a Call list, a per-Call trace viewer and the summary metrics.

Server-rendered HTML plus a JSON API over the same data, no JavaScript. Both are staff routes
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
from html import escape
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.clock import CLINIC_TZ
from backend.app.db import CallRecord, CallTurn, EscalationRecord
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
body{font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:960px;padding:16px;color:#1b1f23;
background:#fff}
@media (prefers-color-scheme:dark){body{color:#e6e6e6;background:#15181b}
th,td{border-color:#3a3f44}.flag{background:#5a4300;color:#ffe9a8}.turn.agent{background:#1d2a33}}
h1,h2{margin:.8em 0 .4em}table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #ccd;padding:4px 8px;text-align:left;vertical-align:top}
.flag{background:#ffe9a8;color:#5a4300;border-radius:3px;padding:0 4px;font-size:.8em}
.turn{padding:6px 8px;margin:4px 0;border-radius:4px}.turn.agent{background:#eaf2f8}
pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:2px 0;font-size:.85em}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px}dd{margin:0}
"""

PLACEHOLDER_NOTE = "placeholder price: not a real provider price"


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


def _call_row(c: dict[str, Any]) -> str:
    seconds = "" if c["duration_seconds"] is None else f"{c['duration_seconds']} s"
    flag = _flag("call", call_id=c["id"]) if c["placeholder_prices"] else ""
    started = c["started_at"].astimezone(CLINIC_TZ).strftime("%Y-%m-%d %H:%M")
    link = f'<a data-link="call-{c["id"]}" href="/dashboard/calls/{c["id"]}">#{c["id"]}</a>'
    return (
        f'<tr data-row="call" data-call-id="{c["id"]}"><td>{link}</td><td>{escape(started)}</td>'
        f"<td>{escape(c['mode'])}</td><td>{escape(c['outcome'] or c['status'])}</td>"
        f"<td>{seconds}</td><td>{c['turns']}</td><td>{c['escalations']}</td>"
        f"<td>{_usd(c['cost_usd'])}{flag}</td></tr>"
    )


def render_index(
    metrics: dict[str, Any], calls: list[dict[str, Any]], limit: int, offset: int
) -> HTMLResponse:
    done, latency, cost = metrics["completion"], metrics["latency"], metrics["cost_per_minute"]
    rate = "n/a" if done["rate"] is None else f"{done['rate'] * 100:.1f}%"
    per_day = "".join(
        f'<tr data-row="calls-per-day"><td>{escape(d["day"])}</td><td>{d["calls"]}</td></tr>'
        for d in metrics["calls_per_day"]
    )
    reasons = "".join(
        f'<tr data-row="escalation-reason"><td>{escape(r["reason"])}</td><td>{r["count"]}</td></tr>'
        for r in metrics["escalation_reasons"]
    )
    cost_flag = _flag("metrics") if cost["placeholder_prices"] else ""
    incomplete = (
        f' <span data-field="incomplete-cost-calls">({cost["incomplete_calls"]} Call(s) left out '
        "an unpriced component)</span>"
        if cost["incomplete_calls"]
        else ""
    )
    rows = "".join(_call_row(c) for c in calls)
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
    body = f"""<h1>Receptio dashboard</h1>
<h2>Summary</h2>
<dl id="metrics">
<dt>Completion rate</dt><dd data-metric="completion-rate">{rate}</dd>
<dt>Calls ended</dt><dd data-metric="calls-ended">{done["ended"]} ({done["completed"]} completed,
{done["escalated"]} escalated, {done["dropped"]} dropped)</dd>
<dt>p95 latency (from speech end)</dt>
<dd data-metric="p95-latency">{_ms(latency["p95_ms"])}</dd>
<dt>Latency samples</dt><dd data-metric="latency-samples">{latency["samples"]}</dd>
<dt>Cost per minute</dt>
<dd data-metric="cost-per-minute">{_usd(cost["usd"])} {cost_flag}{incomplete}</dd>
</dl>
<h2>Calls per day</h2>
<table id="calls-per-day"><tr><th>Day</th><th>Calls</th></tr>{per_day}</table>
<h2>Escalation reasons</h2>
<table id="escalation-reasons"><tr><th>Reason</th><th>Escalations</th></tr>{reasons}</table>
<h2>Calls</h2>
<table id="calls"><tr><th>Call</th><th>Started (clinic time)</th><th>Mode</th><th>Outcome</th>
<th>Length</th><th>Turns</th><th>Escalations</th><th>Cost</th></tr>{rows}</table>
<p>{newer}{older}</p>"""
    return _page("Receptio dashboard", body)


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
    cost_html = "n/a (the Call has not ended)"
    if cost is not None:
        flag = _flag("call") if cost["placeholder_prices"] else ""
        partial = "" if cost["total_complete"] else " (partial: a component has no price)"
        cost_html = (
            f'<span data-field="cost-total">{_usd(cost["total_usd"])}</span>{partial} {flag}'
        )

    turns: list[str] = []
    for t in trace["turns"]:
        extras: list[str] = []
        for tool in t["tool_calls"]:
            detail = json.dumps(
                {"args": tool["args"], "result": tool["result"]}, ensure_ascii=False, sort_keys=True
            )
            status = "refused" if tool["refused"] else ("ok" if tool["ok"] else "failed")
            extras.append(
                f'<div data-tool="{escape(tool["name"])}">{escape(tool["name"])} ({status})'
                f"<pre>{escape(detail)}</pre></div>"
            )
        if t["latency"] is not None:
            ms = t["latency"]["total_from_speech_end_ms"]
            interrupted = " (interrupted)" if t["latency"]["interrupted"] else ""
            extras.append(
                f'<div>latency from speech end: <span data-field="turn-latency" '
                f'data-turn-seq="{t["seq"]}">{_ms(ms)}</span>{interrupted}</div>'
            )
        spent = t["cost"]["claude_usd"]
        if t["llm_calls"]:
            flagged = any(c["price_placeholder"] for c in t["llm_calls"])
            extras.append(
                f'<div>Claude cost: <span data-field="turn-cost" data-turn-seq="{t["seq"]}">'
                f"{_usd(spent)}</span> {_flag('turn', turn_seq=t['seq']) if flagged else ''}</div>"
            )
        turns.append(
            f'<div class="turn {escape(t["speaker"])}" data-turn-seq="{t["seq"]}" '
            f'data-speaker="{escape(t["speaker"])}"><b>{escape(t["speaker"])}</b>: '
            f'<span data-field="turn-text">{escape(t["text"])}</span>{"".join(extras)}</div>'
        )
    escalations = "".join(
        f'<li data-row="escalation">{escape(e["kind"])}: {escape(e["reason"])}</li>'
        for e in trace["escalations"]
    )
    body = f"""<p><a data-link="back" href="/dashboard">all Calls</a></p>
<h1>Call #{call["id"]}</h1>
<dl>
<dt>Mode</dt><dd data-field="mode">{escape(call["mode"])}</dd>
<dt>Started (clinic time)</dt><dd>{escape(started)}</dd>
<dt>Status</dt><dd data-field="status">{escape(call["status"])}</dd>
<dt>Outcome</dt><dd data-field="outcome">{escape(call["outcome"] or "")}</dd>
<dt>Outcome reason</dt><dd data-field="outcome-reason">{escape(call["outcome_reason"] or "")}</dd>
<dt>p95 latency (from speech end)</dt><dd data-field="call-latency-p95">{_ms(p95)}</dd>
<dt>Cost</dt><dd>{cost_html}</dd>
</dl>
<h2>Escalations</h2><ul>{escalations}</ul>
<h2>Transcript, tool calls and latency</h2>
{"".join(turns)}"""
    return _page(f"Call #{call['id']}", body)


@router.get("", response_class=HTMLResponse)
def index(session: SessionDep, limit: Limit = 50, offset: Offset = 0) -> HTMLResponse:
    return render_index(
        compute_metrics(session), call_summaries(session, limit, offset), limit, offset
    )


@router.get("/calls/{call_id}", response_class=HTMLResponse)
def call_page(request: Request, call_id: CallId, session: SessionDep) -> HTMLResponse:
    return render_trace(_trace(request, session, call_id))
