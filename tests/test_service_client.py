"""Seam: the agent's one way to reach the backend carries the service token from the env."""

import httpx
import pytest

from agent.backend_client import make_backend_client
from agent.config import AgentSettings
from tests.support import SERVICE_TOKEN


def test_the_agent_client_sends_the_service_token_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKEND_SERVICE_TOKEN", SERVICE_TOKEN)
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    client = make_backend_client(AgentSettings(), transport=httpx.MockTransport(respond))
    client.get("/providers")

    assert seen[0].headers["X-Service-Token"] == SERVICE_TOKEN


def test_without_a_token_the_agent_sends_none_and_never_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BACKEND_SERVICE_TOKEN", raising=False)
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    client = make_backend_client(
        AgentSettings(_env_file=None),  # pyright: ignore[reportCallIssue]
        transport=httpx.MockTransport(respond),
    )
    client.get("/providers")

    assert "X-Service-Token" not in seen[0].headers


def test_the_token_never_appears_in_the_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKEND_SERVICE_TOKEN", SERVICE_TOKEN)
    assert SERVICE_TOKEN not in repr(AgentSettings())
