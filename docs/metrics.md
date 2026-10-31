# Target metrics

From the spec. Measured by the eval harness on the scenario suite unless noted. Recall is always reported next to its false-positive rate so over-flagging cannot inflate it.

| Metric | Target | Notes |
|---|---|---|
| Task completion rate | >= 90% | Scenario suite, text mode |
| Wrong-time or overlapping-Appointment rate | 0% | Whole suite, after every prompt or model change. Also proven deterministically at the backend API seam |
| Emergency recall | 100% | Emergency scenarios |
| Urgent Case recall | >= 95% | Urgent Case scenarios |
| Emergency and Urgent Case false-positive rate | Tracked | Reported alongside recall |
| End-of-speech to start-of-agent-speech latency | p50 < 800 ms, p95 < 1500 ms | Voice pipeline. Measure first, then tune |
| Cost per average 3-minute Call | Tracked and reported | Provider cost per Call, per minute |
| Escalation rate and reasons | Tracked and reported | By Handoff versus Callback Request and by reason |

## Definitions

- **Task completion:** the scenario's expected final database state is reached and no `must_not` rule fired.
- **Wrong-time:** a Confirmed Appointment whose Provider, Service, date or time differs from what the Caller agreed in the read-back, or one that overlaps another Confirmed Appointment of the same Patient.
- **Emergency recall:** share of Emergency scenarios where the fixed script was delivered, an Escalation was made, no scheduling was attempted and any Hold was released.
- **Urgent Case recall:** same, without the 112 script.
- **Latency:** time from the end of Caller speech, as detected by voice-activity detection, to the first audio of the agent's reply (`total_from_speech_end_ms` in the turn log). This is what the Caller experiences and what the spec targets. The ASR-final-to-first-audio figure (`total_ms`) is logged as a diagnostic only, because the speech recogniser's final usually arrives before the VAD closes, which makes it read lower than the Caller's real wait.

## Measured results

### Voice latency, first browser run (2026-10-31): target NOT met

Two real Calls from a browser (Deepgram nova-3, Claude via `claude-sonnet-5-5`, Cartesia sonic-3, local LiveKit), 21 completed turns, 19 with a full timing, 7 interrupted by the Caller before any agent audio (barge-in). Provider prices and the machine are a developer laptop, so treat the absolute numbers as indicative.

| stage | n | p50 ms | p95 ms |
|---|---|---|---|
| asr_final_ms | 18 | -149 | 240 |
| llm_first_token_ms | 19 | 3122 | 16424 |
| tts_first_audio_ms | 19 | 155 | 340 |
| total_from_speech_end_ms | 19 | 3190 | 16797 |

Against the target (p50 < 800 ms, p95 < 1500 ms) this fails by a wide margin. Speech recognition and speech synthesis are fine. All of the time is in the agent core's model step: its fastest turn was 1.3 s, so the target is out of reach without changing how a turn is answered, not by tuning. Slow turns (8 to 16 s) are suspected to be turns with several model and tool round trips; the log did not say, so `voice/timing.py` now records `llm_steps`, `llm_ms`, `tool_ms` and `tools` per turn to confirm it.

Known causes in the code: the model is called without streaming, the core waits for the whole step before speaking (its time and price guards need the full text), and every tool call is another full model round trip. `LLM_MODEL_FAST` is configured but not used.
