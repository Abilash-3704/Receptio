"""Backend rate limits: capacity admission per IP, and throttling of identification attempts.

Identification is the brute-force target flagged in the earlier audit: nothing but a full name AND
phone proves who a Caller is, so a script posting guesses at the identity endpoints must be
stopped. Failed attempts are counted per name, per phone and overall; successes are free."""

from datetime import timedelta
from typing import Any

import httpx

from tests.conftest import BackendFactory
from tests.support import FakeClock, held, new_key, read_back
from tests.test_capacity import admit
from tests.test_confirm_cancel_reschedule import booked, cancel, reschedule
from tests.test_identity_backend import ASHA, lookup

Json = dict[str, Any]

WRONG_PHONES = [f"+91 98450 0000{n}" for n in range(9)]


def with_asha(backend: httpx.Client) -> Json:
    """Asha becomes a Patient by booking (the one thing in these tests that is not a failure)."""
    return booked(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00", **ASHA)


def test_admission_requests_over_the_per_ip_limit_are_refused(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(caps_test_mode=True, admission_requests_per_minute_per_ip=3)

    statuses = [admit(backend, f"r{n}", ip="203.0.113.5").status_code for n in range(5)]

    assert statuses == [201, 201, 201, 429, 429]
    limited = admit(backend, "r9", ip="203.0.113.5")
    assert limited.json()["reason"] == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1
    assert admit(backend, "r10", ip="203.0.113.6").status_code == 201


def test_a_rate_limited_admission_leaves_the_daily_accounting_untouched(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    backend = backend_factory(admission_requests_per_minute_per_ip=2)
    assert [admit(backend, f"r{n}").status_code for n in range(2)] == [201, 201]
    assert {admit(backend, f"x{n}").status_code for n in range(20)} == {429}
    clock.advance(timedelta(seconds=61))

    third = admit(backend, "r2")
    fourth = admit(backend, "r3")

    # Visitor allowance 3: two were admitted, twenty rate limited ones cost nothing.
    assert third.status_code == 201
    assert fourth.status_code == 429 and fourth.json()["reason"] == "visitor_limit"


def test_the_global_budget_is_not_charged_for_rate_limited_requests(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    # 10 minutes a day = two Calls of 5 reserved minutes each.
    backend = backend_factory(
        global_minutes_per_day=10,
        ip_calls_per_day=100,
        visitor_calls_per_day=100,
        admission_requests_per_minute_per_ip=2,
    )
    assert [admit(backend, f"a{n}").status_code for n in range(2)] == [201, 201]
    assert {admit(backend, f"b{n}").status_code for n in range(10)} == {429}
    clock.advance(timedelta(seconds=61))

    refused = admit(backend, "c0")

    assert refused.json()["reason"] == "global_limit"  # full because of the two real Calls


def test_repeated_wrong_phones_for_one_name_lock_that_name_out(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(identity_failures_per_name=5)
    with_asha(backend)

    codes = [lookup(backend, "Asha Menon", p).status_code for p in WRONG_PHONES[:5]]
    locked = lookup(backend, "Asha Menon", ASHA["phone"])  # even the right phone now

    assert codes == [403] * 5
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) >= 1
    assert "Asha" not in locked.text


def test_repeated_wrong_names_for_one_phone_lock_that_phone_out(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(identity_failures_per_phone=4)
    with_asha(backend)

    for n in range(4):
        assert lookup(backend, f"Guess Number{'x' * n}", "+91 98450 12345").status_code == 403

    assert lookup(backend, "Asha Menon", "+91 98450 12345").status_code == 429


def test_changing_both_name_and_phone_every_time_still_hits_the_overall_limit(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(identity_failures_global=6)

    codes = [lookup(backend, f"Name{'a' * n}", f"+91 7000000{n:04d}").status_code for n in range(8)]

    assert codes[:6] == [403] * 6
    assert codes[6:] == [429, 429]


def test_every_identification_endpoint_shares_the_limit(backend_factory: BackendFactory) -> None:
    backend = backend_factory(identity_failures_per_name=3)
    aid = with_asha(backend)["id"]
    # An unrelated name never set up as a Patient: three failures through three different doors.
    body = {"patient_name": "Asha Menon"}
    assert (
        backend.post(f"/appointments/{aid}/read", json=body | {"patient_phone": "+910000000001"})
    ).status_code == 403
    assert cancel(backend, aid, name="Asha Menon", phone="+910000000002").status_code == 403
    assert (
        reschedule(
            backend,
            aid,
            "Dr. Anita Rao",
            "2026-11-02 15:00",
            name="Asha Menon",
            phone="+910000000003",
        ).status_code
        == 403
    )

    for response in (
        lookup(backend, "Asha Menon", ASHA["phone"]),
        backend.post(f"/appointments/{aid}/read", json=body | {"patient_phone": ASHA["phone"]}),
        cancel(backend, aid, name="Asha Menon", phone=ASHA["phone"]),
    ):
        assert response.status_code == 429


def test_reschedule_read_back_and_confirm_are_throttled_too(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(identity_failures_per_name=3, identity_failures_global=100)
    appointment = with_asha(backend)
    moved = reschedule(backend, appointment["id"], "Dr. Anita Rao", "2026-11-02 15:00", **ASHA)
    assert moved.status_code == 201
    hold_id: int = moved.json()["id"]

    def guess(n: int) -> httpx.Response:
        return backend.post(
            f"/holds/{hold_id}/read-back",
            headers={"Idempotency-Key": new_key()},
            json={"patient_name": "Asha Menon", "patient_phone": f"+91 99999 0000{n}"},
        )

    assert [guess(n).status_code for n in range(3)] == [403, 403, 403]
    assert guess(4).status_code == 429
    confirm_attempt = backend.post(
        f"/holds/{hold_id}/confirm",
        headers={"Idempotency-Key": new_key()},
        json={"patient_name": "Asha Menon", "patient_phone": ASHA["phone"]},
    )
    assert confirm_attempt.status_code == 429


def test_a_new_booking_is_not_held_up_by_a_name_that_is_locked_out(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(identity_failures_per_name=2, identity_failures_global=100)
    for p in WRONG_PHONES[:2]:
        lookup(backend, "Asha Menon", p)
    assert lookup(backend, "Asha Menon", ASHA["phone"]).status_code == 429

    hold_id = held(backend, "Filling", "Dr. Anita Rao", "2026-11-02 14:00")
    assert read_back(backend, hold_id).status_code == 200
    response = backend.post(
        f"/holds/{hold_id}/confirm",
        headers={"Idempotency-Key": new_key()},
        json={"patient_name": "Asha Menon", "patient_phone": ASHA["phone"]},
    )

    assert response.status_code == 201  # no identification was being attempted


def test_successful_identification_is_never_counted(backend_factory: BackendFactory) -> None:
    backend = backend_factory(identity_failures_per_name=2, identity_failures_global=2)
    with_asha(backend)

    codes = [lookup(backend, "Asha Menon", ASHA["phone"]).status_code for _ in range(20)]

    assert codes == [200] * 20


def test_the_lockout_ends_when_the_window_passes(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    backend = backend_factory(identity_failures_per_name=2, identity_failure_window_seconds=600)
    with_asha(backend)
    for p in WRONG_PHONES[:2]:
        lookup(backend, "Asha Menon", p)
    assert lookup(backend, "Asha Menon", ASHA["phone"]).status_code == 429

    clock.advance(timedelta(seconds=601))

    assert lookup(backend, "Asha Menon", ASHA["phone"]).status_code == 200


def test_name_and_phone_spellings_share_one_counter(backend_factory: BackendFactory) -> None:
    backend = backend_factory(identity_failures_per_name=3, identity_failures_global=100)
    with_asha(backend)
    for name, phone in [
        ("asha menon", WRONG_PHONES[0]),
        ("  ASHA   Menon ", WRONG_PHONES[1]),
        ("Asha Menon", WRONG_PHONES[2]),
    ]:
        lookup(backend, name, phone)

    assert lookup(backend, "ASHA MENON", ASHA["phone"]).status_code == 429
