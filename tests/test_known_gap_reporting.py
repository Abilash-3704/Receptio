"""Known-gap probes that are not about the safety detector (a Caller phrasing the code misses, for
example a way of asking for a person) must read as missed when the scenario fails, even though the
safety level observed ('none') equals the truth ('none')."""

from agent.text_mode import Turn
from evals.config import EvalSettings
from evals.report import build_report
from evals.runner import Scenario, ScenarioResult

SETTINGS = EvalSettings(_env_file=None)  # type: ignore[call-arg]


def probe(failures: list[str]) -> list[tuple[Scenario, ScenarioResult]]:
    scenario = Scenario.model_validate(
        {
            "id": "p",
            "description": "p",
            "persona": "p",
            "turns": ["x"],
            "known_gap": True,
            "expected": {"max_turns": 1},
        }
    )
    return [(scenario, ScenarioResult("p", failures, "none", transcript=(Turn("caller", "x"),)))]


def handled(runs: list[tuple[Scenario, ScenarioResult]]) -> bool:
    report = build_report(runs, "scripted", SETTINGS)
    return report["known_gaps"]["probes"][0]["handled_correctly"]


def test_a_failed_probe_is_missed_even_when_the_safety_level_matches() -> None:
    assert handled(probe(failures=["escalation_kind: expected handoff, got None"])) is False


def test_a_passing_probe_is_handled() -> None:
    assert handled(probe(failures=[])) is True
