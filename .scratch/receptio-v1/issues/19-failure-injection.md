# 19: Failure injection and graceful degradation

**What to build:** The system behaves sensibly when parts fail. Injected failures (slow or failing backend, LLM error, speech-recognition dropout, speech-synthesis failure, mid-Call disconnect) are each handled with an apology, one retry, and an offer of a Callback Request or Handoff. A mid-Call disconnect leaves no stuck Hold, and a follow-up Callback Request is recorded for dropped Calls.

**Blocked by:** 07: Escalation: Handoff, Callback Request, Clinic Hours and retry caps; 12: Web client voice loop with latency instrumentation

**Status:** ready-for-agent

- [ ] Each injected failure produces an apology, one retry and an Escalation offer
- [ ] A disconnect never leaves a Hold blocking Availability beyond its expiry
- [ ] A dropped Call records a Callback Request
- [x] A failure-injection checklist documents each case and its outcome (`docs/failure-analysis.md`)

## Notes (implementation)

- Left unticked on purpose: the first three boxes are verified for the backend, the LLM, speech recognition, speech synthesis and disconnect handling with stand-ins (no provider, no LiveKit), but not against real LiveKit, Deepgram or Cartesia. What to check by hand is listed, unticked, in `docs/failure-analysis.md`.
- Seams: `agent/failure_injection.py` (`FlakyBackend`, `FailingLLM`), `voice/failure_injection.py`, `voice/resilience.py` (`VoiceResilience`, `AsrDropoutWatch`).
- Policy decisions made here for the human to confirm: a dropped Call is followed up with a Callback Request only when the Caller gave a number in that Call (otherwise nobody to ring); the follow-up is recorded after the Call ends so the outcome stays `dropped`; the scenario `dropped_call_after_readback_015` now expects a `callback_request`.
