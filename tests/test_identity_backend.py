"""Clinic backend API seam: Patient identification guards cancel and reschedule. The agent is not
trusted to have identified anyone: the backend needs the Patient's full name AND phone on file to
match, and a failed match must look the same whether or not the Appointment exists."""

import httpx
import pytest

from tests.support import availability, local, new_key
from tests.test_confirm_cancel_reschedule import booked, cancel, reschedule, status_of

ASHA = {"name": "Asha Menon", "phone": "+91 98450 12345"}
RAVI = {"name": "Ravi Kumar", "phone": "+91 90000 11111"}


def lookup(backend: httpx.Client, name: str, phone: str) -> httpx.Response:
    return backend.post("/appointments/lookup", json={"patient_name": name, "patient_phone": phone})


@pytest.mark.parametrize(
    "who",
    [
        {"name": "Asha Menon", "phone": "+91 98450 99999"},  # right name, wrong phone
        {"name": "Asha Menan", "phone": "+91 98450 12345"},  # wrong name, right phone
        {"name": "Seeded Patient", "phone": "+91 98450 12345"},  # a real name, someone else's phone
        {"name": "Asha Menon", "phone": "not a phone"},
        {"name": "Asha\nMenon", "phone": "+91 98450 12345"},
    ],
)
def test_cancel_with_the_wrong_identity_changes_nothing_and_reveals_nothing(
    backend: httpx.Client, who: dict[str, str]
) -> None:
    appointment = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", **ASHA)

    forged = cancel(backend, appointment["id"], **who)
    nonexistent = cancel(backend, 9999, **who)

    assert forged.status_code == 403
    assert (nonexistent.status_code, nonexistent.json()) == (403, forged.json())
    assert status_of(backend, appointment["id"]) == "confirmed"


def test_cancel_accepts_the_identity_in_any_casing_and_phone_format(
    backend: httpx.Client,
) -> None:
    appointment = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", **ASHA)

    response = cancel(backend, appointment["id"], name="  asha  MENON ", phone="+91-98450 12345")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_another_patient_cannot_cancel_or_reschedule_it_even_with_their_own_valid_details(
    backend: httpx.Client,
) -> None:
    mine = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", **ASHA)
    booked(backend, "Check-up", "Dr. Vikram Shetty", "2026-11-02 11:00", **RAVI)
    ravi = {"name": RAVI["name"], "phone": RAVI["phone"]}

    assert cancel(backend, mine["id"], **ravi).status_code == 403
    moved = reschedule(backend, mine["id"], "Dr. Anita Rao", "2026-11-02 16:00", **ravi)
    assert moved.status_code == 403
    assert status_of(backend, mine["id"]) == "confirmed"


def test_a_failed_reschedule_identification_places_no_hold_and_reveals_nothing(
    backend: httpx.Client,
) -> None:
    appointment = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", **ASHA)

    forged = reschedule(
        backend, appointment["id"], "Dr. Anita Rao", "2026-11-02 16:00", phone="+91 98450 99999"
    )
    nonexistent = reschedule(
        backend, 9999, "Dr. Anita Rao", "2026-11-02 16:00", phone="+91 98450 99999"
    )

    assert forged.status_code == 403
    assert (nonexistent.status_code, nonexistent.json()) == (403, forged.json())
    assert "2026-11-02 16:00" in local(
        availability(backend, "Filling", "2026-11-02", provider="Dr. Anita Rao")
    )


def test_cancel_requires_both_identity_fields(backend: httpx.Client) -> None:
    appointment = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", **ASHA)
    path = f"/appointments/{appointment['id']}/cancel"

    assert backend.post(path, headers={"Idempotency-Key": new_key()}).status_code == 422
    only_name = backend.post(
        path, headers={"Idempotency-Key": new_key()}, json={"patient_name": "Asha Menon"}
    )
    assert only_name.status_code == 422
    assert status_of(backend, appointment["id"]) == "confirmed"


def test_lookup_lists_only_the_matched_patients_upcoming_confirmed_appointments(
    backend: httpx.Client,
) -> None:
    first = booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", **ASHA)
    second = booked(backend, "Check-up", "Dr. Vikram Shetty", "2026-11-03 11:00", **ASHA)
    gone = booked(backend, "Check-up", "Dr. Vikram Shetty", "2026-11-04 11:00", **ASHA)
    cancel(backend, gone["id"])
    booked(backend, "Check-up", "Dr. Vikram Shetty", "2026-11-02 11:00", **RAVI)

    response = lookup(backend, "asha menon", "+919845012345")

    assert response.status_code == 200
    found = response.json()
    assert [a["id"] for a in found] == [first["id"], second["id"]]
    assert found[0]["service"] == "Filling" and found[0]["provider"] == "Dr. Anita Rao"


def test_a_failed_lookup_is_the_same_for_a_wrong_phone_and_an_unknown_patient(
    backend: httpx.Client,
) -> None:
    booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", **ASHA)

    wrong_phone = lookup(backend, "Asha Menon", "+91 98450 99999")
    unknown = lookup(backend, "Nobody Here", "+91 98450 99999")

    assert wrong_phone.status_code == 403
    assert (unknown.status_code, unknown.json()) == (403, wrong_phone.json())
    assert "Filling" not in wrong_phone.text and "14:00" not in wrong_phone.text
