"""Alert evaluation over persisted data (ticket 17's tables), seeded through the backend API.

Seam: `evaluate_alerts(session, settings, now)` returns the alerts firing now. Thresholds are
configuration. The data is made through the same HTTP endpoints the agent and worker write with,
at moments set by the fake clock, so what is evaluated is what a real run would have stored."""

import json
import logging
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any, cast

import httpx
import pytest

from backend.app.alerts import Alert, emit, evaluate_alerts, main
from backend.app.config import BackendSettings
from tests.support import FakeClock

Evaluate = Callable[..., list[Alert]]


def settings(**overrides: Any) -> BackendSettings:
    return BackendSettings(database_url="sqlite+pysqlite:///:memory:", **overrides)


def seed_call(
    backend: httpx.Client,
    totals: Sequence[int | None] = (),
    tools: Sequence[tuple[str, bool, bool]] = (),
    outcome: str | None = "completed",
) -> int:
    """One Call with a timing per entry of `totals` (total_from_speech_end_ms), a tool call per
    (name, ok, refused), ended with `outcome`."""
    call_id: int = backend.post("/calls", json={"mode": "voice"}).json()["id"]
    for n, total in enumerate(totals, start=1):
        backend.post(f"/calls/{call_id}/turns", json={"speaker": "caller", "text": f"turn {n}"})
        timing = {"caller_turn_seq": n, "total_from_speech_end_ms": total}
        backend.post(f"/calls/{call_id}/turn-timings", json=timing).raise_for_status()
    for name, ok, refused in tools:
        body: dict[str, Any] = {
            "turn_seq": 1,
            "name": name,
            "args": {},
            "ok": ok,
            "refused": refused,
            "result": {},
        }
        backend.post(f"/calls/{call_id}/tool-calls", json=body).raise_for_status()
    if outcome is not None:
        backend.post(f"/calls/{call_id}/end", json={"outcome": outcome})
    return call_id


@pytest.fixture
def evaluate(backend: httpx.Client, clock: FakeClock) -> Evaluate:
    factory = cast(Any, backend).app.state.session_factory

    def run(**overrides: Any) -> list[Alert]:
        with factory() as session:
            return evaluate_alerts(session, settings(**overrides), clock())

    return run


def rules(alerts: list[Alert]) -> set[str]:
    return {a.rule for a in alerts}


def test_a_quiet_system_fires_nothing(backend: httpx.Client, evaluate: Evaluate) -> None:
    for _ in range(4):
        seed_call(backend, totals=[700, 800, 900], tools=[("search_faq", True, False)] * 3)

    assert evaluate() == []


def test_a_latency_spike_fires_on_the_p95_of_speech_end_to_audio(
    backend: httpx.Client, evaluate: Evaluate
) -> None:
    for _ in range(5):
        seed_call(backend, totals=[900, 3200])  # p95 of ten turns is the slow one

    alerts = evaluate(alert_latency_p95_ms=1500, alert_min_turns=10)

    spike = next(a for a in alerts if a.rule == "latency_p95")
    assert spike.value == 3200 and spike.threshold == 1500
    assert spike.sample_size == 10
    assert spike.severity == "critical"


def test_the_median_has_its_own_threshold(backend: httpx.Client, evaluate: Evaluate) -> None:
    for _ in range(5):
        seed_call(backend, totals=[1000, 1100])  # all under p95's 1500, all over p50's 800

    alerts = evaluate(alert_latency_p50_ms=800, alert_latency_p95_ms=1500, alert_min_turns=10)

    assert rules(alerts) == {"latency_p50"}


def test_too_few_turns_is_not_a_spike(backend: httpx.Client, evaluate: Evaluate) -> None:
    seed_call(backend, totals=[9000, 9000, 9000])

    assert evaluate(alert_min_turns=10) == []


def test_turns_without_audio_are_left_out(backend: httpx.Client, evaluate: Evaluate) -> None:
    for _ in range(5):
        seed_call(backend, totals=[600, None])  # interrupted turns have no total

    assert evaluate(alert_min_turns=5) == []


