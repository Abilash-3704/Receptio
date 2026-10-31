# 17: Call persistence and trace

**What to build:** Every Call is stored: transcript, turns, tool calls, outcome, latency and cost, so that any Call can be fully reconstructed afterwards (speech-recognition text, LLM calls, tool calls, synthesis timing and cost). Personal data is stored in full only in the backend database.

**Blocked by:** 05: Book an Appointment end to end in text mode; 12: Web client voice loop with latency instrumentation

**Status:** ready-for-agent

- [ ] Each Call, turn and tool call is persisted with its outcome
- [ ] Latency and cost are stored per turn and per Call
- [ ] A Call can be reconstructed end to end from stored data
- [ ] Escalation reasons are stored with the Call
