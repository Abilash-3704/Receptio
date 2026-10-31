"""The web client's server side: the token endpoint and the static page (no LiveKit server)."""

from typing import Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("livekit.api")

from voice.config import VoiceSettings  # noqa: E402
from voice.web import create_app  # noqa: E402

SETTINGS = VoiceSettings(
    livekit_url="ws://livekit:7880",
    livekit_public_url="ws://localhost:7880",
    livekit_api_key="testkey",
    livekit_api_secret="x" * 40,
)


def client(backend: httpx.Client) -> TestClient:
    return TestClient(create_app(SETTINGS, backend=backend))


def test_token_endpoint_gives_a_caller_a_token_for_a_fresh_room(backend: httpx.Client) -> None:
    response = client(backend).post("/token")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["url"] == "ws://localhost:7880"  # what the browser dials, not the internal URL
    claims: dict[str, Any] = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
        body["token"], SETTINGS.livekit_api_secret, algorithms=["HS256"]
    )
    assert claims["iss"] == "testkey"
    assert claims["video"]["roomJoin"] is True
    assert claims["video"]["room"] == body["room"]
    assert claims["sub"].startswith("caller-")
    # A token lets the Caller join one room and nothing else: no admin, no room creation.
    assert not claims["video"].get("roomAdmin")
    assert not claims["video"].get("roomCreate")


def test_every_call_gets_its_own_room(backend: httpx.Client) -> None:
    c = client(backend)
    rooms = {c.post("/token").json()["room"] for _ in range(3)}
    assert len(rooms) == 3


def test_the_page_is_served_and_pins_the_livekit_client_version(backend: httpx.Client) -> None:
    page = client(backend).get("/")

    assert page.status_code == 200
    assert "livekit-client@" in page.text
    assert "@latest" not in page.text
