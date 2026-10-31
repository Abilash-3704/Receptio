# 16: Voice-mode scenarios and latency report

**What to build:** A small subset of 10 to 15 scenarios runs in voice mode with a speech-synthesis Caller and optional background noise. Because voice minutes cost money, only audio-specific behaviour is covered: barge-in, per-turn latency and the duration warning. The result is a latency budget document with p50 and p95 against the targets, produced as a report and not as pass/fail logic.

**Blocked by:** 12: Web client voice loop with latency instrumentation; 14: Eval harness: simulated Caller, personas and metrics report

**Status:** ready-for-agent

- [ ] 10 to 15 scenarios run in voice mode from one command
- [ ] Barge-in and the duration warning are exercised by audio scenarios
- [ ] A latency budget document reports p50 and p95 per stage and total
- [ ] Cost of the voice run is reported
