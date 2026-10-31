"""Seam: the clinic backend's Call API. A Call ends with an outcome (completed, escalated or
dropped) and, for an Escalation or a drop, the reason it is stored with the Call."""

from typing import Any

import httpx

from tests.support import new_key


def open_call(backend: httpx.Client) -> int:
    return int(backend.post("/calls").json()["id"])


def escalate(backend: httpx.Client, call_id: int, reason: str) -> None:
    response = backend.post(
        f"/calls/{call_id}/escalations",
        headers={"Idempotency-Key": new_key()},
        json={"reason": reason, "caller_phone": "9845012345"},
    )
    assert response.status_code == 201


def end(backend: httpx.Client, call_id: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = backend.post(f"/calls/{call_id}/end", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_an_open_call_has_no_outcome_yet(backend: httpx.Client) -> None:
    record = backend.get(f"/calls/{open_call(backend)}").json()

    assert (record["status"], record["outcome"], record["outcome_reason"]) == ("open", None, None)


def test_a_call_ended_with_nothing_special_is_completed(backend: httpx.Client) -> None:
    call_id = open_call(backend)

    record = end(backend, call_id)

    assert (record["status"], record["outcome"], record["outcome_reason"]) == (
        "ended",
        "completed",
        None,
    )


def test_a_call_with_an_escalation_ends_escalated_with_the_escalation_reason(
    backend: httpx.Client,
) -> None:
    call_id = open_call(backend)
    escalate(backend, call_id, "caller asked for a human")

    record = end(backend, call_id)

    assert (record["outcome"], record["outcome_reason"]) == (
        "escalated",
        "caller asked for a human",
    )


def test_an_escalation_wins_over_a_claimed_outcome(backend: httpx.Client) -> None:
    call_id = open_call(backend)
    escalate(backend, call_id, "emergency: the Caller reported a medical emergency")

    record = end(backend, call_id, {"outcome": "completed"})

    assert record["outcome"] == "escalated"


def test_a_dropped_call_is_stored_with_why(backend: httpx.Client) -> None:
    call_id = open_call(backend)

    record = end(backend, call_id, {"outcome": "dropped", "reason": "caller silent: no speech"})

    assert (record["outcome"], record["outcome_reason"]) == (
        "dropped",
        "caller silent: no speech",
    )


def test_a_second_end_changes_nothing(backend: httpx.Client) -> None:
    call_id = open_call(backend)
    end(backend, call_id, {"outcome": "dropped", "reason": "caller silent: no speech"})

    record = end(backend, call_id, {"outcome": "completed"})

    assert record["outcome"] == "dropped"


def test_an_unknown_outcome_is_refused(backend: httpx.Client) -> None:
    call_id = open_call(backend)

    assert backend.post(f"/calls/{call_id}/end", json={"outcome": "vanished"}).status_code == 422
