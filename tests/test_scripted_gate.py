"""The scripted suite as a CI gate (ticket 15): deterministic, whatever day it is run."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from evals.harness import SCENARIOS, load_scenarios, run_suite
from evals.runner import run_scenario
from tests.support import FakeClock


def test_the_suite_does_not_depend_on_the_day_it_is_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent's clock must be the backend's fixed clock, not the machine's. Simulate a CI run
    on another day by making the machine clock a Tuesday in December."""
    december = FakeClock(datetime(2026, 12, 15, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
    monkeypatch.setattr(run_scenario, "__defaults__", (december, None, None))

    suite = run_suite(load_scenarios(SCENARIOS))

    # Known-gap probes are expected to miss; they are not part of the gate.
    failed = {s.id: r.failures for s, r in suite.runs if not r.passed and not s.known_gap}
    assert failed == {}
