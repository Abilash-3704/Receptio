"""Scenarios run with the simulated Caller, and what a result records for the metrics report."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import httpx

from agent.text_mode import Turn
from agent.tools import ToolCall
from evals.personas import load_personas
from evals.runner import (
    Observation,
    caller_goal,
    load_scenario,
    run_scenario,
    wrong_time,
)
from evals.simulated_caller import SimulatedCaller
from evals.state import AppointmentRow, FinalState
from tests.fake_anthropic import FakeAnthropic, replies
from tests.support import IST

SCENARIOS = Path(__file__).parent.parent / "evals" / "scenarios"


def session_factory(backend: httpx.Client) -> Any:
    return cast(Any, backend).app.state.session_factory


def test_a_simulated_caller_writes_its_own_words_and_the_scenario_is_still_judged(
    backend: httpx.Client,
) -> None:
    scenario = load_scenario(SCENARIOS / "emergency_chest_pain.yaml")
    client = FakeAnthropic(
        replies("My dad is clutching his chest, it is crushing and he can't breathe properly")
    )
    caller = SimulatedCaller(
        client, "sim", load_personas()["rushed"], caller_goal(scenario), max_turns=1
    )

    result = run_scenario(scenario, backend, session_factory(backend), caller=caller)

    spoken = [t.text for t in result.transcript if t.speaker == "caller"]
    assert spoken == ["My dad is clutching his chest, it is crushing and he can't breathe properly"]
    assert scenario.turns[0] not in spoken
    assert result.observed_safety == "emergency"
    assert result.passed, result.failures
    assert result.escalations and result.escalations[0][0] == "callback_request"
    assert result.caller_turns == 1


def test_the_goal_is_the_scenarios_caller_goal_or_else_its_description() -> None:
    plain = load_scenario(SCENARIOS / "emergency_chest_pain.yaml")
    with_goal = plain.model_copy(update={"caller_goal": "You are Priya, number 98765 43210."})

    assert caller_goal(plain) == plain.description
    assert caller_goal(with_goal) == "You are Priya, number 98765 43210."


def test_the_goal_does_not_leak_the_scripted_phrasings() -> None:
    scenario = load_scenario(SCENARIOS / "emergency_chest_pain.yaml")
    assert scenario.turns[0] not in caller_goal(scenario)


def test_a_scripted_run_records_zero_escalations_for_a_plain_booking(
    backend: httpx.Client,
) -> None:
    scenario = load_scenario(SCENARIOS / "book_cleaning_happy_path_001.yaml")

    result = run_scenario(scenario, backend, session_factory(backend))

    assert result.passed, result.failures
    assert result.escalations == ()
    assert result.wrong_time is False
    assert result.caller_turns == 3


def appointment(id: int, start: str, patient_id: int = 1, provider_id: int = 1) -> AppointmentRow:
    starts = datetime.fromisoformat(start).replace(tzinfo=IST)
    return AppointmentRow(
        id=id,
        service="Cleaning",
        provider="Dr A",
        starts_at=starts,
        ends_at=starts.replace(minute=starts.minute + 30),
        provider_id=provider_id,
        patient_id=patient_id,
        patient="P",
        patient_phone="1",
        read_back_recorded=True,
    )


def observation(after: list[AppointmentRow], offered: list[str]) -> Observation:
    empty = FinalState(confirmed=[], held=0, patients=[], messages=[])
    return Observation(
        before=empty,
        after=replace(empty, confirmed=after),
        tool_calls=[
            ToolCall("check_availability", {}, True, False, {"offered": offered}),
        ],
        transcript=[Turn("agent", "hello")],
        caller_turns=1,
    )


def test_wrong_time_is_an_overlap_or_an_appointment_at_a_time_never_offered() -> None:
    offered = ["Tuesday 3 November at 10:00 AM"]

    ok = observation([appointment(1, "2026-11-03T10:00")], offered)
    overlap = observation(
        [appointment(1, "2026-11-03T10:00"), appointment(2, "2026-11-03T10:15", provider_id=2)],
        offered,
    )
    invented = observation([appointment(1, "2026-11-03T16:00")], offered)

    assert wrong_time(ok) is False
    assert wrong_time(overlap) is True
    assert wrong_time(invented) is True
