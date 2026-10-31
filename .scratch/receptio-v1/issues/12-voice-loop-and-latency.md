# 12: Web client voice loop with latency instrumentation

**What to build:** A Caller opens a web page, starts a Call over voice, and talks to the agent using the same agent core as text mode. Speech starts on the first sentence, and the agent stops speaking at once when interrupted. The AI and recording disclosure is spoken first. Every turn logs speech-recognition final, LLM first token, speech-synthesis first audio and total response latency, and a script summarises p50 and p95.

**Blocked by:** 02: Project skeleton and a tracer text-mode Call

**Status:** ready-for-agent

- [ ] A browser Call works end to end over voice
- [ ] Barge-in stops the agent immediately
- [ ] The disclosure is the first thing spoken
- [x] Per-turn timings are logged from the first working voice loop
- [ ] A script reports p50 and p95 latency over at least 20 turns

## Implementation notes (agent run, 2026-10-31)

Verified without a human: the self-hosted LiveKit server (v1.13.11) starts; the worker registers and joins a room; the token endpoint mints a room-scoped token; a synthetic Caller (`scripts/voice_smoke.py`) hears audio first, Deepgram transcribes its speech, the agent core answers and Cartesia audio returns; per-turn JSON timing lines were written from that run (the ticked box). Timing and summary logic are unit tested on synthetic logs.

Left unticked, needs a human with a mic and a browser (README "Voice mode", manual checklist):

- A browser Call works end to end over voice: the page was never opened in a browser.
- Barge-in stops the agent immediately: configured (LiveKit VAD interruption), never exercised by a human or timed.
- The disclosure is the first thing spoken: it is the first audio a synthetic client received and the first stored turn, but nobody listened to it.
- A script reports p50 and p95 latency over at least 20 turns: the script exists and is tested; only 2 real turns were measured (see `docs/latency-budget.md`), so no p50/p95 claim exists.
