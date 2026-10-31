# 19: Failure injection and graceful degradation

**What to build:** The system behaves sensibly when parts fail. Injected failures (slow or failing backend, LLM error, speech-recognition dropout, speech-synthesis failure, mid-Call disconnect) are each handled with an apology, one retry, and an offer of a Callback Request or Handoff. A mid-Call disconnect leaves no stuck Hold, and a follow-up Callback Request is recorded for dropped Calls.

**Blocked by:** 07: Escalation: Handoff, Callback Request, Clinic Hours and retry caps; 12: Web client voice loop with latency instrumentation

**Status:** ready-for-agent

- [ ] Each injected failure produces an apology, one retry and an Escalation offer
- [ ] A disconnect never leaves a Hold blocking Availability beyond its expiry
- [ ] A dropped Call records a Callback Request
- [ ] A failure-injection checklist documents each case and its outcome
