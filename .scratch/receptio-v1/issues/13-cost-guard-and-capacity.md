# 13: Cost guard and capacity limits

**What to build:** Spend and abuse are bounded in code. A Call lasts at most 5 minutes, with a warning at 4 minutes 30 seconds that offers a Callback Request. Each visitor (browser plus IP) gets 3 Calls per day, and a global cap of 120 minutes per day applies. When a cap is hit the web client disables starting a Call and shows a clear message. A test-mode override lifts the caps and is off by default. A Caller who objects to recording is offered a Callback Request instead of a Call.

**Blocked by:** 07: Escalation: Handoff, Callback Request, Clinic Hours and retry caps; 12: Web client voice loop with latency instrumentation

**Status:** ready-for-agent

- [ ] A Call cannot exceed 5 minutes and is warned at 4:30, never cut off silently
- [ ] The per-visitor and global caps block new Calls with a visible message
- [ ] All cap values are configuration
- [ ] The test-mode override is off by default and cannot be enabled in the deployed demo
- [ ] An objection to recording produces a Callback Request and no recorded Call
