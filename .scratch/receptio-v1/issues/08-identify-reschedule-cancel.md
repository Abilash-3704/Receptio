# 08: Identify a Caller; reschedule and cancel in a Call

**What to build:** A Caller can find, move or cancel an existing Appointment in a text-mode Call. The agent reveals nothing until the Patient's full name and phone number both match a record. Two failed attempts lead to a Callback Request. When a Patient has several Appointments the agent asks which one. Reschedule and cancel both read back what will change and need an explicit yes.

**Blocked by:** 04: Confirm, cancel and atomic reschedule rules; 07: Escalation: Handoff, Callback Request, Clinic Hours and retry caps

**Status:** ready-for-agent

- [ ] No Appointment detail is spoken before name and phone both match
- [ ] Two failed identifications produce a Callback Request
- [ ] The agent asks which Appointment when more than one matches
- [ ] A reschedule keeps the original Appointment if the new time falls through
- [ ] A cancel is read back and confirmed before it happens
- [ ] Scenarios cover successful and failed identification, reschedule and cancel
