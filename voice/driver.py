"""One Caller turn through the agent core, for the voice pipeline. Imports no LiveKit.

The pipeline hands over the recognised text; the driver runs the same `TextCall.hear` as text
mode (safety detector first, code-recorded read-back yes, Escalation rules, time and price
guards) and yields the reply sentence by sentence so speech synthesis can start on the first.

The agent core answers a whole step at a time, because its guards must see the full text before
the Caller hears any of it (a time Availability did not return is withheld, not spoken). So the
LLM's reply is complete before the first sentence is yielded, and the "LLM first token" moment
the timing record logs is when the first sentence of the reply became available."""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable

from agent.text_mode import TextCall
from voice.timing import TurnTimings

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text.strip()) if s]


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
        # The core is synchronous (blocking LLM and backend calls): keep the event loop free so
        # audio keeps flowing and the Caller can still barge in.
        spoken = await asyncio.to_thread(self._call.hear, caller_text)
        first = True
        for utterance in spoken:
            for sentence in split_sentences(utterance):
                if first:
                    self._timings.on_llm_first_token(self._clock())
                    first = False
                yield sentence

    def end(self) -> None:
        self._call.end()
