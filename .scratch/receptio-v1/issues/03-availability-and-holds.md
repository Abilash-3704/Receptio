# 03: Availability and Holds in the clinic backend

**What to build:** The clinic backend knows Providers, Services (with durations), Clinic Hours and working hours from seed data. Given a Service, optional Provider and a time range, it returns real Availability fitted to the Service's duration, minus existing Appointments and unexpired Holds. A Hold reserves a time for 5 minutes and expires on its own.

**Blocked by:** 02: Project skeleton and a tracer text-mode Call

**Status:** resolved

- [x] Seed data covers a few Providers, Services of different lengths and Clinic Hours
- [x] Availability respects Service duration, working hours, Appointments and Holds
- [x] A Hold removes that time from Availability for everyone else
- [x] A Hold expires after 5 minutes and the time becomes available again
- [x] Deterministic tests cover these behaviours with no LLM involved
