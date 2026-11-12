"""Scenarios that move the clock: a Hold that lapses while the Caller thinks (tricky call 13) and
one that lapses after the Caller hangs up (tricky call 15)."""

from typing import Any, cast

import httpx

from evals.runner import Scenario, ScenarioResult, run_scenario
from tests.support import FakeClock

HOLD_AND_READ_BACK: list[dict[str, Any]] = [
    {"tool": "check_availability", "args": {"service": "Cleaning", "when": "2026-11-03"}},
    {"say": "I can do {offered[0]}. Does that suit you?"},
    {"tool": "record_patient", "args": {"name": "Priya Sharma", "phone": "+91 98765 43210"}},
    {"tool": "place_hold", "args": {"option": 1}},
    {"tool": "read_back"},
]


def scenario(**fields: Any) -> Scenario:
    base: dict[str, Any] = {
        "id": "clock",
        "description": "d",
        "persona": "polite_rushed",
        "turns": [
            "A cleaning tomorrow please",
            "That works, I'm Priya Sharma, +91 98765 43210",
        ],
        "agent_script": HOLD_AND_READ_BACK,
        "expected": {"final_state": {}, "max_turns": 2},
    }
    return Scenario.model_validate({**base, **fields})


def run(backend: httpx.Client, clock: FakeClock, s: Scenario) -> ScenarioResult:
    factory = cast(Any, backend).app.state.session_factory
    return run_scenario(s, backend, factory, clock)


def test_after_the_call_the_clock_can_move_so_an_unconfirmed_hold_is_seen_to_lapse(
    backend: httpx.Client, clock: FakeClock
) -> None:
    kept = scenario(expected={"final_state": {"live_holds": 1}, "max_turns": 2})

    assert run(backend, clock, kept).failures == []


def test_a_hold_that_outlives_the_call_lapses_on_its_own(
    backend: httpx.Client, clock: FakeClock
) -> None:
    lapsed = scenario(
        after_call_minutes=6,
        expected={"final_state": {"live_holds": 0, "appointment_confirmed": False}, "max_turns": 2},
    )

    assert run(backend, clock, lapsed).failures == []


def test_a_scenario_can_advance_the_clock_before_a_caller_turn(
    backend: httpx.Client, clock: FakeClock
) -> None:
    # The Caller says yes six minutes after the read-back: the Hold has gone, so the yes books
    # nothing, and the agent is told so.
    late_yes = scenario(
        turns=[
            "A cleaning tomorrow please",
            "That works, I'm Priya Sharma, +91 98765 43210",
            "Yes",
        ],
        advance_minutes={2: 6},
        agent_script=[*HOLD_AND_READ_BACK, {"tool": "confirm_appointment"}, {"say": "Sorry."}],
        expected={
            "final_state": {"appointment_confirmed": False, "live_holds": 0},
            "must_not": ["confirm_without_readback"],
            "max_turns": 3,
        },
    )

    result = run(backend, clock, late_yes)

    assert result.failures == []
