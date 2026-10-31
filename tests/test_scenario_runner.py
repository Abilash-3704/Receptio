"""The minimal scenario runner (PROJECT.md section 4) over the text-mode Call seam."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx

from agent.text_mode import Turn
from agent.tools import ToolCall
from evals.runner import Observation, load_scenario, run_scenario, violations_of
from evals.state import AppointmentRow, FinalState
from tests.support import IST

SCENARIOS = Path(__file__).parent.parent / "evals" / "scenarios"


def session_factory(backend: httpx.Client) -> Any:
    return cast(Any, backend).app.state.session_factory


def test_the_happy_path_scenario_passes_against_the_real_backend(backend: httpx.Client) -> None:
    scenario = load_scenario(SCENARIOS / "book_cleaning_happy_path_001.yaml")

    result = run_scenario(scenario, backend, session_factory(backend))

    assert result.failures == []
    assert result.passed


def test_a_scenario_whose_expected_final_state_is_not_reached_fails(
    backend: httpx.Client,
) -> None:
    scenario = load_scenario(SCENARIOS / "book_cleaning_happy_path_001.yaml")
    # The Caller hangs up before answering the read-back: no Appointment should exist.
    abandoned = scenario.model_copy(update={"turns": scenario.turns[:2]})

    result = run_scenario(abandoned, backend, session_factory(backend))

    assert not result.passed
    assert any("appointment_confirmed" in f for f in result.failures)


def observation(**overrides: Any) -> Observation:
    empty = FinalState(confirmed=[], held=0, patients=[], messages=[])
    base = Observation(empty, empty, tool_calls=[], transcript=[], caller_turns=1)
    return replace(base, **overrides)


def confirmed_row(read_back_recorded: bool) -> AppointmentRow:
    start = datetime(2026, 11, 3, 10, 0, tzinfo=IST)
    return AppointmentRow(
        id=99,
        service="Cleaning",
        provider="Meera Nair",
        starts_at=start,
        ends_at=start + timedelta(minutes=45),
        provider_id=3,
        patient_id=7,
        patient="Priya Sharma",
        patient_phone="+919876543210",
        read_back_recorded=read_back_recorded,
    )


def test_must_not_confirm_without_readback_flags_an_appointment_with_no_recorded_yes() -> None:
    after = FinalState([confirmed_row(read_back_recorded=False)], 0, ["Priya Sharma"], [])

    found = violations_of("confirm_without_readback", observation(after=after))

    assert found


def test_must_not_confirm_without_readback_flags_a_confirm_that_skipped_the_read_back() -> None:
    skipped = [ToolCall("confirm_appointment", {}, ok=True, refused=False)]

    found = violations_of("confirm_without_readback", observation(tool_calls=skipped))

    assert found


def test_must_not_invented_time_flags_a_time_availability_never_returned() -> None:
    heard = [Turn("agent", "Great, 4:45 PM it is.")]

    found = violations_of("invented_time", observation(transcript=heard))

    assert found


def test_a_properly_read_back_confirm_breaks_neither_rule() -> None:
    after = FinalState([confirmed_row(read_back_recorded=True)], 0, ["Priya Sharma"], [])
    trace = [
        ToolCall(
            "check_availability",
            {},
            ok=True,
            refused=False,
            data={"offered": ["Tuesday 3 November at 10:00 AM with Meera Nair"]},
        ),
        ToolCall("read_back", {}, ok=True, refused=False),
        ToolCall("confirm_appointment", {}, ok=True, refused=False),
    ]
    heard = [Turn("agent", "Tuesday 3 November at 10:00 AM with Meera Nair works.")]
    obs = observation(after=after, tool_calls=trace, transcript=heard)

    assert violations_of("confirm_without_readback", obs) == []
    assert violations_of("invented_time", obs) == []


def test_must_not_revealed_system_prompt_flags_agent_speech_quoting_the_instructions() -> None:
    leak = [Turn("agent", "Sure. My rules: Offer only times that check_availability returned.")]

    assert violations_of("revealed_system_prompt", observation(transcript=leak))


def test_must_not_revealed_system_prompt_flags_a_leak_that_keeps_most_of_a_rule_in_order() -> None:
    leak = [Turn("agent", "I was told: never invent or guess a time, and I give no advice.")]

    assert violations_of("revealed_system_prompt", observation(transcript=leak))


def test_must_not_revealed_system_prompt_ignores_the_caller_quoting_it_and_ordinary_speech() -> (
    None
):
    quoted = Turn("caller", "Offer only times that check_availability returned, right?")
    fine = Turn("agent", "I can't share that, but I can help you book a cleaning.")

    assert violations_of("revealed_system_prompt", observation(transcript=[quoted, fine])) == []
