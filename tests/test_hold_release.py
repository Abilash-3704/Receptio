"""Clinic backend API, deterministic seam: releasing a Hold early (an Emergency or Urgent Case
must not leave time blocked for someone who will not attend)."""

import httpx

from tests.support import availability, confirm, held, local, new_key, read_back
from tests.test_confirm_cancel_reschedule import booked, reschedule


def release(
    backend: httpx.Client,
    hold_id: int,
    key: str | None = None,
    name: str | None = None,
    phone: str | None = None,
) -> httpx.Response:
    """Release a Hold. A reschedule Hold also needs the identified Patient's name and phone."""
    details = None if name is None else {"patient_name": name, "patient_phone": phone}
    return backend.post(
        f"/holds/{hold_id}/release", headers={"Idempotency-Key": key or new_key()}, json=details
    )


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


def _reschedule_hold(backend: httpx.Client) -> int:
    """Asha Menon's Appointment at 14:00, with a reschedule Hold on 16:00."""
    original = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    hold = reschedule(backend, original["id"], "Dr. Anita Rao", "2026-11-02 16:00")
    assert hold.status_code == 201
    return hold.json()["id"]


def test_a_reschedule_hold_is_only_released_for_its_patient(backend: httpx.Client) -> None:
    hold_id = _reschedule_hold(backend)

    assert release(backend, hold_id).status_code == 403  # no identity at all
    assert release(backend, hold_id, name="Asha Menon", phone="+91 90000 00000").status_code == 403
    assert release(backend, hold_id, name="Ravi Kumar", phone="+91 98450 12345").status_code == 403
    # Still live: the refused releases changed nothing, and the Patient can go on.
    assert (
        read_back(backend, hold_id, name="Asha Menon", phone="+91 98450 12345").status_code == 200
    )
    assert release(backend, hold_id, name="Asha Menon", phone="+91 98450 12345").status_code == 200


def test_releasing_a_reschedule_hold_frees_its_time(backend: httpx.Client) -> None:
    hold_id = _reschedule_hold(backend)

    release(backend, hold_id, name="Asha Menon", phone="+91 98450 12345")

    assert "2026-11-02 16:00" in local(
        availability(backend, "Filling", "2026-11-02", provider="Dr. Anita Rao")
    )


def test_a_plain_booking_hold_has_no_owner_and_the_service_may_release_it(
    backend: httpx.Client,
) -> None:
    hold_id = held(backend, "Cleaning", "Dr. Anita Rao", "2026-11-02 14:00")

    assert release(backend, hold_id).status_code == 200
