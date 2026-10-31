# Receptio

An AI voice receptionist for a single fictional dental clinic in Bengaluru. Callers reach it through a web client, never a real phone number.

## Language

### People

**Caller**:
The person speaking on a Call. May or may not be the Patient.
_Avoid_: User, customer, client

**Patient**:
The person an Appointment is for. Can differ from the Caller.
_Avoid_: Customer, client

**Provider**:
A dentist or hygienist who performs Services and whose working hours bound availability.
_Avoid_: Doctor, dentist, practitioner, staff

### Scheduling

**Appointment**:
A Patient's scheduled visit with a Provider for a Service at a specific time. Is Held, then Confirmed, then possibly Cancelled.
_Avoid_: Booking, reservation, slot

**Hold**:
A temporary reservation of Availability for a Held Appointment while the Caller hears the read-back. Expires if the Appointment is not Confirmed in time.
_Avoid_: Lock, reservation, pending booking

**Confirmed**:
The state of an Appointment after the Caller has explicitly agreed to the read-back and the clinic has accepted it. Only this state counts as a real Appointment.
_Avoid_: Booked, committed, finalised

**Service**:
A type of visit the clinic offers, with a fixed duration (for example a cleaning or a filling).
_Avoid_: Treatment, procedure, appointment type

**Availability**:
The open times when a given Provider can perform a given Service, fitted to the Service's duration.
_Avoid_: Free slots, openings

### Calls and escalation

**Call**:
A voice session between a Caller and the agent, started from the web client.
_Avoid_: Session, conversation, chat

**Escalation**:
The agent ending its own handling of a Call by passing it to clinic staff, either as a Handoff or a Callback Request. Always carries a reason.
_Avoid_: Transfer, fallback

**Handoff**:
A live Escalation where the Caller is connected to clinic staff. In the demo, staff are simulated.
_Avoid_: Transfer, warm transfer

**Callback Request**:
An Escalation that records the Caller's number and reason so staff can ring back later. The only Escalation available outside clinic hours.
_Avoid_: Voicemail, message, ticket

**Emergency**:
A life-threatening situation described by the Caller, such as trouble breathing, uncontrolled bleeding or severe facial trauma. Gets a fixed safety script telling the Caller to call 112, then an Escalation. Never enters scheduling.
_Avoid_: Crisis, urgent case

**Urgent Case**:
A dental problem needing same-day attention but not life-threatening, such as severe pain or a knocked-out tooth. Gets an immediate Escalation for staff triage. Never enters scheduling and never gets medical advice.
_Avoid_: Emergency, urgent appointment

**Confirmation Message**:
A record of the message "sent" to a Patient's stated phone number when an Appointment is Confirmed. Mocked; shown on screen and in the dashboard rather than delivered by a real SMS provider.
_Avoid_: SMS, notification, receipt

**Clinic Hours**:
The times when clinic staff are available. A Handoff is only offered during Clinic Hours; otherwise a Callback Request is.
_Avoid_: Business hours, opening hours
