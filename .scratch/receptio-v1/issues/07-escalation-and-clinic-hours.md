# 07: Escalation: Handoff, Callback Request, Clinic Hours and retry caps

**What to build:** The agent can stop handling a Call and pass it to clinic staff. During Clinic Hours it starts a Handoff to a simulated staff line, and otherwise it records a Callback Request with the Caller's number and reason. Every Escalation records a reason. A demo-only control forces the clinic open or closed. Two failures on the same step trigger an Escalation, and a Caller can ask for a human at any time.

**Blocked by:** 05: Book an Appointment end to end in text mode

**Status:** resolved

- [x] A Handoff is offered only during Clinic Hours, a Callback Request otherwise
- [x] Every Escalation is stored with a reason
- [x] The open/closed override changes which path is taken without a restart
- [x] Two failures on the same step produce an Escalation
- [x] Asking for a human at any point produces an Escalation
- [x] Scenarios cover Handoff, Callback Request and the retry cap
