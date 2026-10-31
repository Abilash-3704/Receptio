"""Store each per-turn timing line with the Call, next to the log line. Imports no LiveKit.

The latency fields are numbers only: nothing the Caller said ever passes through here."""

import json
import logging
from collections.abc import Callable

import httpx

logger = logging.getLogger("receptio.voice")

_FIELDS = (
    "asr_final_ms",
    "llm_first_token_ms",
    "tts_first_audio_ms",
    "total_ms",
    "total_from_speech_end_ms",
    "interrupted",
)


def storing(
    backend: httpx.Client, call_id: int, log: Callable[[str], None]
) -> Callable[[str], None]:
    """An `emit` for TurnTimings: log the line, then store it. A failed store is logged (the
    Call id only) and swallowed: a latency record is never worth dropping a live Call."""

    def emit(line: str) -> None:
        log(line)
        record = json.loads(line)
        body = {name: record[name] for name in _FIELDS} | {"caller_turn_seq": record["caller_turn"]}
        try:
            backend.post(f"/calls/{call_id}/turn-timings", json=body).raise_for_status()
        except Exception:  # noqa: BLE001 - best effort by design; see the docstring
            logger.warning("call %s: storing a turn timing failed", call_id)

    return emit
