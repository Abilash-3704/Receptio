"""Deterministic seam: how Callers' words become dates and times of day in Asia/Kolkata."""

from datetime import UTC, date, datetime, time

import pytest

from agent.times import resolve, say_date
from tests.support import IST

SUNDAY = datetime(2026, 11, 1, 11, 0, tzinfo=IST)
MONDAY = datetime(2026, 11, 2, 6, 0, tzinfo=IST)
WEDNESDAY = datetime(2026, 11, 4, 15, 0, tzinfo=IST)


def test_next_tuesday_said_on_a_sunday_is_two_days_away_or_nine_and_must_be_confirmed() -> None:
    found = resolve("next Tuesday", SUNDAY)

    assert found.dates == (date(2026, 11, 3),)
    assert found.alternatives == (date(2026, 11, 10),)
    assert found.needs_confirmation
    assert say_date(found.dates[0]) == "Tuesday 3 November"


def test_next_tuesday_said_on_a_monday_proposes_the_following_week() -> None:
    found = resolve("next tuesday", MONDAY)

    assert found.dates == (date(2026, 11, 10),)
    assert found.alternatives == (date(2026, 11, 3),)
    assert found.needs_confirmation


@pytest.mark.parametrize(
    ("phrase", "now", "expected"),
    [
        ("tomorrow", SUNDAY, date(2026, 11, 2)),
        ("tomorrow", datetime(2026, 12, 31, 20, 0, tzinfo=IST), date(2027, 1, 1)),
        ("day after tomorrow", SUNDAY, date(2026, 11, 3)),
        ("Thursday", WEDNESDAY, date(2026, 11, 5)),
        ("on Monday", WEDNESDAY, date(2026, 11, 9)),
        ("in 3 days", MONDAY, date(2026, 11, 5)),
        ("in two weeks", MONDAY, date(2026, 11, 16)),
        ("15th of November", MONDAY, date(2026, 11, 15)),
        ("January 5", MONDAY, date(2027, 1, 5)),
        ("2026-11-20", MONDAY, date(2026, 11, 20)),
    ],
)
def test_unambiguous_days_resolve_without_confirmation(
    phrase: str, now: datetime, expected: date
) -> None:
    found = resolve(phrase, now)

    assert found.dates == (expected,)
    assert not found.needs_confirmation


def test_it_is_the_clinic_date_that_counts_not_the_utc_date() -> None:
    # 22:30 UTC on 1 Nov is already 04:00 on Monday 2 Nov in Bengaluru.
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)

    assert resolve("today", now).dates == (date(2026, 11, 2),)
    assert resolve("tomorrow", now).dates == (date(2026, 11, 3),)


def test_a_weekday_said_on_that_weekday_is_ambiguous() -> None:
    found = resolve("Wednesday", WEDNESDAY)

    assert found.dates == (date(2026, 11, 4),)
    assert found.alternatives == (date(2026, 11, 11),)
    assert found.needs_confirmation


def test_this_week_after_lunch_is_a_window_over_the_remaining_days() -> None:
    found = resolve("after lunch, sometime this week", WEDNESDAY)

    assert found.dates == (
        date(2026, 11, 4),
        date(2026, 11, 5),
        date(2026, 11, 6),
        date(2026, 11, 7),
    )
    assert found.window == (time(13), time(18))
    assert not found.needs_confirmation


def test_a_time_of_day_with_no_day_asks_for_the_day() -> None:
    assert resolve("after lunch", MONDAY).needs_day
    assert resolve("around 4", MONDAY).needs_day


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("Thursday at 4", time(16)),
        ("Thursday at 4:30 pm", time(16, 30)),
        ("Thursday 11 am", time(11)),
        ("Thursday at 11", time(11)),
        ("Thursday at 16:15", time(16, 15)),
        ("Thursday at noon", time(12)),
    ],
)
def test_a_specific_time_without_am_or_pm_is_read_within_clinic_hours(
    phrase: str, expected: time
) -> None:
    assert resolve(phrase, MONDAY).exact == expected


def test_a_request_for_right_now_or_within_the_hour_is_a_moment() -> None:
    now = resolve("right now please", MONDAY)
    soon = resolve("can I come in an hour", MONDAY)
    later = resolve("in 3 hours", MONDAY)

    assert now.moment == MONDAY
    assert soon.moment == datetime(2026, 11, 2, 7, 0, tzinfo=IST)
    assert later.moment == datetime(2026, 11, 2, 9, 0, tzinfo=IST)


def test_words_that_are_not_a_time_are_not_guessed() -> None:
    assert resolve("whenever you like", MONDAY).unparsed
