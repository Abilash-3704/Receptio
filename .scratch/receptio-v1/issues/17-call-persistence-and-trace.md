# 17: Call persistence and trace

**What to build:** Every Call is stored: transcript, turns, tool calls, outcome, latency and cost, so that any Call can be fully reconstructed afterwards (speech-recognition text, LLM calls, tool calls, synthesis timing and cost). Personal data is stored in full only in the backend database.

**Blocked by:** 05: Book an Appointment end to end in text mode; 12: Web client voice loop with latency instrumentation

**Status:** ready-for-agent

- [x] Each Call, turn and tool call is persisted with its outcome
- [x] Latency and cost are stored per turn and per Call
- [x] A Call can be reconstructed end to end from stored data
- [x] Escalation reasons are stored with the Call

## Notes (implementation)

- Verified: full suite on real Postgres (513 passed, 9 xfailed), ruff, pyright. Not verified: any real provider call or real voice Call; cost figures were never compared with an invoice.
- Dropped-call follow-up record (PROJECT.md Phase 5) is NOT done: it needs a number to ring and a policy on contacting someone who hung up. A dropped Call stores its outcome and reason; staff can query those.
- For ticket 18: `GET /calls/{id}` still returns transcripts unauthenticated; the new `POST /calls/{id}/tool-calls|llm-calls|turn-timings` are unauthenticated writes; trace data (names, numbers in tool args) is read only through `backend.app.trace.load_trace`, no HTTP endpoint.
- Migration `0005` replaces `calls.transcript` (JSON) with `call_turns`; old Calls are copied across and keep no outcome.
