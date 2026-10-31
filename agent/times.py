"""Understanding how Callers say times, as plain code in the clinic's timezone.

The LLM never works out a date or a time. It passes the Caller's words ("next Tuesday after
lunch") to `resolve`, which answers from an injectable `now`. Anything a sensible listener
would double check ("next Tuesday" is two different days on a Sunday) is flagged
`needs_confirmation`, and the agent must get the Caller to agree to the exact date aloud before
it looks at Availability."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from backend.app.clock import CLINIC_TZ

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tues": 1, "tue": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thurs": 3, "thur": 3, "thu": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}  # fmt: skip
MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sept", "sep"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}  # fmt: skip

# Vague parts of the day, as [from, to) in clinic local time. Offers are still limited to
# Clinic Hours by the backend.
DAY_PARTS = {
    "before lunch": (time(9), time(12)),
    "morning": (time(9), time(12)),
    "after lunch": (time(13), time(18)),
    "afternoon": (time(12), time(18)),
    "evening": (time(16), time(18)),
    "tonight": (time(16), time(18)),
}
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

_WEEKDAY = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY_NUMBER = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"


@dataclass(frozen=True)
class Resolution:
    dates: tuple[date, ...] = ()
    # The part of the day wanted, as [from, to), or None for any time.
    window: tuple[time, time] | None = None
    # One specific time wanted ("at 4 pm"): offer the nearest times from then on.
    exact: time | None = None
    # The Caller wants a specific moment, such as "in an hour" or "right now".
    moment: datetime | None = None
    # More than one date is plausible: `dates` holds the proposal, `alternatives` the others.
    needs_confirmation: bool = False
    alternatives: tuple[date, ...] = ()
    # The Caller gave a time of day but no day at all.
    needs_day: bool = False
    # The words could not be understood at all.
    unparsed: bool = False


def say_date(day: date) -> str:
    """How a date is said aloud, for example 'Tuesday 3 November'."""
    return f"{day:%A} {day.day} {day:%B}"


def _next_weekday(today: date, weekday: int, *, strictly_after: bool) -> date:
    ahead = (weekday - today.weekday()) % 7
    if ahead == 0 and strictly_after:
        ahead = 7
    return today + timedelta(days=ahead)


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _day_part(text: str) -> tuple[time, time] | None:
    for words, window in DAY_PARTS.items():
        if words in text:
            return window
    return None


def _exact_time(text: str) -> time | None:
    if "noon" in text:
        return time(12)
    meridiem = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b", text)
    if meridiem:
        hour = int(meridiem[1]) % 12 + (12 if meridiem[3] == "p" else 0)
        return time(hour, int(meridiem[2] or 0)) if hour < 24 else None
    clock = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text) or re.search(
        r"\b(?:at|around|about|make it) (\d{1,2})()\b", text
    )
    if clock is None:
        return None
    hour, minute = int(clock[1]), int(clock[2] or 0)
    if ":" in clock[0] and hour >= 13:
        return time(hour, minute)
    # No am or pm said: the clinic is open 9 to 6, so 9-11 is morning and 1-8 is afternoon.
    if 1 <= hour <= 8:
        hour += 12
    return time(hour, minute) if hour < 24 else None


def resolve(phrase: str, now: datetime) -> Resolution:
    """Turn the Caller's words into dates and times of day, relative to `now`."""
    text = " ".join(phrase.casefold().replace(",", " ").split())
    today = now.astimezone(CLINIC_TZ).date()
    window, exact = _day_part(text), _exact_time(text)

    def at(dates: tuple[date, ...] = (), **extra: Any) -> Resolution:
        return Resolution(dates, window=window, exact=exact, **extra)

    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        try:
            return at((date(int(iso[1]), int(iso[2]), int(iso[3])),))
        except ValueError:
            return Resolution(unparsed=True)

    named = re.search(rf"\b{_DAY_NUMBER} (?:of )?(?P<month>{_MONTH})\b", text) or re.search(
        rf"\b(?P<month>{_MONTH}) {_DAY_NUMBER}\b", text
    )
    if named:
        month, day = MONTHS[named["month"]], int(named["day"])
        try:
            found = date(today.year, month, day)
            if found < today:
                found = date(today.year + 1, month, day)
        except ValueError:
            return Resolution(unparsed=True)
        return at((found,))

    # "in 3 hours", "in 45 minutes", "right now": a specific moment, not a day.
    soon = re.search(
        r"\bin (a|an|one|two|three|four|five|\d+) (hour|hours|minute|minutes|mins?)\b", text
    )
    if soon:
        count = NUMBER_WORDS.get(soon[1]) or int(soon[1])
        delta = timedelta(hours=count) if soon[2].startswith("hour") else timedelta(minutes=count)
        moment = now + delta
        return at((moment.astimezone(CLINIC_TZ).date(),), moment=moment)
    if re.search(r"\b(right now|now|asap|immediately|as soon as possible|this minute)\b", text):
        return at((today,), moment=now)

    ahead = re.search(r"\bin (a|an|one|two|three|four|five|\d+) (day|days|week|weeks)\b", text)
    if ahead:
        count = NUMBER_WORDS.get(ahead[1]) or int(ahead[1])
        days = count * (7 if ahead[2].startswith("week") else 1)
        return at((today + timedelta(days=days),))

    if "day after tomorrow" in text:
        return at((today + timedelta(days=2),))
    if "tomorrow" in text:
        return at((today + timedelta(days=1),))
    if re.search(r"\b(today|tonight)\b", text):
        return at((today,))

    weekday = re.search(rf"\b(?:(?P<which>next|this|coming) )?(?P<wd>{_WEEKDAY})\b", text)
    if weekday:
        number = WEEKDAYS[weekday["wd"]]
        nearest = _next_weekday(today, number, strictly_after=False)
        if weekday["which"] == "next":
            following = _next_weekday(today, number, strictly_after=True)
            # "Next Tuesday" is the nearest Tuesday after today or the one after that.
            proposal = (
                following if _monday(following) > _monday(today) else following + timedelta(days=7)
            )
            other = following + timedelta(days=7) if proposal == following else following
            return at((proposal,), needs_confirmation=True, alternatives=(other,))
        if weekday["which"] == "this" and number < today.weekday():
            # Already gone by this week: the Caller most likely means next week's.
            return at((nearest,), needs_confirmation=True, alternatives=())
        if nearest == today:
            # "Tuesday" said on a Tuesday: today, or a week from today?
            return at((today,), needs_confirmation=True, alternatives=(today + timedelta(days=7),))
        return at((nearest,))

    if "next week" in text:
        start = _monday(today) + timedelta(days=7)
        return at(tuple(start + timedelta(days=i) for i in range(6)))
    if "this week" in text or "few days" in text:
        week = (_monday(today) + timedelta(days=i) for i in range(6))
        return at(tuple(d for d in week if d >= today))

    if window is not None or exact is not None:
        return at(needs_day=True)
    return Resolution(unparsed=True)