def test_old_turns_age_out_of_the_window(
    backend: httpx.Client, evaluate: Evaluate, clock: FakeClock
) -> None:
    for _ in range(6):
        seed_call(backend, totals=[5000, 5000])
    assert "latency_p95" in rules(evaluate(alert_window_minutes=15, alert_min_turns=10))

    clock.advance(timedelta(minutes=16))

    assert evaluate(alert_window_minutes=15, alert_min_turns=10) == []


def test_elevated_tool_failure_rate_fires_and_names_the_worst_tool(
    backend: httpx.Client, evaluate: Evaluate
) -> None:
    for _ in range(4):
        seed_call(
            backend,
            tools=[
                ("find_availability", False, False),
                ("find_availability", False, False),
                ("search_faq", True, False),
            ],
        )

    alerts = evaluate(alert_tool_failure_rate=0.3, alert_min_tool_calls=10)

    failing = next(a for a in alerts if a.rule == "tool_failure_rate")
    assert failing.sample_size == 12
    assert failing.value == pytest.approx(8 / 12)
    assert failing.detail["worst_tool"] == "find_availability"
    assert failing.detail["failures_by_tool"] == {"find_availability": 8}


def test_a_refusal_by_the_state_machine_is_not_a_failure(
    backend: httpx.Client, evaluate: Evaluate
) -> None:
    for _ in range(5):
        seed_call(backend, tools=[("book_appointment", False, True), ("search_faq", True, False)])

    assert evaluate(alert_tool_failure_rate=0.1, alert_min_tool_calls=10) == []


def test_too_few_tool_calls_is_not_a_failure_rate(
    backend: httpx.Client, evaluate: Evaluate
) -> None:
    seed_call(backend, tools=[("search_faq", False, False)] * 3)

    assert evaluate(alert_min_tool_calls=10) == []


def test_a_burst_of_dropped_calls_fires(backend: httpx.Client, evaluate: Evaluate) -> None:
    for _ in range(8):
        seed_call(backend, outcome="dropped")
    for _ in range(2):
        seed_call(backend, outcome="completed")

    alerts = evaluate(alert_dropped_call_rate=0.5, alert_min_calls=10)

    dropped = next(a for a in alerts if a.rule == "dropped_call_rate")
    assert dropped.value == pytest.approx(0.8)
    assert dropped.sample_size == 10


def test_thresholds_come_from_configuration(backend: httpx.Client, evaluate: Evaluate) -> None:
    for _ in range(5):
        seed_call(backend, totals=[2000, 2000])

    assert "latency_p95" in rules(evaluate(alert_latency_p95_ms=1500, alert_min_turns=10))
    assert "latency_p95" not in rules(evaluate(alert_latency_p95_ms=2500, alert_min_turns=10))


def test_alerts_are_emitted_as_structured_log_lines_and_json(
    backend: httpx.Client, evaluate: Evaluate, caplog: pytest.LogCaptureFixture
) -> None:
    for _ in range(5):
        seed_call(backend, totals=[4000, 4000])
    alerts = evaluate(alert_min_turns=10)

    with caplog.at_level(logging.WARNING, logger="receptio.alerts"):
        emitted = emit(alerts)

    record = json.loads(caplog.records[0].getMessage())
    assert record["event"] == "alert" and record["rule"] == "latency_p95"
    assert record["value"] == 4000
    assert json.loads(emitted)["alerts"][0]["rule"] == "latency_p95"
    assert json.loads(emitted)["firing"] is True


def test_no_transcript_or_personal_data_is_in_an_alert(
    backend: httpx.Client, evaluate: Evaluate
) -> None:
    for _ in range(5):
        seed_call(backend, totals=[4000, 4000], tools=[("find_availability", False, False)] * 3)

    text = json.dumps([a.as_dict() for a in evaluate(alert_min_turns=10, alert_min_tool_calls=5)])

    assert "turn 1" not in text


def test_the_command_prints_json_and_exits_nonzero_only_when_something_fires(
    backend: httpx.Client, clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    factory = cast(Any, backend).app.state.session_factory

    assert main(["--min-turns", "4"], session_factory=factory, now=clock()) == 0
    assert json.loads(capsys.readouterr().out) == {"firing": False, "alerts": []}

    for _ in range(3):
        seed_call(backend, totals=[4000, 4000])

    assert main(["--min-turns", "4"], session_factory=factory, now=clock()) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["firing"] is True and printed["alerts"][0]["rule"] == "latency_p95"
