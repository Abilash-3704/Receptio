"""The `offered_outside_clinic_hours` must_not rule: every time Availability returned, as the Caller
was told it, starts inside Clinic Hours (Monday to Saturday, 09:00 to 18:00)."""

from agent.tools import ToolCall
from evals.runner import Observation, violations_of
from evals.state import FinalState


def observation(*labels: str) -> Observation:
    empty = FinalState(confirmed=[], held=0, patients=[], messages=[])
    trace = [ToolCall("check_availability", {}, ok=True, refused=False, data={"offered": labels})]
    return Observation(empty, empty, tool_calls=trace, transcript=[], caller_turns=1)


def test_a_sunday_is_flagged() -> None:
    found = violations_of(
        "offered_outside_clinic_hours", observation("Sunday 8 November at 10:00 AM with Dr. Rao")
    )

    assert found


def test_a_time_before_opening_or_at_closing_is_flagged() -> None:
    early = observation("Tuesday 3 November at 8:30 AM with Dr. Rao")
    closing = observation("Tuesday 3 November at 6:00 PM with Dr. Rao")

    assert violations_of("offered_outside_clinic_hours", early)
    assert violations_of("offered_outside_clinic_hours", closing)


def test_times_inside_the_hours_are_fine() -> None:
    inside = observation(
        "Tuesday 3 November at 9:00 AM with Dr. Rao",
        "Saturday 7 November at 5:45 PM with Dr. Rao",
    )

    assert violations_of("offered_outside_clinic_hours", inside) == []
