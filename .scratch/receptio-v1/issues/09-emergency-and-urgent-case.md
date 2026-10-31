# 09: Emergency and Urgent Case handling

**What to build:** A safety layer checks every Caller turn, even mid-scheduling. An Emergency interrupts the agent and plays the fixed 112 script, then starts an Escalation. An Urgent Case starts an Escalation for same-day staff triage with no 112 script. Neither enters scheduling, neither gets medical advice, and any in-progress Hold is released.

**Blocked by:** 07: Escalation: Handoff, Callback Request, Clinic Hours and retry caps

**Status:** ready-for-agent

- [ ] The Emergency script is fixed, versioned text and is the only thing said on that turn
- [ ] An Emergency mid-booking abandons the flow and releases the Hold
- [ ] An Urgent Case gets an Escalation and no 112 script, and no scheduling is attempted
- [ ] No medical advice is given in either case
- [ ] Scenarios assert Emergency recall of 100% and Urgent Case recall of at least 95%, with false positives tracked
