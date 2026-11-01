"""What the voice layer does when its own parts fail (ticket 19). Imports no LiveKit: the worker
passes in `speak` and `close`, and tests pass stand-ins (voice/failure_injection.py).

- Speech-recognition dropout: the Caller spoke but nothing was recognised (`AsrDropoutWatch`
  notices, or the pipeline reports an unrecoverable recogniser error). The agent core answers:
  first an apology and a request to say it again, then an apology and an offer of staff.
- Speech-synthesis failure: the reply could not be spoken. One retry with a short apology; if that
  cannot be spoken either, the Caller cannot be told anything, so a Callback Request is recorded
  and the Call is closed.
- Mid-Call disconnect is the Call ending (`VoiceTurnDriver.end`): see TextCall.end.

None of this has run against real LiveKit, Deepgram or Cartesia. Where the real providers report
these failures is documented in docs/failure-analysis.md, with what is still to verify by hand."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

logger = logging.getLogger("receptio.voice")

TTS_APOLOGY = "I'm sorry, I'm having trouble with my voice. Could you say that again?"

# Speech shorter than this with no text is treated as noise (a cough, a bump), not a dropout.
MIN_SPEECH_SECONDS = 1.0
# How long after the Caller stops speaking the recogniser may take to produce text.
ASR_GRACE_SECONDS = 4.0


class FaultyCall(Protocol):
    """The part of the voice driver that failure handling needs."""

    def asr_dropout(self) -> list[str]: ...

    def speech_failed(self) -> None: ...


class AsrDropoutWatch:
    """Notices a Caller who spoke and got no text back. Fed with the same moments the timing
    record uses; `due(now)` is true once per such utterance, after the grace period."""

    def __init__(
        self,
        min_speech_seconds: float = MIN_SPEECH_SECONDS,
        grace_seconds: float = ASR_GRACE_SECONDS,
    ) -> None:
        self._min = min_speech_seconds
        self._grace = grace_seconds
        self._speech_start: float | None = None
        self._waiting_since: float | None = None
        self._agent_speaking = False

    def on_agent_speaking(self, speaking: bool) -> None:
        """While the agent talks, whatever the voice detector hears is most likely its own voice
        coming back through the Caller's speakers, not a Caller whose words were lost. Drop any
        speech in progress and any dropout still pending: the agent is replying, so nothing was
        missed. A real interruption is handled by barge-in, not by this watch."""
        self._agent_speaking = speaking
        if speaking:
            self._speech_start = None
            self._waiting_since = None

    def on_speech_start(self, now: float) -> None:
        if self._agent_speaking:
            return
        self._speech_start = now
        self._waiting_since = None

    def on_speech_end(self, now: float) -> None:
        started, self._speech_start = self._speech_start, None
        if started is not None and now - started >= self._min:
            self._waiting_since = now

    def on_text(self) -> None:
        """The recogniser produced a final transcript: nothing was lost."""
        self._waiting_since = None

    def due(self, now: float) -> bool:
        if self._waiting_since is not None and now - self._waiting_since >= self._grace:
            self._waiting_since = None
            return True
        return False


class VoiceResilience:
    def __init__(
        self,
        call: FaultyCall,
        speak: Callable[[str], Awaitable[None]],
        close: Callable[[], Awaitable[None]],
    ) -> None:
        self._call = call
        self._speak = speak
        self._close = close
        self._lost = False
        self._tts_reported = False

    @property
    def voice_lost(self) -> bool:
        return self._lost

    async def on_asr_dropout(self) -> None:
        """Say what the agent core says for a dropout. Spoken like any reply."""
        for utterance in await asyncio.to_thread(self._call.asr_dropout):
            await self.speak_reliably(utterance)

    async def on_tts_failure(self) -> None:
        """A reply could not be spoken: apologise (that is the one retry). A second report means
        the voice is not coming back: give up rather than apologise in a loop."""
        if self._tts_reported:
            await self._give_up()
            return
        self._tts_reported = True
        await self.speak_reliably(TTS_APOLOGY)

    async def speak_reliably(self, text: str) -> bool:
        """Speak, and once more if that fails. If it fails twice the Caller cannot be reached by
        voice: record a Callback Request and end the Call. Returns whether it was spoken."""
        if self._lost:
            return False
        for attempt in (1, 2):
            try:
                await self._speak(text)
            except Exception:  # noqa: BLE001 - any synthesis or playout failure counts
                logger.warning("speech synthesis failed (attempt %d)", attempt)
                continue
            return True
        await self._give_up()
        return False

    async def _give_up(self) -> None:
        if self._lost:
            return
        self._lost = True
        await asyncio.to_thread(self._call.speech_failed)
        await self._close()
