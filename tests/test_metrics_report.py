"""The metrics report (ticket 14), from hand-built results so every number is checkable by eye."""

import json
from pathlib import Path
from typing import Any

import pytest

from agent.text_mode import Turn
from evals.baseline import baseline_from, compare_to_baseline
from evals.config import EvalSettings
from evals.metering import Ledger, PriceBook
from evals.report import build_report, render_markdown, write_report
from evals.runner import Scenario, ScenarioResult

SETTINGS = EvalSettings(_env_file=None)  # type: ignore[call-arg]


def scenario(id: str, safety: str = "none", known_gap: bool = False) -> Scenario:
    return Scenario.model_validate(
        {
            "id": id,
            "description": id,
            "persona": "p",
            "turns": ["x"],
            "safety": safety,
            "known_gap": known_gap,
            "expected": {"max_turns": 1},
        }
    )


def result(
    id: str,
    observed: str = "none",
    failures: list[str] | None = None,
    escalations: tuple[tuple[str, str], ...] = (),
    wrong_time: bool = False,
    caller_turns: int = 3,
) -> ScenarioResult:
    return ScenarioResult(
        id,
        failures or [],
        observed,
        wrong_time=wrong_time,
        escalations=escalations,
        caller_turns=caller_turns,
        transcript=(Turn("caller", "hello"),),
    )


# 12 scenarios: 2 Emergency (one missed), 2 Urgent (both caught), 4 negatives (one flagged as
# Emergency, one as Urgent), 2 other (one fails, one has a wrong time), and 2 known-gap probes.
RUNS = [
    (
        scenario("e1", "emergency"),
        result("e1", "emergency", escalations=(("handoff", "Emergency"),)),
    ),
    (scenario("e2", "emergency"), result("e2", "none", failures=["no script"])),
    (
        scenario("u1", "urgent"),
        result("u1", "urgent", escalations=(("callback_request", "Urgent Case"),)),
    ),
    (
        scenario("u2", "urgent"),
        result("u2", "urgent", escalations=(("callback_request", "Urgent Case"),)),
    ),
    (scenario("n1"), result("n1")),
    (scenario("n2"), result("n2")),
    (scenario("n3"), result("n3", "emergency", failures=["flagged"])),
    (scenario("n4"), result("n4", "urgent", failures=["flagged"])),
    (scenario("b1"), result("b1")),
    (scenario("b2"), result("b2", wrong_time=True, failures=["bad"])),
    (scenario("p1", "emergency", known_gap=True), result("p1", "none", failures=["miss"])),
    (scenario("p2", "none", known_gap=True), result("p2", "urgent", failures=["fp"])),
]


def report(**kw: Any) -> dict[str, Any]:
    return build_report(RUNS, mode="scripted", settings=SETTINGS, **kw)


def metric(rep: dict[str, Any], name: str) -> Any:
    return rep["metrics"][name]["value"]


def test_completion_and_wrong_time_rates_exclude_the_known_gap_probes() -> None:
    rep = report()

    # 10 core scenarios; failing: e2, n3, n4, b2 -> 6 pass
    assert metric(rep, "completion_rate") == pytest.approx(0.6)
    assert metric(rep, "wrong_time_rate") == pytest.approx(0.1)  # b2 only
    assert rep["metrics"]["completion_rate"]["target"] == 0.9
    assert rep["metrics"]["completion_rate"]["status"] == "fail"
    assert rep["metrics"]["wrong_time_rate"]["status"] == "fail"  # target 0


def test_recall_and_false_positive_rates_sit_side_by_side() -> None:
    rep = report()

    assert metric(rep, "emergency_recall") == pytest.approx(0.5)
    assert metric(rep, "urgent_recall") == pytest.approx(1.0)
    # negatives (safety none, core): n1..n4, b1, b2 -> 6; n3 got Emergency, n4 got Urgent
    assert metric(rep, "emergency_false_positive_rate") == pytest.approx(1 / 6)
    assert metric(rep, "urgent_false_positive_rate") == pytest.approx(1 / 6)
    assert rep["metrics"]["emergency_recall"]["status"] == "fail"
    assert rep["metrics"]["urgent_recall"]["status"] == "pass"
    assert rep["metrics"]["emergency_false_positive_rate"]["status"] == "tracked"
    assert rep["metrics"]["emergency_recall"]["total"] == 2


def test_known_gaps_are_reported_apart_from_the_targets() -> None:
    rep = report()

    probes = {p["id"]: p for p in rep["known_gaps"]["probes"]}
    assert set(probes) == {"p1", "p2"}
    assert probes["p1"]["truth"] == "emergency" and probes["p1"]["observed"] == "none"
    assert probes["p1"]["handled_correctly"] is False
    # and they moved no target number: Emergency total is still 2, not 3
    assert rep["metrics"]["emergency_recall"]["total"] == 2


def test_escalation_rate_and_reasons_split_handoff_from_callback_request() -> None:
    rep = report()["escalations"]

    assert rep["rate"] == pytest.approx(3 / 10)
    assert rep["by_kind"] == {"handoff": 1, "callback_request": 2}
    assert rep["by_reason"] == {"Emergency": 1, "Urgent Case": 2}


def test_scripted_runs_have_no_llm_cost_and_latency_is_a_marked_voice_only_field() -> None:
    rep = report()

    assert rep["cost"]["status"] == "not_applicable_scripted"
    assert rep["cost"]["per_call_usd"] is None
    assert rep["latency"]["status"] == "not_measured"
    assert "voice" in rep["latency"]["note"].lower()
    assert rep["latency"]["p50_ms"] is None and rep["latency"]["p95_ms"] is None


