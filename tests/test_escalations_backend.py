"""Escalations through the backend's HTTP interface: the backend alone decides Handoff versus
Callback Request, from Clinic Hours, and every Escalation is stored with a reason."""

from datetime import timedelta
from typing import Any

import httpx

from tests.support import FakeClock, Json, new_key


def escalate(
    backend: httpx.Client, call_id: int, reason: str = "caller asked for a human", **extra: Any
) -> httpx.Response:
    return backend.post(
        f"/calls/{call_id}/escalations",
        headers={"Idempotency-Key": new_key()},
        json={"reason": reason, **extra},
    )


def open_call(backend: httpx.Client) -> int:
    return backend.post("/calls").json()["id"]


def test_during_clinic_hours_an_escalation_is_a_handoff(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.advance(timedelta(hours=2))  # Monday 10:00
    call_id = open_call(backend)

    response = escalate(backend, call_id, "caller asked for a human")

    assert response.status_code == 201
    assert response.json()["kind"] == "handoff"
    [stored] = backend.get("/escalations").json()
    assert (stored["call_id"], stored["kind"], stored["reason"]) == (
        call_id,
        "handoff",
        "caller asked for a human",
    )


def test_outside_clinic_hours_an_escalation_is_a_callback_request_with_the_number(
    backend: httpx.Client,
) -> None:
    call_id = open_call(backend)  # Monday 08:00, closed

    response = escalate(
        backend, call_id, "caller asked for a human", caller_phone="+91 98450 12345"
    )

    body: Json = response.json()
    assert body["kind"] == "callback_request"
    assert body["caller_phone"] == "+919845012345"
    assert body["reason"] == "caller asked for a human"


def test_the_override_changes_the_kind_of_escalation(backend: httpx.Client) -> None:
    call_id = open_call(backend)  # closed by the clock
    backend.put("/demo/clinic-override", json={"mode": "open"})

    assert escalate(backend, call_id).json()["kind"] == "handoff"

    backend.put("/demo/clinic-override", json={"mode": "closed"})
    assert escalate(backend, call_id).json()["kind"] == "callback_request"


def test_an_escalation_without_a_reason_is_rejected_and_nothing_is_stored(
    backend: httpx.Client,
) -> None:
    call_id = open_call(backend)

    assert escalate(backend, call_id, reason="   ").status_code == 422
    assert escalate(backend, call_id, reason="").status_code == 422

    assert backend.get("/escalations").json() == []


def test_retrying_an_escalation_with_the_same_key_stores_one(backend: httpx.Client) -> None:
    call_id = open_call(backend)
    key = new_key()
    for _ in range(2):
        response = backend.post(
            f"/calls/{call_id}/escalations",
            headers={"Idempotency-Key": key},
            json={"reason": "caller asked for a human"},
        )
        assert response.status_code == 201

    assert len(backend.get("/escalations").json()) == 1


def test_escalating_an_unknown_call_is_not_found(backend: httpx.Client) -> None:
    assert escalate(backend, 999).status_code == 404


def test_a_forced_callback_request_is_recorded_even_during_clinic_hours(
    backend: httpx.Client, clock: FakeClock
) -> None:
    clock.advance(timedelta(hours=2))  # Monday 10:00, open
    call_id = open_call(backend)

    response = escalate(backend, call_id, "wanted a time too soon", kind="callback_request")

    assert response.status_code == 201
    assert response.json()["kind"] == "callback_request"


def test_a_handoff_can_never_be_forced_while_the_clinic_is_closed(backend: httpx.Client) -> None:
    call_id = open_call(backend)  # Monday 08:00, closed

    response = escalate(backend, call_id, kind="handoff")

    assert response.status_code == 422
    assert backend.get("/escalations").json() == []


def test_an_unknown_kind_is_rejected(backend: httpx.Client) -> None:
    assert escalate(backend, open_call(backend), kind="page_the_dentist").status_code == 422
