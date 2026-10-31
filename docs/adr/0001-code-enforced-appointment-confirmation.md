# Appointments are confirmed only through a code-enforced path, never by the LLM

An LLM can misread a vague "uh-huh" or double-book on a retry, and the project's core promise is zero wrong-slot and double-booking errors. So an Appointment becomes Confirmed only when the backend sees an unexpired Hold and a recorded read-back "yes" for that exact Hold. The call state machine, which lives outside the LLM context, records the "yes", and writes carry idempotency keys so retries cannot create a second Appointment.

## Considered Options

- **Fixed grid of identical slots, LLM calls a single `book` tool.** Simpler, but it cannot model Services of different lengths and leaves correctness to the prompt. Rejected.
- **Duration-based Availability with Holds (chosen).** Availability is computed from Provider working hours minus Appointments and unexpired Holds, fitted to the Service's duration. A Hold lasts 5 minutes.

## Consequences

- A reschedule never edits an Appointment. It Holds the new time, and on confirmation the old Appointment is Cancelled in the same transaction, so a failure leaves the Caller's original Appointment intact.
- An expired Hold forces Availability to be re-checked before the Appointment can be Confirmed.
- A Patient may have several Confirmed Appointments, but never two that overlap in time.
