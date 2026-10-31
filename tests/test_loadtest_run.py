"""The stand-in load run, end to end at small scale: web token endpoint, capacity admission, the
real agent core and backend, persisted turn timings. Latencies are tiny so the test is quick."""

from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("livekit.api")

from loadtest.report import summarise_run  # noqa: E402
from loadtest.runner import LoadConfig, run_load  # noqa: E402
from loadtest.standins import StandInLatency  # noqa: E402
from tests.conftest import POSTGRES_URL, BackendFactory  # noqa: E402
from voice.config import VoiceSettings  # noqa: E402
from voice.web import create_app  # noqa: E402

# Threads against the in-memory SQLite (one shared connection) are not a fair test of anything:
# like the concurrency tests, these need a real Postgres.
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="the load run needs a real Postgres: set TEST_DATABASE_URL"
)

FAST = StandInLatency(asr_ms=5, endpointing_ms=5, llm_step_ms=20, tts_first_audio_ms=5, sigma=0.0)


def web(backend: httpx.Client, **settings: Any) -> httpx.Client:
    config = VoiceSettings(
        livekit_api_secret="x" * 40,
        trusted_proxy_hops=1,
        token_requests_per_minute_per_ip=10_000,
        **settings,
    )
    return cast(httpx.Client, TestClient(create_app(config, backend=backend)))


def test_concurrent_stand_in_calls_are_admitted_run_and_persist_their_timings(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(caps_test_mode=True, admission_requests_per_minute_per_ip=10_000)

    result = run_load(LoadConfig(calls=6, latency=FAST), web(backend), backend)

    assert result.admission == {"admitted": 6}
    assert result.errors == []
    assert result.max_in_flight >= 2  # they really overlapped
    assert all(c.call_id is not None for c in result.calls)
    # Every Call was stored and ended; every Caller turn got a persisted timing.
    summary = summarise_run(cast(Any, backend).app.state.session_factory, result)
    assert summary.calls == 6
    assert (
        summary.calls_by_outcome.get("completed", 0) + summary.calls_by_outcome.get("dropped", 0)
        == 6
    )
    expected_turns = sum(len(c.turn_ms) for c in result.calls)
    assert expected_turns > 6
    assert summary.timing_samples == expected_turns
    assert summary.total_from_speech_end_ms is not None
    # Stand-in floor: asr 5 + endpointing 5 + two LLM steps 20 + tts 5 for a tool turn: at least 15
    # ms for any turn, and our own overhead is whatever is above that.
    assert summary.total_from_speech_end_ms["p50"] >= 15


def test_refusals_by_the_cost_guard_are_counted_not_treated_as_errors(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(ip_calls_per_day=2, admission_requests_per_minute_per_ip=10_000)

    # One shared address (no per-call X-Forwarded-For): the per-IP ceiling admits 2 of 6.
    result = run_load(LoadConfig(calls=6, latency=FAST, distinct_ips=False), web(backend), backend)

    assert result.admission == {"admitted": 2, "refused:ip_limit": 4}
    assert result.errors == []
    assert [c.admitted for c in result.calls].count(True) == 2


def test_rate_limited_requests_are_reported_as_such(backend_factory: BackendFactory) -> None:
    backend = backend_factory(caps_test_mode=True, admission_requests_per_minute_per_ip=10_000)
    limited = VoiceSettings(
        livekit_api_secret="x" * 40, token_requests_per_minute_per_ip=2, trusted_proxy_hops=0
    )
    client = cast(httpx.Client, TestClient(create_app(limited, backend=backend)))

    result = run_load(LoadConfig(calls=5, latency=FAST, distinct_ips=False), client, backend)

    assert result.admission == {"admitted": 2, "refused:rate_limited": 3}


def test_same_slot_contention_shows_up_as_failed_tool_calls_not_crashes(
    backend_factory: BackendFactory,
) -> None:
    backend = backend_factory(caps_test_mode=True, admission_requests_per_minute_per_ip=10_000)

    result = run_load(LoadConfig(calls=8, latency=FAST, contention="same"), web(backend), backend)

    assert result.errors == []
    summary = summarise_run(cast(Any, backend).app.state.session_factory, result)
    assert summary.tool_calls > 0
