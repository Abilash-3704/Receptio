# 04: Confirm, cancel and atomic reschedule rules

**What to build:** The backend alone guarantees Appointment correctness (ADR-0001). An Appointment becomes Confirmed only with an unexpired Hold and a recorded read-back agreement for that exact Hold. Writes carry idempotency keys, so a retry returns the same result. A Patient never has two overlapping Confirmed Appointments. Cancel works at any time. Reschedule places a Hold on the new time and, on confirmation, cancels the old Appointment in the same transaction, leaving the original intact on any failure.

**Blocked by:** 03: Availability and Holds in the clinic backend

**Status:** resolved

- [x] Confirm is rejected without a recorded read-back or with an expired Hold
- [x] A retried confirm with the same idempotency key never creates a second Appointment
- [x] Concurrent confirms for the same time produce exactly one Appointment
- [x] Overlapping Appointments for one Patient are rejected regardless of Provider
- [x] A failed reschedule leaves the original Appointment Confirmed
- [x] Deterministic and concurrency tests prove all of the above with no LLM involved
