"""Clinic backend API, deterministic seam: confirm, cancel and atomic reschedule (ADR-0001).
No LLM, no sleeping. Set TEST_DATABASE_URL to run against a real Postgres."""

from datetime import timedelta
from typing import Any

import httpx

from tests.support import (
    FakeClock,
    availability,
    confirm,
    held,
    local,
    new_key,
    place_hold,
    provider_id,
    read_back,
    service_id,
)

Json = dict[str, Any]


def booked(
    backend: httpx.Client,
    service: str,
    provider: str,
    start: str,
    name: str = "Asha Menon",
    phone: str = "+91 98450 12345",
) -> Json:
    hold_id = held(backend, service, provider, start)
    assert read_back(backend, hold_id).status_code == 200
    response = confirm(backend, hold_id, name=name, phone=phone)
    assert response.status_code == 201, response.text
    return response.json()


def reschedule(
    backend: httpx.Client,
    appointment_id: int,
    provider: str,
    start: str,
    key: str | None = None,
    name: str = "Asha Menon",
    phone: str = "+91 98450 12345",
) -> httpx.Response:
    return backend.post(
        f"/appointments/{appointment_id}/reschedule",
        headers={"Idempotency-Key": key or new_key()},
        json={
            "provider_id": provider_id(backend, provider),
            "start": f"{start.replace(' ', 'T')}:00+05:30",
            "patient_name": name,
            "patient_phone": phone,
        },
    )


def read_back_reschedule(
    backend: httpx.Client,
    hold_id: int,
    key: str | None = None,
    name: str = "Asha Menon",
    phone: str = "+91 98450 12345",
) -> httpx.Response:
    """The Caller's yes to a reschedule read-back, as the identified Patient."""
    return read_back(backend, hold_id, key=key, name=name, phone=phone)


def confirm_reschedule(
    backend: httpx.Client,
    hold_id: int,
    key: str | None = None,
    name: str = "Asha Menon",
    phone: str = "+91 98450 12345",
) -> httpx.Response:
    """Confirm a reschedule Hold as the identified Patient (name AND phone on file)."""
    return confirm(backend, hold_id, name=name, phone=phone, key=key)


def cancel(
    backend: httpx.Client,
    appointment_id: int,
    key: str | None = None,
    name: str = "Asha Menon",
    phone: str = "+91 98450 12345",
) -> httpx.Response:
    return backend.post(
        f"/appointments/{appointment_id}/cancel",
        headers={"Idempotency-Key": key or new_key()},
        json={"patient_name": name, "patient_phone": phone},
    )


def status_of(
    backend: httpx.Client,
    appointment_id: int,
    name: str = "Asha Menon",
    phone: str = "+91 98450 12345",
) -> str:
    """The Appointment's status, read as its own Patient (an id alone reads nothing)."""
    response = backend.post(
        f"/appointments/{appointment_id}/read",
        json={"patient_name": name, "patient_phone": phone},
    )
    assert response.status_code == 200, response.text
    return response.json()["status"]


def test_a_write_without_an_idempotency_key_is_rejected(backend: httpx.Client) -> None:
    response = backend.post(
        "/holds",
        json={
            "service_id": service_id(backend, "Filling"),
            "provider_id": provider_id(backend, "Dr. Anita Rao"),
            "start": "2026-11-02T14:00:00+05:30",
        },
    )

    assert response.status_code == 422


