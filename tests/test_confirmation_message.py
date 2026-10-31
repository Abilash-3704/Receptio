"""Clinic backend API: the mocked Confirmation Message record for a Confirmed Appointment."""

import httpx

from tests.support import confirm, held, new_key, read_back

SLOT = "2026-11-02 10:00"


def confirmed_appointment(backend: httpx.Client) -> int:
    hold_id = held(backend, "Cleaning", "Meera Nair", "2026-11-03 10:00")
    assert read_back(backend, hold_id).status_code == 200
    response = confirm(backend, hold_id, phone="+91 98450 12345")
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(backend: httpx.Client, appointment_id: int, key: str | None = None) -> httpx.Response:
    return backend.post(
        f"/appointments/{appointment_id}/confirmation-message",
        headers={"Idempotency-Key": key or new_key()},
    )


def test_message_is_recorded_for_a_confirmed_appointment_to_the_patients_number(
    backend: httpx.Client,
) -> None:
    appointment_id = confirmed_appointment(backend)

    response = send(backend, appointment_id)

    assert response.status_code == 201, response.text
    messages = backend.get("/confirmation-messages", params={"appointment_id": appointment_id})
    [message] = messages.json()
    assert message["to_phone"] == "+919845012345"
    assert message["appointment_id"] == appointment_id
    assert "Cleaning" in message["body"]


def test_retrying_with_the_same_key_records_one_message(backend: httpx.Client) -> None:
    appointment_id = confirmed_appointment(backend)
    key = new_key()

    first = send(backend, appointment_id, key)
    second = send(backend, appointment_id, key)

    assert second.status_code == first.status_code
    assert second.json() == first.json()
    messages = backend.get("/confirmation-messages", params={"appointment_id": appointment_id})
    assert len(messages.json()) == 1


def test_a_hold_gets_no_message(backend: httpx.Client) -> None:
    hold_id = held(backend, "Cleaning", "Meera Nair", "2026-11-03 10:00")

    response = send(backend, hold_id)

    assert response.status_code == 409
    assert backend.get("/confirmation-messages").json() == []
