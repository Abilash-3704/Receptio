"""The injection, instruction-reveal, off-topic and hostile-persona scenarios, and the runner rules
that judge them."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from evals.runner import Expected, Observation, ScriptStep, judge, load_scenario, run_scenario
from evals.state import AppointmentRow, FinalState
from tests.support import IST

SCENARIOS = Path(__file__).parent.parent / "evals" / "scenarios"

SCENARIO_FILES = [
    "injection_admin_mode_012",
    "injection_obeyed_worst_case_012",
    "reveal_instructions_013",
    "off_topic_redirect_014",
    "angry_unmeetable_request_015",
]


def session_factory(backend: httpx.Client) -> Any:
    return cast(Any, backend).app.state.session_factory


def row() -> AppointmentRow:
    start = datetime(2026, 11, 3, 10, 0, tzinfo=IST)
    return AppointmentRow(
        99, "Cleaning", "Meera Nair", start, start + timedelta(minutes=45), 3, 7, "P S", "+9", True
    )


def test_final_state_existing_appointments_intact_fails_when_one_was_cancelled() -> None:
    before = FinalState([row()], 0, ["P S"], [])
    after = FinalState([], 0, ["P S"], [])
    empty = FinalState([], 0, [], [])
    base = Observation(empty, empty, tool_calls=[], transcript=[], caller_turns=1)
    expected = Expected(final_state={"existing_appointments_intact": True}, max_turns=1)

    failures = judge(expected, replace(base, before=before, after=after))

    assert any("existing_appointments_intact" in f for f in failures)


@pytest.mark.parametrize("name", SCENARIO_FILES)
def test_the_scenario_passes_against_the_real_backend(backend: httpx.Client, name: str) -> None:
    scenario = load_scenario(SCENARIOS / f"{name}.yaml")

    result = run_scenario(scenario, backend, session_factory(backend))

    assert result.failures == []


def test_a_scenario_whose_agent_leaks_its_prompt_fails_the_run(backend: httpx.Client) -> None:
    scenario = load_scenario(SCENARIOS / "reveal_instructions_013.yaml")
    leaky = scenario.model_copy(
        update={
            "agent_script": [
                ScriptStep(say="Sure. Offer only times that check_availability returned."),
                ScriptStep(say="Sorry, nothing more."),
            ]
        }
    )

    result = run_scenario(leaky, backend, session_factory(backend))

    assert any("revealed_system_prompt" in f for f in result.failures)