def test_confirm_is_rejected_without_a_recorded_read_back(backend: httpx.Client) -> None:
    hold_id = held(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")

    response = confirm(backend, hold_id)

    assert response.status_code == 409
    assert "read-back" in response.json()["detail"]


def test_confirm_with_a_read_back_creates_the_appointment_for_a_new_patient(
    backend: httpx.Client,
) -> None:
    hold_id = held(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    assert read_back(backend, hold_id).status_code == 200

    response = confirm(backend, hold_id, name="  asha   Menon ", phone="+91 98450-12345")

    assert response.status_code == 201
    appointment = response.json()
    assert appointment["status"] == "confirmed"
    assert appointment["patient_name"] == "asha Menon"
    assert appointment["start"] == "2026-11-02T08:30:00Z"
    read = backend.post(
        f"/appointments/{appointment['id']}/read",
        json={"patient_name": "Asha Menon", "patient_phone": "+91 98450 12345"},
    )
    assert read.json() == appointment
    # The confirmed time is no longer available to anyone.
    assert place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00").status_code == 409


def test_confirm_is_rejected_when_the_hold_has_expired(
    backend: httpx.Client, clock: FakeClock
) -> None:
    hold_id = held(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    assert read_back(backend, hold_id).status_code == 200

    clock.advance(timedelta(minutes=5))
    response = confirm(backend, hold_id)

    assert response.status_code == 409
    assert "expired" in response.json()["detail"]
    # A read-back on an expired Hold is refused too, and Availability must be checked again.
    assert read_back(backend, hold_id).status_code == 409
    assert "2026-11-02 14:00" in local(
        availability(backend, "Filling", "2026-11-02", provider="Dr. Anita Rao")
    )


def test_an_expired_hold_cannot_be_confirmed_once_the_time_was_taken(
    backend: httpx.Client, clock: FakeClock
) -> None:
    stale = held(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    assert read_back(backend, stale).status_code == 200
    clock.advance(timedelta(minutes=6))
    booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", name="Ravi Kumar")

    assert confirm(backend, stale).status_code == 409


def test_a_retried_confirm_with_the_same_key_returns_the_same_appointment(
    backend: httpx.Client,
) -> None:
    hold_id = held(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    read_back(backend, hold_id)
    key = new_key()

    first = confirm(backend, hold_id, key=key)
    retry = confirm(backend, hold_id, key=key)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    # A different key for the same Hold is a new request and must not create a second one.
    again = confirm(backend, hold_id, key=new_key())
    assert again.status_code == 409
    assert "already confirmed" in again.json()["detail"]


def test_a_retried_hold_with_the_same_key_returns_the_same_hold(backend: httpx.Client) -> None:
    key = new_key()

    first = place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", key=key)
    retry = place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", key=key)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()


def test_reusing_a_key_for_a_different_request_is_rejected(backend: httpx.Client) -> None:
    key = new_key()
    assert place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", key=key).is_success

    other = place_hold(backend, "Filling", "Dr. Anita Rao", "2026-11-02 16:00", key=key)

    assert other.status_code == 422


def test_a_rejected_request_stores_nothing_so_it_can_be_retried_with_the_same_key(
    backend: httpx.Client,
) -> None:
    hold_id = held(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    key = new_key()
    assert confirm(backend, hold_id, key=key).status_code == 409  # no read-back yet

    read_back(backend, hold_id)

    assert confirm(backend, hold_id, key=key).status_code == 201


def test_invalid_patient_details_are_rejected_not_guessed(backend: httpx.Client) -> None:
    hold_id = held(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    read_back(backend, hold_id)

    assert confirm(backend, hold_id, phone="12345").status_code == 422
    assert confirm(backend, hold_id, name="   ").status_code == 422
    assert confirm(backend, hold_id, phone="nine eight four").status_code == 422
    no_details = backend.post(f"/holds/{hold_id}/confirm", headers={"Idempotency-Key": new_key()})
    assert no_details.status_code == 422  # a new booking needs the Patient's details
    assert confirm(backend, hold_id).status_code == 201


def test_a_patient_never_has_overlapping_appointments_whatever_the_provider(
    backend: httpx.Client,
) -> None:
    booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")  # 14:00-15:00
    clash = held(backend, "Check-up", "Dr. Vikram Shetty", "2026-11-02 14:30")
    read_back(backend, clash)

    rejected = confirm(backend, clash)

    assert rejected.status_code == 409
    assert "overlapping" in rejected.json()["detail"]
    # Back-to-back is fine, and a different Patient is unaffected.
    booked(backend, "Check-up", "Dr. Vikram Shetty", "2026-11-02 15:00")
    other = booked(backend, "Check-up", "Dr. Vikram Shetty", "2026-11-02 15:30", name="Ravi Kumar")
    assert other["status"] == "confirmed"


def test_a_cancelled_appointment_no_longer_blocks_the_patient_or_the_time(
    backend: httpx.Client,
) -> None:
    first = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")

    response = cancel(backend, first["id"])

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert "2026-11-02 14:00" in local(
        availability(backend, "Filling", "2026-11-02", provider="Dr. Anita Rao")
    )
    booked(backend, "Check-up", "Dr. Vikram Shetty", "2026-11-02 14:30")  # same Patient, overlap


def test_cancel_works_at_any_time_and_twice_changes_nothing(
    backend: httpx.Client, clock: FakeClock
) -> None:
    appointment = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    clock.advance(timedelta(hours=7))  # after it started

    assert cancel(backend, appointment["id"]).status_code == 200
    assert cancel(backend, appointment["id"]).json()["status"] == "cancelled"
    assert cancel(backend, 9999).status_code == 403  # same as a wrong identity
    hold_id = held(backend, "Filling", "Dr. Anita Rao", "2026-11-03 14:00")
    assert cancel(backend, hold_id).status_code == 403  # a Hold is not an Appointment


def test_reschedule_confirms_the_new_time_and_cancels_the_old_in_one_step(
    backend: httpx.Client,
) -> None:
    original = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")

    hold = reschedule(backend, original["id"], "Dr. Vikram Shetty", "2026-11-02 11:00")
    assert hold.status_code == 201
    # Nothing has changed yet: the original is intact while the Caller hears the read-back.
    assert status_of(backend, original["id"]) == "confirmed"
    hold_id = hold.json()["id"]
    read_back_reschedule(backend, hold_id)

    response = confirm_reschedule(backend, hold_id)

    assert response.status_code == 201
    new = response.json()
    assert new["status"] == "confirmed"
    assert new["patient_id"] == original["patient_id"]
    assert new["replaces_id"] == original["id"]
    assert status_of(backend, original["id"]) == "cancelled"
    assert "2026-11-02 14:00" in local(
        availability(backend, "Filling", "2026-11-02", provider="Dr. Anita Rao")
    )


def test_a_patient_can_move_an_appointment_to_an_overlapping_time_of_their_own(
    backend: httpx.Client,
) -> None:
    original = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")

    hold = reschedule(backend, original["id"], "Dr. Anita Rao", "2026-11-02 14:15")

    assert hold.status_code == 201, hold.text
    read_back_reschedule(backend, hold.json()["id"])
    assert confirm_reschedule(backend, hold.json()["id"]).status_code == 201
    assert status_of(backend, original["id"]) == "cancelled"
    # Only the new time is taken now: an hour ending at 14:15 is free, one starting at 14:00 not.
    free = local(availability(backend, "Filling", "2026-11-02", provider="Dr. Anita Rao"))
    assert "2026-11-02 13:15" in free
    assert "2026-11-02 14:00" not in free


def test_rescheduling_still_conflicts_with_other_patients_appointments(
    backend: httpx.Client,
) -> None:
    original = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 15:00", name="Ravi Kumar")

    overlaps_other = reschedule(backend, original["id"], "Dr. Anita Rao", "2026-11-02 14:45")

    assert overlaps_other.status_code == 409


def test_rescheduling_still_conflicts_with_a_live_hold_of_someone_else(
    backend: httpx.Client,
) -> None:
    original = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    held(backend, "Filling", "Dr. Anita Rao", "2026-11-02 15:00")

    overlaps_hold = reschedule(backend, original["id"], "Dr. Anita Rao", "2026-11-02 14:45")

    assert overlaps_hold.status_code == 409


def test_a_failed_reschedule_leaves_the_original_confirmed(
    backend: httpx.Client, clock: FakeClock
) -> None:
    original = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    other = booked(backend, "Check-up", "Dr. Vikram Shetty", "2026-11-02 11:00", name="Ravi Kumar")

    # The new time is taken.
    taken = reschedule(backend, original["id"], "Dr. Vikram Shetty", "2026-11-02 11:00")
    assert taken.status_code == 409
    # The Hold expires before confirmation.
    stale = reschedule(backend, original["id"], "Dr. Vikram Shetty", "2026-11-02 12:00").json()
    read_back_reschedule(backend, stale["id"])
    clock.advance(timedelta(minutes=5))
    assert confirm_reschedule(backend, stale["id"]).status_code == 409
    # No read-back was recorded.
    unread = reschedule(backend, original["id"], "Dr. Vikram Shetty", "2026-11-02 12:00").json()
    assert confirm_reschedule(backend, unread["id"]).status_code == 409
    # The Appointment being rescheduled was cancelled meanwhile: nothing is revived.
    late = reschedule(
        backend, other["id"], "Dr. Vikram Shetty", "2026-11-02 13:00", name="Ravi Kumar"
    ).json()
    read_back_reschedule(backend, late["id"], name="Ravi Kumar")
    cancel(backend, other["id"], name="Ravi Kumar")
    assert confirm_reschedule(backend, late["id"], name="Ravi Kumar").status_code == 409

    assert status_of(backend, original["id"]) == "confirmed"
    assert status_of(backend, other["id"], name="Ravi Kumar") == "cancelled"
    assert reschedule(backend, 9999, "Dr. Anita Rao", "2026-11-02 16:00").status_code == 403
    cancelled = reschedule(
        backend, other["id"], "Dr. Anita Rao", "2026-11-02 16:00", name="Ravi Kumar"
    )
    assert cancelled.status_code == 409


def test_a_reschedule_into_another_of_the_patients_appointments_is_rejected_whole(
    backend: httpx.Client,
) -> None:
    morning = booked(backend, "Check-up", "Dr. Anita Rao", "2026-11-02 10:00")
    booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    hold = reschedule(backend, morning["id"], "Dr. Vikram Shetty", "2026-11-02 14:30").json()
    read_back_reschedule(backend, hold["id"])

    response = confirm_reschedule(backend, hold["id"])

    assert response.status_code == 409
    assert status_of(backend, morning["id"]) == "confirmed"


def test_a_retried_reschedule_confirm_returns_the_same_result(backend: httpx.Client) -> None:
    original = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    key = new_key()
    first_hold = reschedule(backend, original["id"], "Dr. Anita Rao", "2026-11-02 16:00", key=key)
    retried_hold = reschedule(backend, original["id"], "Dr. Anita Rao", "2026-11-02 16:00", key=key)
    assert retried_hold.json() == first_hold.json()
    hold_id = first_hold.json()["id"]
    read_back_reschedule(backend, hold_id)
    confirm_key = new_key()

    first = confirm_reschedule(backend, hold_id, key=confirm_key)
    retry = confirm_reschedule(backend, hold_id, key=confirm_key)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()


def test_a_reschedule_hold_cannot_be_read_back_by_anyone_else(backend: httpx.Client) -> None:
    original = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    hold_id = reschedule(backend, original["id"], "Dr. Vikram Shetty", "2026-11-02 11:00").json()[
        "id"
    ]

    guessed = [
        read_back(backend, hold_id),
        read_back(backend, hold_id, name="Asha Nair", phone="+91 98450 12345"),
        read_back(backend, hold_id, name="Asha Menon", phone="+91 98450 00000"),
        read_back(backend, 99999),  # an id that does not exist looks exactly the same
    ]

    for response in guessed:
        assert response.status_code == 403
        assert response.json() == {"detail": "identification failed"}
    # No agreement was recorded, so even the right Patient cannot confirm yet.
    assert confirm_reschedule(backend, hold_id).status_code == 409
    assert status_of(backend, original["id"]) == "confirmed"
    assert (
        read_back(backend, hold_id, name="Asha Menon", phone="+91 98450 12345").status_code == 200
    )
    assert confirm_reschedule(backend, hold_id).status_code == 201


def test_a_reschedule_hold_is_confirmed_only_for_the_patient_it_belongs_to(
    backend: httpx.Client,
) -> None:
    original = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 15:00", "Ravi Nair", "+91 98450 77777")
    hold_id = reschedule(backend, original["id"], "Dr. Vikram Shetty", "2026-11-02 11:00").json()[
        "id"
    ]
    read_back_reschedule(backend, hold_id)

    wrong_name = confirm_reschedule(backend, hold_id, name="Asha Nair")
    wrong_phone = confirm_reschedule(backend, hold_id, phone="+91 98450 00000")
    other_patient = confirm_reschedule(backend, hold_id, name="Ravi Nair", phone="+91 98450 77777")
    unknown = confirm_reschedule(backend, hold_id, name="Nobody Here", phone="+91 90000 11111")
    no_details = backend.post(f"/holds/{hold_id}/confirm", headers={"Idempotency-Key": new_key()})
    malformed = confirm_reschedule(backend, hold_id, phone="123")

    for response in (wrong_name, wrong_phone, other_patient, unknown, no_details, malformed):
        assert response.status_code == 403
        assert response.json() == {"detail": "identification failed"}
    assert status_of(backend, original["id"]) == "confirmed"
    # Nothing was consumed: the right Patient can still confirm the same Hold.
    assert confirm_reschedule(backend, hold_id).status_code == 201
    assert status_of(backend, original["id"]) == "cancelled"
