"""Clinic backend API, deterministic seam: releasing a Hold early (an Emergency or Urgent Case
must not leave time blocked for someone who will not attend)."""

import httpx

from tests.support import availability, confirm, held, local, new_key, read_back


def release(backend: httpx.Client, hold_id: int, key: str | None = None) -> httpx.Response:
    return backend.post(f"/holds/{hold_id}/release", headers={"Idempotency-Key": key or new_key()})


def test_releasing_a_hold_frees_its_time_at_once(backend: httpx.Client) -> None:
    hold_id = held(backend, "Cleaning", "Dr. Anita Rao", "2026-11-02 14:00")
    assert "2026-11-02 14:00" not in local(
        availability(backend, "Cleaning", "2026-11-02", provider="Dr. Anita Rao")
    )

    assert release(backend, hold_id).status_code == 200

    assert "2026-11-02 14:00" in local(
        availability(backend, "Cleaning", "2026-11-02", provider="Dr. Anita Rao")
    )


def test_a_released_hold_can_no_longer_be_confirmed(backend: httpx.Client) -> None:
    hold_id = held(backend, "Cleaning", "Dr. Anita Rao", "2026-11-02 14:00")
    assert read_back(backend, hold_id).status_code == 200
    release(backend, hold_id)

    assert confirm(backend, hold_id).status_code == 409


def test_releasing_twice_is_harmless(backend: httpx.Client) -> None:
    hold_id = held(backend, "Cleaning", "Dr. Anita Rao", "2026-11-02 14:00")
    assert release(backend, hold_id).status_code == 200
    assert release(backend, hold_id).status_code == 200  # a new key, already released


def test_a_confirmed_appointment_is_never_released(backend: httpx.Client) -> None:
    hold_id = held(backend, "Cleaning", "Dr. Anita Rao", "2026-11-02 14:00")
    read_back(backend, hold_id)
    assert confirm(backend, hold_id).status_code == 201

    assert release(backend, hold_id).status_code == 409
    assert "2026-11-02 14:00" not in local(
        availability(backend, "Cleaning", "2026-11-02", provider="Dr. Anita Rao")
    )


def test_releasing_an_unknown_hold_is_not_found(backend: httpx.Client) -> None:
    assert release(backend, 99999).status_code == 404
