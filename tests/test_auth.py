"""Seam: the backend's HTTP interface, with and without credentials.

Two kinds of caller: the agent and voice worker (service token in `X-Service-Token`) and clinic
staff (HTTP Basic, user `staff`). Every route needs one of them except the explicit PUBLIC list;
the enumeration test fails when a route is added without protection."""

import base64
import re
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.app.config import BackendSettings
from tests.conftest import BackendFactory
from tests.support import SERVICE_TOKEN, STAFF_PASSWORD

# The only routes that answer without credentials, and why.
PUBLIC = {
    ("GET", "/health"),  # liveness probe
    # The Caller-facing cost guard: the public token endpoint (voice/web.py) calls these for a
    # visitor who has no credentials. Ticket 13 built them so; ticket 18 leaves them.
    ("POST", "/capacity/admissions"),
    ("POST", "/capacity/admissions/{room}/settle"),
}


@pytest.fixture
def backend(backend_factory: BackendFactory) -> httpx.Client:
    """These tests sweep every route with wrong credentials, hundreds of failures from one address:
    the lockout (tests/test_rate_limit_auth.py) is lifted here so it does not answer 429 instead
    of the 401 being asserted."""
    return backend_factory(auth_failures_per_ip=1_000_000)


def basic(password: str, user: str = "staff") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def app_of(backend: httpx.Client) -> FastAPI:
    return cast(FastAPI, cast(Any, backend).app)


def anonymous(backend: httpx.Client) -> TestClient:
    """The same app, a client that carries no credentials at all."""
    return TestClient(app_of(backend), base_url="http://backend")


def routes(app: FastAPI) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for route in app.routes:
        assert isinstance(route, APIRoute), f"unexpected non-API route {route!r}"
        found += [(m, route.path) for m in sorted(route.methods - {"HEAD", "OPTIONS"})]
    return found


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_every_route_outside_the_public_list_rejects_a_request_without_credentials(
    backend: httpx.Client,
) -> None:
    client = anonymous(backend)
    all_routes = routes(app_of(backend))
    assert set(all_routes) >= PUBLIC  # the allowlist cannot go stale
    assert ("GET", "/calls/{call_id}") in all_routes

    open_routes = [
        (m, p)
        for m, p in all_routes
        if (m, p) not in PUBLIC and client.request(m, concrete(p), json={}).status_code != 401
    ]
    assert open_routes == [], "routes answering without credentials: add auth or list in PUBLIC"


def test_a_wrong_service_token_or_password_is_rejected_on_every_protected_route(
    backend: httpx.Client,
) -> None:
    client = anonymous(backend)
    for method, path in routes(app_of(backend)):
        if (method, path) in PUBLIC:
            continue
        bad: list[dict[str, Any]] = [
            {"X-Service-Token": "wrong-token-wrong-token-1234"},
            {"X-Service-Token": "tökén-non-ascii".encode()},
            basic("wrong-password-wrong-1234"),
            basic(STAFF_PASSWORD, user="someone-else"),
        ]
        for headers in bad:
            response = client.request(method, concrete(path), json={}, headers=headers)
            assert response.status_code == 401, (method, path, headers)


def test_staff_routes_reject_the_agents_service_token_and_service_routes_reject_staff(
    backend: httpx.Client,
) -> None:
    client = anonymous(backend)
    service = {"X-Service-Token": SERVICE_TOKEN}
    staff = basic(STAFF_PASSWORD)

    for path in ("/escalations", "/confirmation-messages", "/calls/1"):
        assert client.get(path, headers=service).status_code == 403, path
        assert client.get(path, headers=staff).status_code != 403, path
    override = client.put("/demo/clinic-override", json={"mode": None}, headers=service)
    assert override.status_code == 403

    for method, path in (
        ("POST", "/calls"),
        ("POST", "/calls/1/tool-calls"),
        ("GET", "/providers"),
    ):
        assert client.request(method, path, json={}, headers=staff).status_code == 403, path
        assert client.request(method, path, json={}, headers=service).status_code != 403, path


def test_the_agent_can_open_and_write_a_call_but_not_read_it_back(backend: httpx.Client) -> None:
    client = anonymous(backend)
    service = {"X-Service-Token": SERVICE_TOKEN}
    opened = client.post("/calls", json={}, headers=service)
    assert opened.status_code == 201
    call_id = opened.json()["id"]
    turn = {"speaker": "caller", "text": "hello"}
    assert client.post(f"/calls/{call_id}/turns", json=turn, headers=service).status_code == 201
    assert client.get(f"/calls/{call_id}", headers=service).status_code == 403
    assert client.get(f"/calls/{call_id}", headers=basic(STAFF_PASSWORD)).status_code == 200


def test_a_backend_without_secrets_configured_refuses_everyone(
    backend_factory: BackendFactory,
) -> None:
    unconfigured = backend_factory(backend_service_token=None, dashboard_password=None)
    client = anonymous(unconfigured)
    # Whatever a client sends, there is no default password to match.
    for headers in ({}, {"X-Service-Token": ""}, basic(""), basic("staff"), basic("password")):
        assert client.get("/escalations", headers=headers).status_code == 503
        assert client.post("/calls", json={}, headers=headers).status_code == 503
    assert client.get("/health").status_code == 200


def test_a_short_secret_counts_as_not_configured() -> None:
    settings = BackendSettings(
        database_url="sqlite+pysqlite:///:memory:",
        backend_service_token="short",  # pyright: ignore[reportArgumentType]
        dashboard_password="short",  # pyright: ignore[reportArgumentType]
    )
    assert settings.backend_service_token is None
    assert settings.dashboard_password is None
