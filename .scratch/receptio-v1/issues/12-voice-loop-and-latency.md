# 12: Web client voice loop with latency instrumentation

**What to build:** A Caller opens a web page, starts a Call over voice, and talks to the agent using the same agent core as text mode. Speech starts on the first sentence, and the agent stops speaking at once when interrupted. The AI and recording disclosure is spoken first. Every turn logs speech-recognition final, LLM first token, speech-synthesis first audio and total response latency, and a script summarises p50 and p95.

**Blocked by:** 02: Project skeleton and a tracer text-mode Call

**Status:** ready-for-agent

- [ ] A browser Call works end to end over voice
- [ ] Barge-in stops the agent immediately
- [ ] The disclosure is the first thing spoken
- [ ] Per-turn timings are logged from the first working voice loop
- [ ] A script reports p50 and p95 latency over at least 20 turns
