# 15: Scenario suite growth and CI regression gate

**What to build:** The suite grows to 50 to 100 scenarios covering happy paths, interruptions, corrections, no Availability, a time taken during a Hold, angry and off-topic Callers, Emergencies, injection attempts and silence or dropped Calls. A stored baseline lets CI run the text-mode suite on every prompt or model change and fail on regression. Two to three improvement cycles are documented with before and after numbers, and each prompt or model change is logged with its evaluation result.

**Blocked by:** 14: Eval harness: simulated Caller, personas and metrics report

**Status:** ready-for-agent

- [ ] The suite contains at least 50 scenarios across all intents and failure types
- [ ] CI fails when a change regresses any tracked metric against the baseline
- [ ] Wrong-time rate is 0% and Emergency recall is 100% across the suite
- [ ] A changelog ties each prompt or model change to an evaluation result
- [ ] An evaluation report documents at least two improvement cycles with before and after numbers