def test_cost_per_call_is_tokens_times_configured_prices_and_flags_placeholders() -> None:
    ledger = Ledger()
    ledger.add("m", 1_000_000, 100_000)  # 2.0 + 1.0 = 3.0 USD at (2, 10)
    book = PriceBook({"m": (2.0, 10.0)}, placeholder=True)
    runs = RUNS[:6]  # 6 Calls

    rep = build_report(
        runs, mode="simulated", settings=SETTINGS, ledger=ledger, prices=book, persona="rushed"
    )

    cost = rep["cost"]
    assert cost["total_usd"] == pytest.approx(3.0)
    assert cost["per_call_usd"] == pytest.approx(0.5)
    # 3 turns x 20 s (placeholder assumption) = 1 minute per Call
    assert cost["assumed_minutes_per_call"] == pytest.approx(1.0)
    assert cost["per_minute_usd"] == pytest.approx(0.5)
    assert cost["per_3_minute_call_usd"] == pytest.approx(1.5)
    assert cost["prices_are_placeholders"] is True
    assert cost["covers"] == "LLM tokens only; speech, telephony and hosting are not included"
    assert rep["persona"] == "rushed"


def test_an_aborted_run_is_marked_so_it_cannot_pass_for_a_complete_one() -> None:
    rep = report(aborted="request cap of 5 requests reached")

    assert rep["aborted"] == "request cap of 5 requests reached"
    assert "ABORTED" in render_markdown(rep)


def test_the_markdown_has_every_metric_and_the_known_gaps() -> None:
    text = render_markdown(report())

    for needle in (
        "Task completion rate",
        "Wrong-time or overlapping",
        "Emergency recall",
        "Urgent Case recall",
        "Emergency false-positive rate",
        "Urgent Case false-positive rate",
        "Escalation rate",
        "Cost per Call",
        "Latency",
        "not measured",
        "Known gaps",
        "p1",
    ):
        assert needle in text, needle


def test_reports_are_written_as_markdown_and_json_under_the_directory(tmp_path: Path) -> None:
    md, js = write_report(report(), tmp_path)

    assert md.name == "metrics-scripted.md" and js.name == "metrics-scripted.json"
    assert json.loads(js.read_text())["metrics"]["completion_rate"]["value"] == pytest.approx(0.6)
    assert "Task completion rate" in md.read_text()


def test_the_json_carries_each_scenario_and_transcripts_only_for_simulated_runs() -> None:
    scripted = report()
    simulated = build_report(RUNS, mode="simulated", settings=SETTINGS)

    assert [s["id"] for s in scripted["scenarios"]][:2] == ["e1", "e2"]
    assert "transcript" not in scripted["scenarios"][0]
    assert simulated["scenarios"][0]["transcript"] == [{"speaker": "caller", "text": "hello"}]


GOOD = [(s, result(s.id, s.safety)) for s, _ in RUNS]


def test_a_baseline_holds_the_gated_numbers() -> None:
    base = baseline_from(build_report(GOOD, "scripted", SETTINGS))

    assert base["schema_version"] == 1 and base["mode"] == "scripted"
    assert base["metrics"]["completion_rate"] == 1.0
    assert base["metrics"]["emergency_recall"] == 1.0
    assert "escalation_rate" not in base["metrics"]  # tracked, not gated
    assert "cost_per_call_usd" not in base["metrics"]
    assert compare_to_baseline(build_report(GOOD, "scripted", SETTINGS), base) == []


def test_a_worse_run_is_flagged_against_the_baseline_and_its_targets() -> None:
    base = baseline_from(build_report(GOOD, "scripted", SETTINGS))

    regressions = compare_to_baseline(report(), base)

    assert any(r.startswith("completion_rate") and "baseline" in r for r in regressions)
    assert any(r.startswith("emergency_recall") for r in regressions)
    assert any(r.startswith("emergency_false_positive_rate") for r in regressions)
    assert any("misses its target" in r for r in regressions)


def test_a_tolerance_forgives_small_drops_in_a_noisy_simulated_run() -> None:
    base = {"schema_version": 1, "mode": "scripted", "metrics": {"completion_rate": 0.95}}
    runs = GOOD[:9] + [(scenario("x"), result("x", failures=["f"]))]
    rep = build_report(runs, "scripted", SETTINGS)  # 9 of 10 pass: 0.9

    assert any("worse than" in r for r in compare_to_baseline(rep, base))
    assert not any("worse than" in r for r in compare_to_baseline(rep, base, tolerance=0.06))


def test_a_baseline_for_another_mode_is_not_comparable() -> None:
    base = baseline_from(build_report(GOOD, "simulated", SETTINGS))

    assert "mode" in compare_to_baseline(report(), base)[0]


def test_a_suite_that_shrinks_below_the_baseline_is_a_regression() -> None:
    # Deleting (or relabelling as a known gap) a failing scenario must not make the gate pass.
    base = baseline_from(build_report(GOOD, "scripted", SETTINGS))
    assert base["scenarios"] == sum(1 for s, _ in GOOD if not s.known_gap)

    smaller = build_report(GOOD[1:], "scripted", SETTINGS)

    assert any(r.startswith("scenarios") for r in compare_to_baseline(smaller, base))
    grown = build_report(GOOD + [(scenario("extra"), result("extra"))], "scripted", SETTINGS)
    assert compare_to_baseline(grown, base) == []
