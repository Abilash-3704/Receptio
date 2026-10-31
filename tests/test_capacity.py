"""Cost guard, backend side: admission of a new Call against per-visitor, per-IP and global daily
caps, through the HTTP interface. Counters are in the database; concurrency is in
test_capacity_concurrency.py (Postgres only)."""

from datetime import timedelta
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.config import BackendSettings
from tests.conftest import BackendFactory
from tests.support import FakeClock

Json = dict[str, Any]


def admit(
    backend: httpx.Client, room: str, browser: str = "b1", ip: str = "203.0.113.5"
) -> httpx.Response:
    return backend.post("/capacity/admissions", json={"room": room, "browser": browser, "ip": ip})


def settle(backend: httpx.Client, room: str, used_seconds: int) -> httpx.Response:
    return backend.post(f"/capacity/admissions/{room}/settle", json={"used_seconds": used_seconds})


def test_a_visitor_gets_three_calls_a_day_and_the_fourth_is_refused(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory()

    assert [admit(backend, f"r{n}").status_code for n in range(3)] == [201, 201, 201]
    refused = admit(backend, "r3")

    assert refused.status_code == 429
    body: Json = refused.json()
    assert body["reason"] == "visitor_limit"
    assert "limit" in body["message"].lower()


def test_the_visitor_is_browser_plus_ip(backend_factory: BackendFactory) -> None:
    backend = backend_factory()
    for n in range(3):
        admit(backend, f"a{n}", browser="b1", ip="203.0.113.5")

    other_browser = admit(backend, "x1", browser="b2", ip="203.0.113.5")
    other_ip = admit(backend, "x2", browser="b1", ip="203.0.113.9")

    assert other_browser.status_code == 201
    assert other_ip.status_code == 201


def test_the_visitor_allowance_comes_back_the_next_day(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    backend = backend_factory()
    for n in range(3):
        admit(backend, f"a{n}")
    assert admit(backend, "a3").status_code == 429

    clock.advance(timedelta(days=1))

    assert admit(backend, "a4").status_code == 201


def test_one_ip_is_bounded_even_when_the_browser_id_changes(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(ip_calls_per_day=4)

    statuses = [admit(backend, f"r{n}", browser=f"fresh-{n}").status_code for n in range(5)]

    assert statuses == [201, 201, 201, 201, 429]
    assert admit(backend, "other", browser="fresh-9", ip="198.51.100.1").status_code == 201


def test_the_global_cap_is_minutes_per_day_and_reserves_the_longest_a_call_can_run(
    backend_factory: BackendFactory,
) -> None:
    # 120 minutes at 5 minutes per Call reserved: 24 Calls fit, the 25th does not.
    backend = backend_factory(ip_calls_per_day=1000, visitor_calls_per_day=1000)

    statuses = [admit(backend, f"r{n}", browser=f"b{n}").status_code for n in range(25)]

    assert statuses == [201] * 24 + [429]
    assert admit(backend, "late", browser="late").json()["reason"] == "global_limit"


def test_a_global_refusal_does_not_use_up_the_visitors_allowance(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(global_minutes_per_day=5)
    assert admit(backend, "first", browser="b1").status_code == 201

    for n in range(4):
        assert admit(backend, f"no{n}", browser="b2").status_code == 429
    settle(backend, "first", used_seconds=0)

    # Four refusals would have exhausted b2's three Calls if refusals counted.
    assert admit(backend, "yes", browser="b2").status_code == 201


def test_a_short_call_gives_its_unused_minutes_back(backend_factory: BackendFactory) -> None:
    backend = backend_factory(global_minutes_per_day=8)  # room for one full 5-minute Call
    admit(backend, "one", browser="b1")
    assert admit(backend, "two", browser="b2").status_code == 429

    assert settle(backend, "one", used_seconds=60).status_code == 200

    assert admit(backend, "two", browser="b2").status_code == 201


def anonymous(backend: httpx.Client) -> httpx.Client:
    """The same backend, a client that holds no credentials: anyone who can reach its port."""
    return cast(httpx.Client, TestClient(cast(Any, backend).app, base_url="http://backend"))


def test_settling_needs_the_service_token_so_a_stranger_cannot_refund_the_cap(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(global_minutes_per_day=5)
    admit(backend, "one", browser="b1")

    stranger = settle(anonymous(backend), "one", used_seconds=0)

    assert stranger.status_code == 401
    assert admit(backend, "two", browser="b2").status_code == 429  # nothing was given back


def test_a_call_cannot_claim_to_have_used_less_than_the_time_the_backend_saw(
    backend_factory: BackendFactory, clock: FakeClock
) -> None:
    backend = backend_factory(global_minutes_per_day=8)  # 480 s: one 300 s reservation, 180 free
    admit(backend, "one", browser="b1")
    clock.advance(timedelta(seconds=200))  # the room has been open 200 s

    settle(backend, "one", used_seconds=0)  # claims it used nothing

    # Only 100 s may come back (300 reserved - 200 seen): 280 free, a new Call needs 300.
    assert admit(backend, "two", browser="b2").status_code == 429


def test_a_call_cannot_settle_for_more_than_it_reserved(backend_factory: BackendFactory) -> None:
    backend = backend_factory(global_minutes_per_day=5)
    admit(backend, "one", browser="b1")

    settle(backend, "one", used_seconds=10_000)

    assert admit(backend, "two", browser="b2").status_code == 429


def test_settling_twice_returns_the_minutes_once(backend_factory: BackendFactory) -> None:
    backend = backend_factory(global_minutes_per_day=10, visitor_calls_per_day=10)
    admit(backend, "one", browser="b1")
    admit(backend, "two", browser="b2")
    assert admit(backend, "three", browser="b3").status_code == 429

    settle(backend, "one", used_seconds=0)
    settle(backend, "one", used_seconds=0)

    assert admit(backend, "three", browser="b3").status_code == 201
    assert admit(backend, "four", browser="b4").status_code == 429


def test_settling_an_unknown_room_is_a_404(backend_factory: BackendFactory) -> None:
    assert settle(backend_factory(), "nope", used_seconds=5).status_code == 404


def test_admitting_the_same_room_twice_does_not_charge_twice(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory()

    assert admit(backend, "same").status_code == 201
    assert admit(backend, "same").status_code == 201

    assert [admit(backend, f"r{n}").status_code for n in range(3)] == [201, 201, 429]


def test_caps_are_configuration(backend_factory: BackendFactory) -> None:
    backend = backend_factory(visitor_calls_per_day=1)

    assert admit(backend, "r1").status_code == 201
    assert admit(backend, "r2").status_code == 429


def test_the_defaults_are_the_specified_caps() -> None:
    s = BackendSettings()

    assert s.call_max_seconds == 300
    assert s.visitor_calls_per_day == 3
    assert s.global_minutes_per_day == 120
    assert s.caps_test_mode is False
    assert s.deployed_environment is False


def test_test_mode_lifts_the_caps(backend_factory: BackendFactory) -> None:
    backend = backend_factory(caps_test_mode=True)

    statuses = [admit(backend, f"r{n}").status_code for n in range(30)]

    assert statuses == [201] * 30
    assert settle(backend, "r0", used_seconds=10).status_code == 200


def test_test_mode_cannot_be_enabled_in_a_deployed_environment() -> None:
    with pytest.raises(ValidationError, match="test mode"):
        BackendSettings(caps_test_mode=True, deployed_environment=True)


def test_test_mode_cannot_be_enabled_by_environment_variable_when_deployed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPS_TEST_MODE", "true")
    monkeypatch.setenv("DEPLOYED_ENVIRONMENT", "true")

    with pytest.raises(ValidationError, match="test mode"):
        BackendSettings()
