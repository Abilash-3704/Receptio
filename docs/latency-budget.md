# Voice latency: what was measured

Target (`docs/metrics.md`): p50 < 800 ms, p95 < 1500 ms from the end of Caller speech to the first agent audio. **No p50 or p95 over the required 20+ turns has been measured yet.** That run needs a human with a microphone and a browser; the steps are in `README.md`, "Voice mode". Do not quote p50/p95 from this page until that run's numbers replace the placeholder at the bottom.

## What the log records

One JSON line per Caller turn (`voice/timing.py`), written to `TURN_LOG_PATH` (default `logs/voice-turns.jsonl`) and to the worker's stdout from the first working loop. Summarise with `scripts/latency_summary logs/voice-turns.jsonl --min-turns 20` (nearest-rank p50/p95; exits 1 below the minimum; says "too few" under 20 completed turns).

| Field | Meaning |
|---|---|
| `asr_final_ms` | speech-recognition final minus the VAD's end of speech. Negative when Deepgram finalised first (the usual order) |
| `llm_first_token_ms` | ASR final to the first agent text. The agent core answers a whole LLM step at once (its guards must see the full text before the Caller hears any of it), so this is the full LLM step including tool round trips, not a token-stream time-to-first-token |
| `tts_first_audio_ms` | first agent text to the first agent audio frame out of Cartesia |
| `total_ms` | ASR final to first agent audio (the `docs/metrics.md` definition) |
| `total_from_speech_end_ms` | VAD end of speech to first agent audio. Stricter; includes the VAD silence window (0.55 s default) and the endpointing delay |
| `interrupted` | the turn got no agent audio (Caller spoke again, or the Call ended, or the agent had nothing to say after an Escalation) |

Both `total` readings are reported because the VAD only declares end of speech after its silence window, so the real end of speech is earlier than either clock origin and the Caller's perceived latency is longer than `total_ms`.

## Synthetic-participant smoke run (not a latency result)

Conditions: 2026-10-31, one developer laptop (macOS, Apple silicon), self-hosted LiveKit server 1.13.11 in a podman container, voice worker running locally, real Deepgram nova-3, Claude `claude-sonnet-5-5` through the Anthropic SDK (non-streaming, tool loop), Cartesia sonic-3. The "Caller" was `scripts/voice_smoke.py`: Cartesia-generated speech published as a microphone track. Network from a home or office connection, no load.

**Sample size: 2 completed turns. This is not a distribution and says nothing about p50 or p95.** Raw turn lines:

| Turn | `llm_first_token_ms` | `tts_first_audio_ms` | `total_ms` | `total_from_speech_end_ms` |
|---|---|---|---|---|
| "What are your opening hours?" (LLM calls `search_faq`, then answers) | 3451 | 157 | 3608 | 3404 |
| Emergency sentence (safety detector in code, no LLM) | 261 | 154 | 415 | 213 |

From the script's own clock (end of the last published audio to first audible agent audio, 100 ms audio queue): 4376 ms and 1226 ms. Those include the VAD silence window and the endpointing delay (`MIN_ENDPOINTING_DELAY`, 0.5 s) that the worker's `total_ms` excludes.

Cartesia time to first audio for 12 to 41 character phrases, measured by the script in isolation: 122 to 259 ms (the 4 values I saw printed: 122, 204, 214, 259).

What this does show: TTS first audio is about 150 ms after text; the code-only Emergency path is well inside the budget from the ASR final; a normal turn that needs a tool call costs two sequential non-streaming Claude requests and is the dominant cost (about 3.4 s here). Expect the 800 ms p50 target to be missed on tool turns until the LLM step is cut down (streamed or faster model for simple turns via `LLM_MODEL_FAST`, fewer round trips, a shorter endpointing delay). Measure first, then tune; no tuning has been done.

## Human run (to fill in)

Not done. Placeholder for: date, who, machine and network, browser, number of completed turns (at least 20), the `scripts/latency_summary` output pasted verbatim, and the settings used (`MIN_ENDPOINTING_DELAY`, `LLM_MODEL`).
