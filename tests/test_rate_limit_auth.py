"""Throttling of failed authentication (ticket 18's staff password and service token), per client
address. Successes are free; a locked-out address is answered 429 before its credentials are even
compared, so it cannot keep guessing."""

from datetime import timedelta
from typing import cast

import httpx
from fastapi.testclient import TestClient

from tests.conftest import BackendFactory
from tests.support import STAFF_PASSWORD, FakeClock
from tests.test_auth import anonymous, basic

LIMIT = 4


def guess(client: TestClient, password: str = "wrong-password-0123456789") -> httpx.Response:
    response = client.get("/escalations", headers=basic(password))  # a staff route
    return cast(httpx.Response, response)  # call-compatible; see tests/conftest.py


def test_repeated_wrong_staff_passwords_lock_the_address_out(
    backend_factory: BackendFactory,
) -> None:
    client = anonymous(backend_factory(auth_failures_per_ip=LIMIT))

    codes = [guess(client).status_code for _ in range(LIMIT)]
    locked = guess(client)

    assert codes == [401] * LIMIT
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) >= 1


def test_the_right_password_is_refused_while_locked_out(backend_factory: BackendFactory) -> None:
    client = anonymous(backend_factory(auth_failures_per_ip=LIMIT))
    for _ in range(LIMIT):
        guess(client)

    assert guess(client, STAFF_PASSWORD).status_code == 429


def test_a_wrong_service_token_counts_the_same_way(backend_factory: BackendFactory) -> None:
    client = anonymous(backend_factory(auth_failures_per_ip=LIMIT))

    codes = [
        client.post("/calls", headers={"X-Service-Token": f"wrong-{n}"}).status_code
        for n in range(LIMIT + 1)
    ]

    assert codes == [401] * LIMIT + [429]


def test_successful_requests_are_never_counted(backend_factory: BackendFactory) -> None:
    client = anonymous(backend_factory(auth_failures_per_ip=LIMIT))

    codes = [guess(client, STAFF_PASSWORD).status_code for _ in range(LIMIT * 5)]

    assert codes == [200] * (LIMIT * 5)


def test_the_lockout_ends_after_the_window(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    client = anonymous(backend_factory(auth_failures_per_ip=LIMIT, auth_failure_window_seconds=600))
    for _ in range(LIMIT):
        guess(client)
    assert guess(client, STAFF_PASSWORD).status_code == 429

    clock.advance(timedelta(seconds=601))

    assert guess(client, STAFF_PASSWORD).status_code == 200


def test_another_address_is_not_locked_out_and_proxy_hops_decide_which_address(
    backend_factory: BackendFactory,
) -> None:
    client = anonymous(backend_factory(auth_failures_per_ip=LIMIT, trusted_proxy_hops=1))

    def from_ip(ip: str, password: str) -> int:
        headers = basic(password) | {"X-Forwarded-For": f"1.1.1.1, {ip}"}
        return client.get("/escalations", headers=headers).status_code

    for _ in range(LIMIT):
        from_ip("7.7.7.7", "wrong-password-0123456789")

    assert from_ip("7.7.7.7", STAFF_PASSWORD) == 429
    assert from_ip("8.8.8.8", STAFF_PASSWORD) == 200  # the left-hand entries were never trusted


def test_without_a_trusted_proxy_x_forwarded_for_cannot_dodge_the_lockout(
    backend_factory: BackendFactory,
) -> None:
    client = anonymous(backend_factory(auth_failures_per_ip=LIMIT))  # TRUSTED_PROXY_HOPS=0

    for n in range(LIMIT):
        client.get(
            "/escalations",
            headers=basic("wrong-password-0123456789") | {"X-Forwarded-For": f"{n}.0.0.1"},
        )

    spoofed = client.get(
        "/escalations", headers=basic(STAFF_PASSWORD) | {"X-Forwarded-For": "9.9.9.9"}
    )
    assert spoofed.status_code == 429


def test_the_public_health_check_still_answers_while_an_address_is_locked_out(
    backend_factory: BackendFactory,
) -> None:
    client = anonymous(backend_factory(auth_failures_per_ip=LIMIT))
    for _ in range(LIMIT):
        guess(client)

    assert client.get("/health").status_code == 200
