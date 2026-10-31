"""Output guard: the agent may only say clock times that Availability returned.

The LLM is never trusted to remember or work out times. Every Say is scanned for clock times;
one that was not returned by a tool this Call is withheld from the Caller and the LLM is told
why. This catches times written as digits ("9:30 AM", "14:00", "3 pm"), not times written out in
words; that gap is covered by the scenario suite's `invented_time` rule."""

import re
from datetime import datetime

from backend.app.clock import CLINIC_TZ

CLOCK_TIME = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\b\.?)"
    r"|\b(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d)\b",
    re.IGNORECASE,
)


def clock_times_in(text: str) -> list[tuple[int, int]]:
    """Clock times mentioned in `text` as (hour in 24h, minute)."""
    found: list[tuple[int, int]] = []
    for match in CLOCK_TIME.finditer(text):
        if match["meridiem"]:
            hour = int(match["hour"]) % 12
            if match["meridiem"].lower().startswith("p"):
                hour += 12
            found.append((hour, int(match["minute"] or 0)))
        else:
            found.append((int(match["h24"]), int(match["m24"])))
    return found


def unoffered_times(text: str, offered: set[datetime]) -> list[tuple[int, int]]:
    allowed = {(t.astimezone(CLINIC_TZ).hour, t.astimezone(CLINIC_TZ).minute) for t in offered}
    return [t for t in clock_times_in(text) if t not in allowed]
