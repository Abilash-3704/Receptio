"""Seam: the dashboard's HTTP interface (JSON API and server-rendered HTML), as clinic staff.

Structural checks only: the HTML is parsed and its elements read; nobody has looked at it in a
browser, so layout and styling are unverified by these tests."""

from html.parser import HTMLParser
from typing import Any, cast

import httpx

from backend.app.db import CallTurnTiming
from tests.support import STAFF_PASSWORD
from tests.test_auth import anonymous, basic

Json = dict[str, Any]


class Page(HTMLParser):
    """Collects every element that carries a `data-*` hook, with its text and attributes."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.found: list[tuple[str, dict[str, str], str]] = []
        self._open: list[tuple[str, dict[str, str], list[str]]] = []
        self.tags: set[str] = set()
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        values = {k: v or "" for k, v in attrs}
        self._open.append((tag, values, []))

    def handle_data(self, data: str) -> None:
        for _, _, text in self._open:
            text.append(data)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][0] == tag:
                t, values, text = self._open.pop(i)
                if any(k.startswith("data-") for k in values):
                    self.found.append((t, values, " ".join(" ".join(text).split())))
                break

    def where(self, **attrs: str) -> list[tuple[dict[str, str], str]]:
        return [(v, t) for _, v, t in self.found if all(v.get(k) == x for k, x in attrs.items())]

    def text_of(self, **attrs: str) -> str:
        [(_, text)] = self.where(**attrs)
        return text


def a_call(backend: httpx.Client, caller_text: str = "hello", mode: str = "text") -> int:
    call_id: int = backend.post("/calls", json={"mode": mode}).json()["id"]
    backend.post(f"/calls/{call_id}/turns", json={"speaker": "agent", "text": "How can I help?"})
    backend.post(f"/calls/{call_id}/turns", json={"speaker": "caller", "text": caller_text})
    return call_id


def end(backend: httpx.Client, call_id: int, **body: Any) -> None:
    assert backend.post(f"/calls/{call_id}/end", json=body).status_code == 200


def test_the_dashboard_and_its_api_need_staff_credentials(backend: httpx.Client) -> None:
    call_id = a_call(backend)
    client = anonymous(backend)
    paths = [
        "/dashboard",
        f"/dashboard/calls/{call_id}",
        "/dashboard/api/metrics",
        "/dashboard/api/calls",
        f"/dashboard/api/calls/{call_id}",
    ]
    for path in paths:
        denied = client.get(path)
        assert denied.status_code == 401, path
        assert denied.headers["www-authenticate"].startswith("Basic"), path  # a browser prompts
        assert client.get(path, headers=basic(STAFF_PASSWORD)).status_code == 200, path


def test_every_call_is_in_the_list_newest_first_without_any_transcript(
    backend: httpx.Client,
) -> None:
    ids = [a_call(backend, "my name is Asha Rao, 9845012345") for _ in range(3)]
    end(backend, ids[0])

    listing: list[Json] = backend.get("/dashboard/api/calls").json()

    assert [c["id"] for c in listing] == [ids[2], ids[1], ids[0]]
    assert [c["outcome"] for c in listing] == [None, None, "completed"]
    assert listing[0]["turns"] == 2
    assert "Asha" not in str(listing) and "9845012345" not in str(listing)
    assert [c["id"] for c in backend.get("/dashboard/api/calls?limit=1&offset=1").json()] == [
        ids[1]
    ]


def test_the_html_list_has_a_row_and_a_trace_link_for_every_call(backend: httpx.Client) -> None:
    ids = [a_call(backend) for _ in range(3)]

    response = backend.get("/dashboard")
    page = Page(response.text)

    assert response.headers["content-type"].startswith("text/html")
    rows = page.where(**{"data-row": "call"})
    assert sorted(int(v["data-call-id"]) for v, _ in rows) == ids
    for call_id in ids:
        links = [v["href"] for _, v, _ in page.found if v.get("data-link") == f"call-{call_id}"]
        assert links == [f"/dashboard/calls/{call_id}"]


def test_the_trace_api_is_the_trace_read_model(backend: httpx.Client, trace: Any) -> None:
    call_id = a_call(backend)
    backend.post(
        f"/calls/{call_id}/tool-calls",
        json={
            "turn_seq": 2,
            "name": "lookup",
            "args": {"q": 1},
            "ok": True,
            "refused": False,
            "result": {"n": 1},
        },
    )
    end(backend, call_id)

    served: Json = backend.get(f"/dashboard/api/calls/{call_id}").json()
    stored = trace(call_id)

    assert [t["text"] for t in served["turns"]] == [t["text"] for t in stored["turns"]]
    assert served["turns"][1]["tool_calls"][0]["name"] == "lookup"
    assert served["call"]["outcome"] == "completed"
    assert backend.get("/dashboard/api/calls/999").status_code == 404
    assert backend.get("/dashboard/calls/999").status_code == 404


def test_the_trace_page_shows_transcript_tools_outcome_latency_and_cost(
    backend: httpx.Client,
) -> None:
    call_id = a_call(backend, "book me a cleaning", mode="voice")
    backend.post(
        f"/calls/{call_id}/tool-calls",
        json={
            "turn_seq": 2,
            "name": "find_slots",
            "args": {"day": "mon"},
            "ok": True,
            "refused": False,
            "result": {"slots": 3},
        },
    )
    backend.post(
        f"/calls/{call_id}/llm-calls",
        json={
            "turn_seq": 2,
            "model": "claude-sonnet-5-5",
            "input_tokens": 1000,
            "output_tokens": 100,
            "duration_ms": 700,
        },
    )
    backend.post(
        f"/calls/{call_id}/turn-timings",
        json={"caller_turn_seq": 2, "total_ms": 900, "total_from_speech_end_ms": 1400},
    )
    end(backend, call_id, outcome="dropped", reason="caller left")

    page = Page(backend.get(f"/dashboard/calls/{call_id}").text)

    assert [t for _, t in page.where(**{"data-field": "turn-text"})] == [
        "How can I help?",
        "book me a cleaning",
    ]
    assert page.text_of(**{"data-tool": "find_slots"}).startswith("find_slots")
    assert page.text_of(**{"data-field": "outcome"}) == "dropped"
    assert page.text_of(**{"data-field": "outcome-reason"}) == "caller left"
    # latency is the figure measured from the end of the Caller's speech, in milliseconds
    assert page.text_of(**{"data-field": "turn-latency", "data-turn-seq": "2"}) == "1400 ms"
    assert page.text_of(**{"data-field": "call-latency-p95"}) == "1400 ms"
    # the default prices are placeholders, and the page says so wherever it shows a cost
    assert page.where(**{"data-flag": "placeholder-price", "data-scope": "call"})
    assert page.where(
        **{"data-flag": "placeholder-price", "data-scope": "turn", "data-turn-seq": "2"}
    )
    assert page.text_of(**{"data-field": "cost-total"}).startswith("$")


def test_a_configured_price_shows_no_placeholder_flag(backend_factory: Any) -> None:
    backend = backend_factory(
        llm_prices_per_mtok={"claude-sonnet-5-5": (3.0, 15.0)},
        deepgram_usd_per_minute=0.0043,
        cartesia_usd_per_1k_characters=0.03,
    )
    call_id = a_call(backend, mode="voice")
    backend.post(
        f"/calls/{call_id}/llm-calls",
        json={"turn_seq": 2, "model": "claude-sonnet-5-5", "input_tokens": 10, "output_tokens": 1},
    )
    end(backend, call_id)

    page = Page(backend.get(f"/dashboard/calls/{call_id}").text)

    assert page.where(**{"data-flag": "placeholder-price"}) == []
    assert page.text_of(**{"data-field": "cost-total"}).startswith("$")


def test_caller_speech_is_escaped_so_it_cannot_run_in_a_staff_browser(
    backend: httpx.Client,
) -> None:
    hostile = '<script>alert(1)</script><img src=x onerror="steal()">'
    call_id = a_call(backend, hostile)
    backend.post(
        f"/calls/{call_id}/tool-calls",
        json={
            "turn_seq": 2,
            "name": "x",
            "args": {"a": hostile},
            "ok": True,
            "refused": False,
            "result": {},
        },
    )

    response = backend.get(f"/dashboard/calls/{call_id}")
    page = Page(response.text)

    assert "<script" not in response.text and "<img" not in response.text
    assert hostile in [t for _, t in page.where(**{"data-field": "turn-text"})]
    assert "script" not in page.tags and "img" not in page.tags
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src" not in csp
    assert response.headers["cache-control"] == "no-store"  # personal data: never cached
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_summary_metrics_on_the_page_and_in_the_api_match_the_stored_calls(
    backend: httpx.Client,
) -> None:
    factory = cast(Any, backend).app.state.session_factory
    done = a_call(backend, mode="voice")
    end(backend, done)
    quit_ = a_call(backend, mode="voice")
    end(backend, quit_, outcome="dropped", reason="caller silent")
    held = a_call(backend, mode="voice")
    backend.post(
        f"/calls/{held}/escalations",
        json={"reason": "caller asked for a human"},
        headers={"Idempotency-Key": "k1"},
    )
    end(backend, held)
    with factory() as session:
        for ms in (1000, 2000, 3000):
            session.add(CallTurnTiming(call_id=done, total_from_speech_end_ms=ms))
        session.commit()

    metrics: Json = backend.get("/dashboard/api/metrics").json()
    page = Page(backend.get("/dashboard").text)

    assert metrics["completion"]["rate"] == round(1 / 3, 4)
    assert metrics["latency"]["p95_ms"] == 3000
    assert metrics["escalation_reasons"] == [{"reason": "caller asked for a human", "count": 1}]
    assert page.text_of(**{"data-metric": "completion-rate"}) == "33.3%"
    assert page.text_of(**{"data-metric": "p95-latency"}) == "3000 ms"
    assert page.where(**{"data-metric": "cost-per-minute"})
    assert page.where(**{"data-flag": "placeholder-price", "data-scope": "metrics"})
    [(_, day_text)] = page.where(**{"data-row": "calls-per-day"})
    assert day_text.endswith("3")
    [(_, reason_text)] = page.where(**{"data-row": "escalation-reason"})
    assert reason_text == "caller asked for a human 1"
