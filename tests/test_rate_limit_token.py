"""Rate limiting of the public token endpoint, per IP, and its relationship to the cost guard.

Rate limiting is abuse throttling; the cost guard's daily counters (ticket 13) stay the only
thing that counts Calls. The tests below check both directions: excess requests are refused, and
a refused request never touches the cost guard's accounting."""

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("livekit.api")

from tests.conftest import BackendFactory  # noqa: E402
from voice.config import VoiceSettings  # noqa: E402
from voice.web import create_app  # noqa: E402

Json = dict[str, Any]


class Ticker:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


def web(backend: httpx.Client, ticker: Ticker, **settings: Any) -> TestClient:
    config = VoiceSettings(livekit_api_secret="x" * 40, **settings)
    return TestClient(create_app(config, backend=backend, now=ticker))


def test_excess_token_requests_from_one_ip_are_refused_with_retry_after(
    backend_factory: BackendFactory,
) -> None:
    ticker = Ticker()
    client = web(backend_factory(caps_test_mode=True), ticker, token_requests_per_minute_per_ip=3)

    statuses = [client.post("/token").status_code for _ in range(5)]

    assert statuses == [200, 200, 200, 429, 429]
    refused = client.post("/token")
    body: Json = refused.json()
    assert body["reason"] == "rate_limited"
    assert "token" not in body
    assert int(refused.headers["retry-after"]) >= 1


def test_the_allowance_returns_after_the_window(backend_factory: BackendFactory) -> None:
    ticker = Ticker()
    client = web(backend_factory(caps_test_mode=True), ticker, token_requests_per_minute_per_ip=1)
    assert client.post("/token").status_code == 200
    assert client.post("/token").status_code == 429

    ticker.now += 61

    assert client.post("/token").status_code == 200


def test_another_ip_is_not_affected(backend_factory: BackendFactory) -> None:
    ticker = Ticker()
    backend = backend_factory(caps_test_mode=True)
    settings = {"token_requests_per_minute_per_ip": 1, "trusted_proxy_hops": 1}
    client = web(backend, ticker, **settings)

    def post(ip: str) -> int:
        return client.post("/token", headers={"X-Forwarded-For": ip}).status_code

    assert [post("7.7.7.1"), post("7.7.7.1"), post("7.7.7.2")] == [200, 429, 200]


def test_the_proxy_semantics_of_ticket_13_apply_to_the_limiter(
    backend_factory: BackendFactory,
) -> None:
    ticker = Ticker()
    backend = backend_factory(caps_test_mode=True)

    # No trusted proxy: X-Forwarded-For is ignored, so rotating it does not escape the limit.
    ignoring = web(backend, ticker, token_requests_per_minute_per_ip=2)
    spoofed = [
        ignoring.post("/token", headers={"X-Forwarded-For": f"9.9.9.{n}"}).status_code
        for n in range(3)
    ]
    assert spoofed == [200, 200, 429]

    # One trusted hop: only the entry our proxy appended counts, whatever the client wrote left.
    trusting = web(backend, ticker, token_requests_per_minute_per_ip=2, trusted_proxy_hops=1)
    claims = [
        trusting.post("/token", headers={"X-Forwarded-For": f"{n}.1.1.1, 8.8.8.8"}).status_code
        for n in range(3)
    ]
    assert claims == [200, 200, 429]


def test_a_rate_limited_request_does_not_use_up_the_visitors_daily_allowance(
    backend_factory: BackendFactory,
) -> None:
    ticker = Ticker()
    backend = backend_factory()  # real caps: 3 Calls per visitor per day
    client = web(backend, ticker, token_requests_per_minute_per_ip=2)
    assert [client.post("/token").status_code for _ in range(2)] == [200, 200]
    assert [client.post("/token").status_code for _ in range(10)] == [429] * 10  # hammering
    ticker.now += 61

    third = client.post("/token")
    fourth = client.post("/token")

    # 2 admitted + 10 rate limited: the allowance of 3 still has exactly one Call left.
    assert third.status_code == 200
    assert fourth.status_code == 429
    assert fourth.json()["reason"] == "visitor_limit"  # the cost guard, not the limiter


def test_a_rate_limited_request_never_reaches_the_cost_guard() -> None:
    reached: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        reached.append(request.url.path)
        return httpx.Response(201, json={"room": "r"})

    backend = httpx.Client(transport=httpx.MockTransport(record), base_url="http://backend")
    client = web(backend, Ticker(), token_requests_per_minute_per_ip=1)

    for _ in range(5):
        client.post("/token")

    assert reached == ["/capacity/admissions"]
