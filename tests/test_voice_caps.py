"""The cost guard at the token endpoint: no token (so no Call, so no provider spend) without
admission from the backend. The web server and the real backend are wired together in-process;
LiveKit and the providers are not involved."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("livekit.api")

from tests.conftest import BackendFactory  # noqa: E402
from voice.config import VoiceSettings  # noqa: E402
from voice.web import create_app  # noqa: E402

Json = dict[str, Any]

SETTINGS = VoiceSettings(livekit_api_key="testkey", livekit_api_secret="x" * 40)


def web(backend: httpx.Client, settings: VoiceSettings = SETTINGS) -> TestClient:
    return TestClient(create_app(settings, backend=backend))


def test_a_visitor_gets_three_tokens_and_then_a_visible_refusal(
    backend_factory: BackendFactory,
) -> None:
    client = web(backend_factory())

    assert [client.post("/token").status_code for _ in range(3)] == [200, 200, 200]
    refused = client.post("/token")

    assert refused.status_code == 429
    body: Json = refused.json()
    assert body["reason"] == "visitor_limit"
    assert "token" not in body
    assert "tomorrow" in body["message"]


def test_the_global_cap_refuses_every_visitor(backend_factory: BackendFactory) -> None:
    backend = backend_factory(global_minutes_per_day=5)
    first = web(backend)
    assert first.post("/token").status_code == 200

    other = web(backend)  # a different browser (its own cookie jar)
    refused = other.post("/token")

    assert refused.status_code == 429
    assert refused.json()["reason"] == "global_limit"


def test_the_browser_is_a_server_issued_cookie(backend_factory: BackendFactory) -> None:
    client = web(backend_factory())

    response = client.post("/token")

    cookie = response.headers["set-cookie"]
    assert re.search(r"receptio_browser=[0-9a-f]{32}", cookie)
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()


def test_a_client_chosen_header_does_not_buy_a_new_visitor(
    backend_factory: BackendFactory,
) -> None:
    client = web(backend_factory())
    for n in range(3):
        assert client.post("/token", headers={"X-Forwarded-For": f"9.9.9.{n}"}).status_code == 200

    # Same cookie jar and the same connection address: a new X-Forwarded-For changes nothing.
    spoofed = client.post("/token", headers={"X-Forwarded-For": "1.2.3.4", "X-Visitor-Id": "new"})

    assert spoofed.status_code == 429


def test_dropping_the_cookie_is_bounded_by_the_ip_ceiling(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(ip_calls_per_day=4)

    statuses = [web(backend).post("/token").status_code for _ in range(5)]  # fresh jar each time

    assert statuses == [200, 200, 200, 200, 429]
    assert web(backend).post("/token").json()["reason"] == "ip_limit"


def test_behind_a_trusted_proxy_the_address_the_proxy_appended_is_used(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(ip_calls_per_day=2)
    settings = VoiceSettings(trusted_proxy_hops=1, livekit_api_secret="x" * 40)

    def post(forwarded: str) -> int:
        response = web(backend, settings).post("/token", headers={"X-Forwarded-For": forwarded})
        return response.status_code

    # The left-hand entries are whatever the client claimed; only the last one (added by our
    # proxy) is trusted, so changing the claim does not change the visitor's address.
    assert [post("1.1.1.1, 7.7.7.7"), post("2.2.2.2, 7.7.7.7"), post("3.3.3.3, 7.7.7.7")] == [
        200,
        200,
        429,
    ]
    assert post("1.1.1.1, 8.8.8.8") == 200


def test_if_the_backend_cannot_be_reached_no_token_is_issued() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("backend down")

    client = web(httpx.Client(transport=httpx.MockTransport(down), base_url="http://backend"))

    response = client.post("/token")

    assert response.status_code == 503
    assert "token" not in response.json()


def test_a_backend_error_does_not_issue_a_token() -> None:
    client = web(
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(500)), base_url="http://backend"
        )
    )

    assert client.post("/token").status_code == 503


def test_test_mode_backend_admits_everyone(backend_factory: BackendFactory) -> None:
    client = web(backend_factory(caps_test_mode=True))

    assert [client.post("/token").status_code for _ in range(10)] == [200] * 10


def test_settling_a_finished_call_returns_unused_minutes(backend_factory: BackendFactory) -> None:
    from voice.admission import settle_admission

    backend = backend_factory(global_minutes_per_day=8)
    client = web(backend)
    room = client.post("/token").json()["room"]
    assert web(backend).post("/token").status_code == 429

    settle_admission(backend, room, used_seconds=30)

    assert web(backend).post("/token").status_code == 200


def test_settling_never_raises_when_the_backend_is_down() -> None:
    from voice.admission import settle_admission

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("backend down")

    settle_admission(
        httpx.Client(transport=httpx.MockTransport(down), base_url="http://backend"), "r", 1
    )


NODE = shutil.which("node")
HARNESS = Path(__file__).parent / "web_page_harness.js"
PAGE = Path(__file__).parents[1] / "voice" / "web" / "index.html"


def run_page(status: int, body: Json) -> Json:
    """Run the page's own script under node with a stub DOM, click Start call, and report the
    button and notice. No browser: this checks the logic, not how the page looks."""
    result = subprocess.run(
        [str(NODE), str(HARNESS), str(PAGE), str(status), json.dumps(body)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_the_page_disables_starting_and_shows_the_message_when_a_cap_is_hit() -> None:
    message = "The demo has reached its capacity for today. Please come back tomorrow."

    state = run_page(429, {"reason": "global_limit", "message": message})

    assert state["buttonDisabled"] is True
    assert state["noticeText"] == message
    assert state["noticeHidden"] is False
    assert state["clickAfterwardsFetched"] == 1  # a second click does nothing


@pytest.mark.skipif(NODE is None, reason="needs node")
def test_a_transient_failure_keeps_the_button_usable_and_says_what_happened() -> None:
    state = run_page(503, {"reason": "unavailable", "message": "Try again in a moment."})

    assert state["buttonDisabled"] is False
    assert "Try again in a moment." in state["statusText"]
    assert state["noticeHidden"] is True
