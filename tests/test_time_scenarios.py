"""The time-handling scenarios (docs/tricky-calls.md 1, 3 and the booking window) run through the
minimal scenario runner against the real backend with the scripted LLM."""

from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from evals.runner import load_scenario, run_scenario
from tests.support import FakeClock

SCENARIOS = Path(__file__).parent.parent / "evals" / "scenarios"


@pytest.mark.parametrize(
    "name",
    [
        "correction_thursday_001",
        "ambiguous_next_tuesday_sunday_001",
        "too_soon_callback_001",
        "no_availability_sunday_001",
        "too_far_ahead_001",
    ],
)
def test_time_scenario_passes(name: str, backend: httpx.Client, clock: FakeClock) -> None:
    scenario = load_scenario(SCENARIOS / f"{name}.yaml")
    factory = cast(Any, backend).app.state.session_factory

    result = run_scenario(scenario, backend, factory, clock)

    assert result.failures == []
