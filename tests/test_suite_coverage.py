"""The scenario suite covers what the spec and docs/tricky-calls.md promise, and says so honestly
(ticket 15). These tests read the scenario files; they run nothing."""

from collections import Counter
from pathlib import Path

from evals.runner import load_scenario

SCENARIOS = Path(__file__).parent.parent / "evals" / "scenarios"
REPORT = Path(__file__).parent.parent / "docs" / "eval-report.md"
ALL = [load_scenario(p) for p in sorted(SCENARIOS.glob("*.yaml"))]
CORE = [s for s in ALL if not s.known_gap]

# The spec's categories (happy paths, interruptions, corrections, no Availability, a time taken
# during a Hold, angry, off-topic, Emergencies, Urgent Cases, injection, silence or a dropped Call)
# and the minimum number of core scenarios each must have.
REQUIRED = {
    "happy_path": 4,
    "interruption": 2,
    "correction": 3,
    "no_availability": 3,
    "hold_race": 2,
    "angry": 3,
    "off_topic": 2,
    "emergency": 10,
    "urgent": 10,
    "injection": 4,
    "silence_dropped": 2,
}


def test_the_suite_has_at_least_50_scenarios_and_each_id_matches_its_file() -> None:
    assert len(CORE) >= 50
    names = {p.stem for p in SCENARIOS.glob("*.yaml")}
    assert names == {s.id for s in ALL}


def test_every_scenario_has_a_category_and_each_spec_category_is_covered() -> None:
    assert [s.id for s in ALL if s.category is None] == []
    counts = Counter(s.category for s in CORE)
    thin = {c: (counts[c], need) for c, need in REQUIRED.items() if counts[c] < need}
    assert thin == {}, "category: (have, need)"


def test_all_15_tricky_calls_are_covered_by_a_scenario_that_counts() -> None:
    covered = {n for s in CORE for n in s.tricky_calls}
    assert covered == set(range(1, 16))


def test_the_report_says_which_scenario_covers_which_tricky_call() -> None:
    text = REPORT.read_text()
    missing = [f"{n}: {s.id}" for s in CORE for n in s.tricky_calls if f"`{s.id}`" not in text]
    assert missing == []
