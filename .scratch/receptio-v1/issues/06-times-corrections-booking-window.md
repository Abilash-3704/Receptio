# 06: Times, corrections and the booking window

**What to build:** The agent copes with how Callers actually speak. Relative and vague times ("next Tuesday", "after lunch") are interpreted in the clinic's timezone. A mid-flow correction ("actually Thursday") releases the old Hold and starts a new one. An optional Provider preference is honoured. Appointments are only offered from 2 hours ahead to 60 days ahead and within Clinic Hours, a request sooner than 2 hours is offered a Callback Request, and no-Availability is explained with alternatives.

**Blocked by:** 05: Book an Appointment end to end in text mode

**Status:** ready-for-agent

- [x] Relative and ambiguous times resolve correctly in `Asia/Kolkata`, including across a day boundary
- [x] A correction releases the previous Hold before a new one is placed
- [x] A preferred Provider is used when available, and the agent does not force a choice otherwise
- [x] Times outside the booking window or Clinic Hours are never offered
- [ ] A request sooner than 2 hours is offered a Callback Request (not ticked: the request is refused and flagged `offer_callback_request` in the tool result, with a marked SEAM in `agent/availability_search.py`; the actual Callback Request needs ticket 07)
- [x] Scenarios cover correction, ambiguous time, out-of-window and no-Availability cases
