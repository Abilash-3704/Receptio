"""The cost guard under concurrency, against a real Postgres (skipped without TEST_DATABASE_URL).

The caps are only worth having if simultaneous Calls cannot overshoot them. Each test repeats the
race on a fresh day (the injected clock moves forward), so a missing lock fails within a few
rounds instead of passing by luck."""

from datetime import timedelta

import httpx
import pytest

from tests.conftest import POSTGRES_URL, BackendFactory
from tests.support import FakeClock
from tests.test_capacity import admit, settle
from tests.test_concurrency import race

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="concurrency needs a real Postgres: set TEST_DATABASE_URL"
)

ROUNDS = 6


def admitted(responses: list[httpx.Response]) -> int:
    return sum(r.status_code == 201 for r in responses)


def test_concurrent_calls_never_overshoot_the_global_cap(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    # 120 minutes with 5 reserved per Call is 24 Calls; 60 distinct visitors race for them.
    backend = backend_factory(ip_calls_per_day=1000, visitor_calls_per_day=1000)

    for day in range(ROUNDS):
        responses = race(
            [
                lambda n=n, day=day: admit(
                    backend, f"d{day}-r{n}", browser=f"b{n}", ip=f"10.0.0.{n}"
                )
                for n in range(60)
            ]
        )

        assert admitted(responses) == 24, f"day {day}"
        assert {r.json()["reason"] for r in responses if r.status_code == 429} == {"global_limit"}
        clock.advance(timedelta(days=1))


def test_concurrent_calls_from_one_visitor_get_exactly_three(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    backend = backend_factory()

    for day in range(ROUNDS):
        responses = race([lambda n=n, day=day: admit(backend, f"d{day}-r{n}") for n in range(10)])

        assert admitted(responses) == 3, f"day {day}"
        clock.advance(timedelta(days=1))


def test_concurrent_calls_from_one_ip_get_exactly_the_ip_cap(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    backend = backend_factory(ip_calls_per_day=5)

    for day in range(ROUNDS):
        responses = race(
            [
                lambda n=n, day=day: admit(backend, f"d{day}-r{n}", browser=f"b{n}")
                for n in range(12)
            ]
        )

        assert admitted(responses) == 5, f"day {day}"
        clock.advance(timedelta(days=1))


def test_settling_the_same_call_concurrently_gives_the_minutes_back_once(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(global_minutes_per_day=10, visitor_calls_per_day=100)
    admit(backend, "one", browser="b1")
    admit(backend, "two", browser="b2")

    race([lambda: settle(backend, "one", used_seconds=0) for _ in range(8)])

    # Exactly one Call's 5 minutes came back: one more fits, a second does not.
    assert admit(backend, "three", browser="b3").status_code == 201
    assert admit(backend, "four", browser="b4").status_code == 429


def test_concurrent_admissions_of_one_room_are_all_admitted_and_charged_once(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    backend = backend_factory()

    for day in range(ROUNDS):
        responses = race([lambda day=day: admit(backend, f"same-{day}") for _ in range(8)])

        assert [r.status_code for r in responses] == [201] * 8, f"day {day}"
        # One Call was charged, not eight: the visitor still has two of their three.
        assert [admit(backend, f"d{day}-x{n}").status_code for n in range(3)] == [201, 201, 429]
        clock.advance(timedelta(days=1))
