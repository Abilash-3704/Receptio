"""Seam: cost and latency stored with a Call. A text-mode Call through the real agent core, the
real Claude adapter over a stub client (no network, no money) and the real backend.

Prices are configuration. Claude tokens are what the provider reported; Cartesia characters are
counted from what the agent said; Deepgram seconds are an estimate from the Call's length."""

from collections.abc import Callable, Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.anthropic_llm import AnthropicLLM
from agent.llm import Say
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call
from backend.app.config import BackendSettings
from backend.app.main import create_app
from backend.app.trace import load_trace
from tests.support import START_OF_TEST, FakeClock

Trace = Callable[[int], dict[str, Any]]
MODEL = "claude-test-1"


class Stub:
    """Answers `messages.create` with a fixed reply and the usage the provider would report."""

    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.requests = 0
        self._usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **_: Any) -> Any:
        self.requests += 1
        block = SimpleNamespace(type="text", text="How can I help?")
        return SimpleNamespace(content=[block], usage=self._usage)


def serve(settings: BackendSettings, clock: FakeClock) -> Iterator[tuple[httpx.Client, Trace]]:
    with TestClient(create_app(settings, clock=clock), base_url="http://backend") as client:
        factory = cast(Any, client).app.state.session_factory

        def read(call_id: int) -> dict[str, Any]:
            with factory() as session:
                return load_trace(session, call_id, cast(Any, client).app.state.prices)

        yield cast(httpx.Client, client), read


def configured(**prices: Any) -> BackendSettings:
    return BackendSettings(database_url="sqlite+pysqlite:///:memory:", **prices)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START_OF_TEST)


@pytest.fixture
def priced(clock: FakeClock) -> Iterator[tuple[httpx.Client, Trace]]:
    yield from serve(
        configured(
            llm_prices_per_mtok={MODEL: (2.0, 10.0)},
            deepgram_usd_per_minute=0.01,
            cartesia_usd_per_1k_characters=0.05,
        ),
        clock,
    )


@pytest.fixture
def unconfigured(clock: FakeClock) -> Iterator[tuple[httpx.Client, Trace]]:
    yield from serve(configured(), clock)


def test_each_llm_request_is_stored_with_the_reported_tokens_and_its_cost(
    priced: tuple[httpx.Client, Trace],
) -> None:
    backend, trace = priced
    llm = AnthropicLLM(Stub(1_000_000, 100_000), model=MODEL, timeout_seconds=5)
    call = open_text_call(backend, llm=llm)

    call.hear("Hello")

    [request] = trace(call.call_id)["turns"][1]["llm_calls"]
    assert (request["model"], request["input_tokens"], request["output_tokens"]) == (
        MODEL,
        1_000_000,
        100_000,
    )
    assert request["cost_usd"] == pytest.approx(2.0 + 1.0)  # 1M x $2 + 0.1M x $10
    assert request["duration_ms"] is not None and request["duration_ms"] >= 0
    assert trace(call.call_id)["turns"][1]["cost"]["claude_usd"] == pytest.approx(3.0)


def test_the_call_total_says_which_parts_are_measured_and_which_are_estimated(
    priced: tuple[httpx.Client, Trace], clock: FakeClock
) -> None:
    backend, trace = priced
    llm = AnthropicLLM(Stub(1_000_000, 100_000), model=MODEL, timeout_seconds=5)
    call = open_text_call(backend, llm=llm, clock=clock, mode="voice")
    call.hear("Hello")
    clock.advance(timedelta(seconds=90))
    call.end()

    cost = trace(call.call_id)["call"]["cost"]

    claude, deepgram, cartesia = (cost["components"][k] for k in ("claude", "deepgram", "cartesia"))
    assert claude["basis"] == "measured" and claude["usd"] == pytest.approx(3.0)
    assert (claude["input_tokens"], claude["output_tokens"]) == (1_000_000, 100_000)
    assert deepgram["basis"] == "estimated" and deepgram["seconds"] == 90
    assert deepgram["usd"] == pytest.approx(0.015)  # 1.5 minutes x $0.01
    spoken = sum(len(t.text) for t in call.transcript if t.speaker == "agent")
    assert cartesia["basis"] == "counted" and cartesia["characters"] == spoken
    assert cartesia["usd"] == pytest.approx(spoken / 1000 * 0.05)
    assert cost["total_usd"] == pytest.approx(3.0 + 0.015 + cartesia["usd"])
    agent_turn = trace(call.call_id)["turns"][0]
    assert agent_turn["cost"]["cartesia_usd"] == pytest.approx(
        len(agent_turn["text"]) / 1000 * 0.05
    )
    assert cost["placeholder_prices"] is False


def test_a_text_call_has_no_audio_cost(priced: tuple[httpx.Client, Trace]) -> None:
    backend, trace = priced
    llm = AnthropicLLM(Stub(10, 5), model=MODEL, timeout_seconds=5)
    call = open_text_call(backend, llm=llm)
    call.hear("Hello")
    call.end()

    components = trace(call.call_id)["call"]["cost"]["components"]

    assert (components["deepgram"], components["cartesia"]) == (None, None)


def test_prices_nobody_configured_are_flagged_as_placeholders(
    unconfigured: tuple[httpx.Client, Trace],
) -> None:
    backend, trace = unconfigured
    call = open_text_call(backend, llm=ScriptedLLM([Say("Hi.")]), mode="voice")
    call.hear("Hello")
    call.end()

    cost = trace(call.call_id)["call"]["cost"]

    assert cost["placeholder_prices"] is True
    assert cost["components"]["deepgram"]["placeholder_price"] is True
    assert cost["components"]["cartesia"]["placeholder_price"] is True


def test_a_model_without_a_price_is_stored_unpriced_not_guessed(
    priced: tuple[httpx.Client, Trace],
) -> None:
    backend, trace = priced
    llm = AnthropicLLM(Stub(1000, 10), model="claude-unpriced", timeout_seconds=5)
    call = open_text_call(backend, llm=llm)
    call.hear("Hello")
    call.end()

    stored = trace(call.call_id)

    assert stored["turns"][1]["llm_calls"][0]["cost_usd"] is None
    claude = stored["call"]["cost"]["components"]["claude"]
    assert (claude["usd"], claude["unpriced_models"]) == (None, ["claude-unpriced"])
    assert claude["input_tokens"] == 1000  # the tokens are still recorded


def test_voice_latency_is_stored_per_turn_and_summarised_per_call(
    priced: tuple[httpx.Client, Trace],
) -> None:
    backend, trace = priced
    call = open_text_call(backend, llm=ScriptedLLM([Say("Hi."), Say("Ok.")]), mode="voice")
    call.hear("Hello")
    call.hear("Thanks")
    for seq, total in ((2, 900), (4, 1100)):
        backend.post(
            f"/calls/{call.call_id}/turn-timings",
            json={
                "caller_turn_seq": seq,
                "asr_final_ms": -50,
                "llm_first_token_ms": 400,
                "tts_first_audio_ms": total - 400,
                "total_ms": total,
                "total_from_speech_end_ms": total + 50,
                "interrupted": False,
            },
        ).raise_for_status()
    call.end()

    stored = trace(call.call_id)

    assert [t["latency"]["total_ms"] for t in stored["turns"] if t["speaker"] == "caller"] == [
        900,
        1100,
    ]
    assert stored["turns"][0]["latency"] is None  # the disclosure answered nobody
    assert stored["call"]["latency"] == {"turns": 2, "p50_ms": 900, "p95_ms": 1100, "max_ms": 1100}
