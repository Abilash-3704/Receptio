"""The voice side of the duration cap: a timer that speaks the 4:30 warning and the close for a
Caller who says nothing, then ends the session. No LiveKit: the pipeline's speak and close are
stand-ins; the clock and the sleeping are injected."""

import asyncio
from datetime import timedelta

import httpx

from agent.call_limits import CallLimits
from agent.llm import Say
from agent.scripted_llm import ScriptedLLM
from agent.text_mode import open_text_call
from tests.support import FakeClock
from voice.driver import VoiceTurnDriver, watch_call_limits
from voice.timing import TurnTimings


def driver_for(backend: httpx.Client, clock: FakeClock) -> VoiceTurnDriver:
    call = open_text_call(
        backend, llm=ScriptedLLM([Say("ok")]), clock=clock, limits=CallLimits(300, 270)
    )
    return VoiceTurnDriver(call, TurnTimings(call.call_id, emit=lambda line: None))


def test_a_silent_caller_hears_the_warning_then_the_close_and_the_session_ends(
    backend: httpx.Client, clock: FakeClock
) -> None:
    driver = driver_for(backend, clock)
    events: list[str] = []

    async def speak(text: str) -> None:
        events.append("say: " + text)

    async def close() -> None:
        events.append("close")

    async def sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))  # time passes only when the watcher waits

    asyncio.run(watch_call_limits(driver, speak, close, poll_seconds=10, sleep=sleep))

    assert len(events) == 3
    assert "30 seconds" in events[0] and "call you back" in events[0]
    assert "time limit" in events[1]
    assert events[2] == "close"  # closed only after the close was spoken


def test_the_watcher_stops_when_the_call_ends_first(
    backend: httpx.Client, clock: FakeClock
) -> None:
    driver = driver_for(backend, clock)
    ended = asyncio.Event()
    ended.set()  # the Caller hung up

    async def speak(text: str) -> None:
        raise AssertionError("nothing to say to a Caller who has gone")

    async def close() -> None:
        raise AssertionError("already closed")

    async def sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    asyncio.run(watch_call_limits(driver, speak, close, poll_seconds=10, sleep=sleep, stop=ended))
