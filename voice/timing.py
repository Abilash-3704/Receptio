"""Per-turn timing record for the voice loop. Pure logic: no LiveKit, no clock of its own.

The voice pipeline reports four moments per Caller turn, as seconds on one clock:
end of Caller speech (voice activity stopped), speech-recognition final, LLM first output, and the
first agent audio. One structured JSON line is emitted per turn, with every stage as a cumulative
number of milliseconds since the end of Caller speech. `total_ms` is the last stage, i.e. end of
speech to first agent audio (the metric in docs/metrics.md). `since_asr_final_ms` is the same
span measured from the recognition final, the other reading of that metric.
"""

import json
from collections.abc import Callable


def _ms(start: float, end: float | None) -> int | None:
    return None if end is None else round((end - start) * 1000)


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
        self._pending = False

    def on_speech_end(self, t: float) -> None:
        self._abandon()  # an earlier turn still waiting for audio never got one
        self._speech_end = t
        self._pending = True

    def on_asr_final(self, t: float) -> None:
        if self._llm_first is None:  # a later final belongs to the next turn
            self._asr_final = t
            self._pending = True

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
        asr = self._asr_final
        origin = self._speech_end if self._speech_end is not None else asr
        assert origin is not None
        self._turn += 1
        record = {
            "event": "turn_timing",
            "call_id": self._call_id,
            "turn": self._turn,
            "asr_final_ms": _ms(origin, asr),
            "llm_first_token_ms": _ms(origin, self._llm_first),
            "tts_first_audio_ms": _ms(origin, audio),
            "total_ms": _ms(origin, audio),
            "since_asr_final_ms": None if asr is None or audio is None else _ms(asr, audio),
            "interrupted": interrupted,
        }
        self._reset()
        self._emit(json.dumps(record))
