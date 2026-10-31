"""Clinic backend, deterministic seam: the booking window (2 hours to 60 days ahead, within
Clinic Hours) is enforced server-side, whatever the agent asks for."""

import httpx

from tests.support import FakeClock, availability, held, local, place_hold


def test_availability_starts_two_hours_after_now(backend: httpx.Client, clock: FakeClock) -> None:
    clock.now = clock.now.replace(hour=9, minute=30)  # Monday 09:30, so the earliest is 11:30

    slots = local(availability(backend, "Check-up", "2026-11-02", provider="Dr. Anita Rao"))

    assert slots[0] == "2026-11-02 11:30"


def test_a_hold_sooner_than_two_hours_ahead_is_refused(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.now = clock.now.replace(hour=9, minute=30)

    refused = place_hold(backend, "Check-up", "Dr. Anita Rao", "2026-11-02 11:15")
    allowed = place_hold(backend, "Check-up", "Dr. Anita Rao", "2026-11-02 11:30")

    assert refused.status_code == 409
    assert allowed.status_code == 201


def test_nothing_is_offered_or_held_beyond_sixty_days(backend: httpx.Client) -> None:
    # Clock is Monday 2 Nov 06:00; 60 days ahead is Friday 1 Jan 2027 at 06:00.
    assert local(availability(backend, "Check-up", "2026-12-31", provider="Dr. Anita Rao"))
    assert availability(backend, "Check-up", "2027-01-04", provider="Dr. Anita Rao") == []
    refused = place_hold(backend, "Check-up", "Dr. Anita Rao", "2027-01-04 10:00")
    assert refused.status_code == 409


def test_a_hold_outside_clinic_hours_is_refused(backend: httpx.Client) -> None:
    assert place_hold(backend, "Check-up", "Dr. Anita Rao", "2026-11-03 08:00").status_code == 409
    assert place_hold(backend, "Check-up", "Dr. Anita Rao", "2026-11-03 18:00").status_code == 409
    assert place_hold(backend, "Check-up", "Dr. Anita Rao", "2026-11-08 10:00").status_code == 409


def test_a_released_hold_frees_the_time(backend: httpx.Client) -> None:
    hold_id = held(backend, "Check-up", "Dr. Anita Rao", "2026-11-03 14:00")
    assert place_hold(backend, "Check-up", "Dr. Anita Rao", "2026-11-03 14:00").status_code == 409

    released = backend.post(f"/holds/{hold_id}/release", headers={"Idempotency-Key": "r1"})

    assert released.status_code == 200
    assert place_hold(backend, "Check-up", "Dr. Anita Rao", "2026-11-03 14:00").status_code == 201
