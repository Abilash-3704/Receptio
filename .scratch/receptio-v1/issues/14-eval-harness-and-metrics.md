# 14: Eval harness: simulated Caller, personas and metrics report

**What to build:** One command runs the scenario suite in text mode and prints a metrics report. An LLM-driven simulated Caller plays configurable personas (patient, rushed, confused, hostile). Scenarios assert final database state and `must_not` rules, not wording. The report shows completion rate, wrong-time rate, Emergency and Urgent Case recall, false positives, Escalation rate and reasons, latency percentiles and cost per Call.

**Blocked by:** 06: Times, corrections and the booking window; 08: Identify a Caller; reschedule and cancel in a Call; 09: Emergency and Urgent Case handling; 10: FAQ and scope limits; 11: Prompt-injection defences and server-side validation

**Status:** ready-for-agent

- [ ] One command runs the suite and outputs a metrics table
- [ ] Personas are configurable and change simulated Caller behaviour
- [ ] Scenarios assert database state and `must_not` rules
- [ ] The report includes every metric listed above
- [ ] Scripted turns remain available wherever a result must be exactly reproducible
