# 05: Book an Appointment end to end in text mode

**What to build:** A Caller can book an Appointment in a text-mode Call. The call state machine keeps structured state outside the LLM context and gives the LLM a compact summary each turn. The agent collects the Service and the Patient's name and phone number (the Patient may differ from the Caller), checks real Availability, offers two or three times, places a Hold, reads back the details, and only on an explicit yes confirms. A Patient record is created on their first Confirmed Appointment, and a Confirmation Message is recorded as a mock. A minimal scenario runner and the first happy-path scenario assert on final database state.

**Blocked by:** 04: Confirm, cancel and atomic reschedule rules

**Status:** ready-for-agent

- [ ] A scripted text Call ends with a Confirmed Appointment in the database
- [ ] The agent never offers a time that Availability did not return
- [ ] Confirmation happens only after an explicit yes to the read-back
- [ ] A Patient is created only when their first Appointment is Confirmed
- [ ] A Confirmation Message record exists for each Confirmed Appointment
- [ ] The scenario runner asserts final state and a `must_not` rule for a confirm without read-back
