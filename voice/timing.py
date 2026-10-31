"""Per-turn timing record for the voice loop. Pure logic: no LiveKit, no clock of its own.

The voice pipeline reports four moments per Caller turn, as seconds on one clock: the voice
activity detector's end of Caller speech, the speech-recognition final, the first agent text
(from the agent core), and the first agent audio. One structured JSON line is emitted per turn:

- `asr_final_ms`: ASR final minus the VAD end of speech. Negative when the recogniser finalised
  before the VAD's silence window closed, which is the usual order.
- `llm_first_token_ms`: ASR final to the first agent text (the core answers a whole step at once).
- `tts_first_audio_ms`: first agent text to first agent audio.
- `total_ms`: ASR final to first agent audio, the latency definition in docs/metrics.md. It is
  `llm_first_token_ms + tts_first_audio_ms`.
- `total_from_speech_end_ms`: VAD end of speech to first agent audio, a stricter reading that
  includes the wait for the VAD to decide the Caller stopped (itself later than the real end).

`caller_turn` is the stored transcript position of the Caller turn, null if the core never took it.
A field is null where a moment was not seen, e.g. a turn cut off before any agent audio.
"""

import json
from collections.abc import Callable


def _ms(start: float, end: float) -> int:
    return round((end - start) * 1000)


class TurnTimings:
    def __init__(self, call_id: int, emit: Callable[[str], None]) -> None:
        self._call_id = call_id
        self._emit = emit
        self._turn = 0
        self._reset()

    def _reset(self) -> None:
        self._speech_end: float | None = None
        self._asr_final: float | None = None
        self._llm_first: float | None = None
        self._core_turn: int | None = None
        self._pending = False

    def on_speech_end(self, t: float) -> None:
        if self._pending and self._speech_end is None:
            self._speech_end = t  # the VAD closed after the recogniser's final: same turn
            return
        self._abandon()  # an earlier turn still waiting for audio never got one
        self._speech_end = t
        self._pending = True

    def on_asr_final(self, t: float) -> None:
        if self._llm_first is None:  # a later final belongs to the next turn
            self._asr_final = t
            self._pending = True

    def on_core_turn(self, caller_turn_seq: int) -> None:
        """The agent core took this Caller turn: its position in the stored transcript."""
        self._core_turn = caller_turn_seq

    def on_llm_first_token(self, t: float) -> None:
        if self._pending and self._llm_first is None:
            self._llm_first = t

    def on_tts_first_audio(self, t: float) -> None:
        if self._pending:
            self._flush(t, interrupted=False)

    def on_user_speech_start(self, t: float) -> None:  # noqa: ARG002
        """The Caller spoke before the agent made a sound: that turn never got a reply."""
        self._abandon()

    def close(self) -> None:
        self._abandon()

    def _abandon(self) -> None:
        if self._pending and self._asr_final is not None:
            self._flush(None, interrupted=True)
        else:
            self._reset()

    def _flush(self, audio: float | None, interrupted: bool) -> None:
        asr, llm, vad = self._asr_final, self._llm_first, self._speech_end
        origin = asr if asr is not None else vad
        assert origin is not None
        self._turn += 1
        record = {
            "event": "turn_timing",
            "call_id": self._call_id,
            "turn": self._turn,
            "caller_turn": self._core_turn,
            "asr_final_ms": None if asr is None or vad is None else _ms(vad, asr),
            "llm_first_token_ms": None if llm is None else _ms(origin, llm),
            "tts_first_audio_ms": None if llm is None or audio is None else _ms(llm, audio),
            "total_ms": None if audio is None else _ms(origin, audio),
            "total_from_speech_end_ms": None if vad is None or audio is None else _ms(vad, audio),
            "interrupted": interrupted,
        }
        self._reset()
        self._emit(json.dumps(record))
