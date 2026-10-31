"""Concurrency seam: simultaneous requests through the backend API, against a real Postgres.
SQLite in memory cannot prove this, so these skip unless TEST_DATABASE_URL is set.

A race window is narrow, so each test repeats the race on fresh times: a missing lock fails
within a few rounds instead of passing by luck."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import httpx
import pytest

from tests.support import FakeClock, confirm, held, new_key, place_hold, read_back
from tests.test_confirm_cancel_reschedule import (
    booked,
    confirm_reschedule,
    read_back_reschedule,
    reschedule,
    status_of,
)


@pytest.fixture(autouse=True)
def _early_clock(clock: FakeClock) -> None:
    clock.advance(timedelta(hours=-2))  # 06:00, so the 2-hour notice does not hide 09:00


CONTENDERS = 8
ROUNDS = 12
# Dr. Rao, Monday 2 November: twelve distinct half-hour Check-up times, one per round.
TIMES = [f"2026-11-02 {9 + n // 2:02d}:{30 * (n % 2):02d}" for n in range(ROUNDS)]


def race(calls: list[Callable[[], httpx.Response]]) -> list[httpx.Response]:
    """Run every call at the same moment, each on its own thread."""
    barrier = Barrier(len(calls))

    def run(call: Callable[[], httpx.Response]) -> httpx.Response:
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


def codes(responses: list[httpx.Response]) -> list[int]:
    return sorted(r.status_code for r in responses)


def later(start: str) -> str:
    """A quarter of an hour into the half-hour that starts at `start`."""
    return start[:-2] + ("15" if start.endswith("00") else "45")


ONE_WINNER = [201] + [409] * (CONTENDERS - 1)


def test_concurrent_holds_for_the_same_time_place_exactly_one(
    postgres_backend: httpx.Client,
) -> None:
    backend = postgres_backend

    for start in TIMES:
        responses = race(
            [
                lambda start=start: place_hold(backend, "Check-up", "Dr. Anita Rao", start)
                for _ in range(CONTENDERS)
            ]
        )

        assert codes(responses) == ONE_WINNER, start


def test_concurrent_confirms_of_one_hold_create_exactly_one_appointment(
    postgres_backend: httpx.Client,
) -> None:
    backend = postgres_backend

    for start in TIMES:
        hold_id = held(backend, "Check-up", "Dr. Anita Rao", start)
        read_back(backend, hold_id)

        # Each contender is a distinct request (own key): only the backend can stop duplicates.
        responses = race(
            [lambda hold_id=hold_id: confirm(backend, hold_id) for _ in range(CONTENDERS)]
        )

        assert codes(responses) == ONE_WINNER, start
        winner = next(r for r in responses if r.status_code == 201).json()
        assert status_of(backend, winner["id"]) == "confirmed"


def test_concurrent_retries_with_one_key_all_get_the_same_appointment(
    postgres_backend: httpx.Client,
) -> None:
    backend = postgres_backend

    for start in TIMES:
        hold_id = held(backend, "Check-up", "Dr. Anita Rao", start)
        read_back(backend, hold_id)
        key = new_key()

        responses = race(
            [
                lambda hold_id=hold_id, key=key: confirm(backend, hold_id, key=key)
                for _ in range(CONTENDERS)
            ]
        )

        assert codes(responses) == [201] * CONTENDERS, start
        assert len({r.json()["id"] for r in responses}) == 1


def test_concurrent_confirms_for_one_patient_at_overlapping_times_confirm_one(
    postgres_backend: httpx.Client,
) -> None:
    backend = postgres_backend

    for n, start in enumerate(TIMES[2:8]):  # 10:00 to 12:30, inside Dr. Shetty's hours too
        # Two Providers, overlapping times, one Patient: both Holds are legitimate and both
        # are read back, so only the Patient rule can stop the second Appointment.
        first = held(backend, "Check-up", "Dr. Anita Rao", start)
        second = held(backend, "Check-up", "Dr. Vikram Shetty", later(start))
        read_back(backend, first)
        read_back(backend, second)
        name = f"Patient {chr(ord('A') + n)}"  # letters only: names are validated

        responses = race(
            [
                lambda first=first, name=name: confirm(backend, first, name=name),
                lambda second=second, name=name: confirm(backend, second, name=name),
            ]
            * (CONTENDERS // 2)
        )

        assert codes(responses) == ONE_WINNER, start


def test_concurrent_reschedules_of_one_appointment_confirm_exactly_one(
    postgres_backend: httpx.Client,
) -> None:
    backend = postgres_backend

    for n, start in enumerate(TIMES[:6]):
        original = booked(
            backend, "Check-up", "Dr. Anita Rao", start, name=f"Patient {chr(ord('A') + n)}"
        )
        holds = [
            reschedule(
                backend,
                original["id"],
                "Dr. Vikram Shetty",
                slot,
                name=f"Patient {chr(ord('A') + n)}",
            ).json()["id"]
            for slot in (f"2026-11-0{3 + k} {10 + n}:00" for k in range(3))
        ]
        patient = f"Patient {chr(ord('A') + n)}"
        for hold_id in holds:
            read_back_reschedule(backend, hold_id, name=patient)

        responses = race(
            [lambda h=h, p=patient: confirm_reschedule(backend, h, name=p) for h in holds]
        )

        assert codes(responses) == [201, 409, 409], start
        assert (
            status_of(backend, original["id"], name=f"Patient {chr(ord('A') + n)}") == "cancelled"
        )


def test_concurrent_turns_of_one_call_all_land_in_order_with_distinct_numbers(
    postgres_backend: httpx.Client,
) -> None:
    backend = postgres_backend

    for _ in range(ROUNDS):
        call_id = backend.post("/calls", json={"mode": "voice"}).json()["id"]

        responses = race(
            [
                lambda n=n, call_id=call_id: backend.post(
                    f"/calls/{call_id}/turns", json={"speaker": "caller", "text": f"turn {n}"}
                )
                for n in range(CONTENDERS)
            ]
        )

        assert codes(responses) == [201] * CONTENDERS
        stored = backend.get(f"/calls/{call_id}").json()["transcript"]
        assert len(stored) == CONTENDERS
