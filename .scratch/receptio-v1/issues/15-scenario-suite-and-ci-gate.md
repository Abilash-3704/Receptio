# 15: Scenario suite growth and CI regression gate

**What to build:** The suite grows to 50 to 100 scenarios covering happy paths, interruptions, corrections, no Availability, a time taken during a Hold, angry and off-topic Callers, Emergencies, injection attempts and silence or dropped Calls. A stored baseline lets CI run the text-mode suite on every prompt or model change and fail on regression. Two to three improvement cycles are documented with before and after numbers, and each prompt or model change is logged with its evaluation result.

**Blocked by:** 14: Eval harness: simulated Caller, personas and metrics report

**Status:** ready-for-agent

- [x] The suite contains at least 50 scenarios across all intents and failure types (114: 101 core, 13 known-gap probes; `docs/eval-report.md`)
- [x] CI fails when a change regresses any tracked metric against the baseline (`scripted-evals` job; the exit-1 behaviour is proven by `tests/test_scripted_gate.py`, the job itself has not yet run on GitHub)
- [x] Wrong-time rate is 0% and Emergency recall is 100% across the suite (scripted run only; no real-model run exists)
- [ ] A changelog ties each prompt or model change to an evaluation result (`CHANGELOG.md` exists with every change so far, but each entry honestly says "not evaluated": no real-model run has been made)
- [ ] An evaluation report documents at least two improvement cycles with before and after numbers (not done: needs a real-model run, see `docs/eval-report.md`)
