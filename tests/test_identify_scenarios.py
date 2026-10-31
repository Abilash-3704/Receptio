"""Identification, reschedule and cancel scenarios (including tricky call 14), and the
`revealed_before_identification` rule, through the scenario runner."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from agent.text_mode import Turn
from agent.tools import ToolCall
from evals.runner import Observation, load_scenario, run_scenario, violations_of
from evals.state import AppointmentRow, FinalState
from tests.support import IST

SCENARIOS = Path(__file__).parent.parent / "evals" / "scenarios"


def session_factory(backend: httpx.Client) -> Any:
    return cast(Any, backend).app.state.session_factory


@pytest.mark.parametrize(
    "scenario_file",
    [
        "cancel_identified_016.yaml",
        "wrong_identity_cancel_014.yaml",
        "reschedule_identified_016.yaml",
        "reschedule_declined_016.yaml",
    ],
)
def test_the_identification_scenarios_pass_against_the_real_backend(
    backend: httpx.Client, scenario_file: str
) -> None:
    scenario = load_scenario(SCENARIOS / scenario_file)

    result = run_scenario(scenario, backend, session_factory(backend))

    assert result.failures == []


def test_a_scenario_expecting_a_cancel_fails_when_the_caller_never_says_yes(
    backend: httpx.Client,
) -> None:
    scenario = load_scenario(SCENARIOS / "cancel_identified_016.yaml")
    hung_up = scenario.model_copy(update={"turns": scenario.turns[:1]})

    result = run_scenario(hung_up, backend, session_factory(backend))

    assert any("appointments_cancelled" in f for f in result.failures)


def existing_row() -> AppointmentRow:
    start = datetime(2026, 11, 3, 10, 0, tzinfo=IST)
    return AppointmentRow(
        id=1,
        service="Filling",
        provider="Dr. Anita Rao",
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        provider_id=1,
        patient_id=1,
        patient="Seeded Patient",
        patient_phone="+910000000000",
        read_back_recorded=False,
    )


def observation(speech: str, trace: list[ToolCall]) -> Observation:
    state = FinalState([existing_row()], 0, ["Seeded Patient"], [])
    base = Observation(state, state, tool_calls=[], transcript=[], caller_turns=1)
    return replace(base, tool_calls=trace, transcript=[Turn("agent", speech)])


def identified(ok: bool) -> ToolCall:
    return ToolCall("identify_patient", {}, ok=ok, refused=False)


def test_the_rule_flags_an_appointment_detail_spoken_without_a_successful_identification() -> None:
    leak = "You have a Filling on Tuesday 3 November at 10:00 AM."

    assert violations_of("revealed_before_identification", observation(leak, []))
    assert violations_of("revealed_before_identification", observation(leak, [identified(False)]))
    name = "Is this about the appointment for Seeded Patient?"
    assert violations_of("revealed_before_identification", observation(name, [identified(False)]))


def test_the_rule_allows_the_same_speech_after_a_successful_identification() -> None:
    leak = "You have a Filling on Tuesday 3 November at 10:00 AM."

    assert (
        violations_of("revealed_before_identification", observation(leak, [identified(True)])) == []
    )
    ask = "Could I have your full name and phone number?"
    assert violations_of("revealed_before_identification", observation(ask, [])) == []
