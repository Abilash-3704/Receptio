"""One Caller turn through the agent core, for the voice pipeline. Imports no LiveKit.

The pipeline hands over the recognised text; the driver runs the same `TextCall.hear` as text
mode (safety detector first, code-recorded read-back yes, Escalation rules, time and price
guards) and yields the reply sentence by sentence so speech synthesis can start on the first.

The core hands each piece of the reply over as soon as it is ready (a fixed phrase while a tool
runs, then the model's answer once the whole step has passed the guards: a time Availability did
not return is withheld, not spoken). The "LLM first token" moment the timing record logs is when
the first piece became available to speak."""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from agent.text_mode import TextCall
from voice.timing import TurnTimings

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text.strip()) if s]


@dataclass(frozen=True)
class _Snapshot:
    """The core's profile at one moment: when speech began, or when the turn ended."""

    caller_turn: int
    llm_steps: int
    llm_ms: int
    tool_ms: int
    tools: list[str]


class VoiceTurnDriver:
    def __init__(
        self,
        call: TextCall,
        timings: TurnTimings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._call = call
        self._timings = timings
        self._clock = clock

    @property
    def call_id(self) -> int:
        return self._call.call_id

    async def reply(self, caller_text: str) -> AsyncIterator[str]:
        """The reply, sentence by sentence."""
        async for segment in self.segments(caller_text):
            for sentence in segment:
                yield sentence

    async def segments(self, caller_text: str) -> AsyncIterator[list[str]]:
        """The reply as the pieces the core handed over, each a list of sentences. A piece is
        something the Caller should hear now, whatever follows: a sentence the model has just
        written, or "Let me look that up" before a tool call. The pipeline speaks each piece at
        once (see ReceptioAgent.llm_node); left to buffer, speech synthesis holds a lone
        sentence until more text arrives, so that line would only be heard after the answer."""
        # The core is synchronous (blocking LLM and backend calls): keep the event loop free so
        # audio keeps flowing and the Caller can still barge in. It runs in a thread and hands
        # each piece of the reply over the moment it is ready, so speech synthesis starts on the
        # first sentence while the model is still writing the rest.
        loop = asyncio.get_running_loop()
        pieces: asyncio.Queue[tuple[str, _Snapshot | None] | None] = asyncio.Queue()
        stopped = False  # the Caller barged in or the Call ended: stop feeding the pipeline
        first = True

        def emit(text: str) -> None:  # runs in the core's thread
            nonlocal first
            if stopped:
                return
            snapshot = None
            if first:
                first = False
                snapshot = self._snapshot()  # what the profile holds as speech begins
            loop.call_soon_threadsafe(pieces.put_nowait, (text, snapshot))

        def run() -> list[str]:
            try:
                return self._call.hear(caller_text, emit)
            finally:
                loop.call_soon_threadsafe(pieces.put_nowait, None)

        self._timings.on_core_start()
        core = asyncio.ensure_future(asyncio.to_thread(run))
        marked = False  # the first sentence has been marked as the "first text" moment
        try:
            while (piece := await pieces.get()) is not None:
                text, snapshot = piece
                if snapshot is not None:
                    self._report(snapshot)
                if sentences := split_sentences(text):
                    if not marked:
                        self._timings.on_llm_first_token(self._clock())
                        marked = True
                    self._timings.on_answer_text(self._clock())
                    yield sentences
            spoken = await core  # raises what the core raised
            if first:  # nothing was handed over live (e.g. staff have the Call, or the reply
                # came back whole): report the turn, and speak whatever the core returned
                self._report(self._snapshot())
                for utterance in spoken:
                    if sentences := split_sentences(utterance):
                        if not marked:
                            self._timings.on_llm_first_token(self._clock())
                            marked = True
                        self._timings.on_answer_text(self._clock())
                        yield sentences
        finally:
            stopped = True
            self._timings.on_core_done()

    def _snapshot(self) -> "_Snapshot":
        profile = self._call.last_turn_profile
        return _Snapshot(
            self._call.caller_turn_seq,
            profile.llm_steps,
            profile.llm_ms,
            profile.tool_ms,
            list(profile.tools),
        )

    def _report(self, snapshot: "_Snapshot") -> None:
        self._timings.on_core_turn(snapshot.caller_turn)
        self._timings.on_core_profile(
            llm_steps=snapshot.llm_steps,
            llm_ms=snapshot.llm_ms,
            tool_ms=snapshot.tool_ms,
            tools=snapshot.tools,
        )

    @property
    def expired(self) -> bool:
        return self._call.expired

    def time_check(self) -> list[str]:
        return self._call.time_check()

    def asr_dropout(self) -> list[str]:
        return self._call.asr_dropout()

    def speech_failed(self) -> None:
        self._call.speech_failed()

    def end(self) -> None:
        self._call.end()


async def watch_call_limits(
    driver: VoiceTurnDriver,
    speak: Callable[[str], Awaitable[None]],
    close: Callable[[], Awaitable[None]],
    poll_seconds: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    stop: asyncio.Event | None = None,
) -> None:
    """Speak the duration cap's warning and close for a Caller who says nothing, then end the
    session. `speak` must return once the speech has been played out, so the close is heard
    before the session ends. Stops quietly if `stop` is set (the Call ended another way)."""
    while stop is None or not stop.is_set():
        for utterance in await asyncio.to_thread(driver.time_check):
            await speak(utterance)
        if driver.expired:
            await close()
            return
        await sleep(poll_seconds)
