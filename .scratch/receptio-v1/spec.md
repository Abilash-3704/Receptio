Status: ready-for-agent

# Receptio v1: web-based AI voice receptionist for a dental clinic

Vocabulary follows `GLOSSARY.md`. Decision ADR-0001 (code-enforced Appointment confirmation) applies throughout.

## Problem Statement

A dental clinic's phone line is answered by people who are not always free. Callers who cannot get through abandon the clinic, and callers who do get through take staff time for routine work: booking, rescheduling, cancelling, and asking about hours, location and prices. Existing voice bots are demos: they sound good but double-book, invent availability, mishandle emergencies, and give no evidence of how well they work.

This is also a portfolio project. It has to show that a voice agent can be trusted with real scheduling, which means measurable correctness, safety, observability and failure handling, not a polished demo.

## Solution

A Caller opens a web page, starts a Call, and talks to an AI receptionist for a single fictional clinic in Bengaluru. There is no real phone number. The agent opens by stating it is an AI and that the Call may be recorded. It books, reschedules and cancels Appointments against real Availability, answers questions from a clinic knowledge base, treats Emergencies and Urgent Cases with fixed safe behaviour, and escalates to simulated clinic staff through a Handoff or a Callback Request when it cannot or should not continue.

The agent never invents Availability, Appointments, Patient records or clinic facts. Everything comes from tools or the knowledge base. An Appointment becomes Confirmed only through a code-enforced path. Every Call is traced end to end, and a scenario-driven evaluation suite measures completion, wrong-time rate, Emergency recall, escalation rate, latency and cost, and gates changes in CI.

## User Stories

### Starting a Call and disclosure

1. As a Caller, I want to start a Call from a web page with one click, so that I can talk to the clinic without installing anything.
2. As a Caller, I want the agent to tell me at the start that it is an AI and that the Call may be recorded, so that I know what I am talking to.
3. As a Caller, I want to object to recording and be offered a Callback Request instead, so that I am not forced to be recorded.
4. As a Caller, I want to be told clearly when the demo is at capacity, so that I understand why I cannot start a Call.
5. As a Caller, I want the agent to warn me shortly before the maximum Call duration and offer a Callback Request, so that I am never cut off silently.
6. As a Caller, I want to interrupt the agent while it is speaking and have it stop at once, so that the conversation feels natural.
7. As a Caller, I want the agent to ask me to repeat myself when it did not hear me well, so that mistakes do not slip into my Appointment.

### Booking an Appointment

8. As a Caller, I want to say what Service I need in my own words, so that I do not have to know the clinic's menu.
9. As a Caller, I want the agent to ask for the Patient's name and phone number, so that the Appointment is recorded under the right person.
10. As a Caller booking for someone else, I want to give the Patient's name when it differs from mine, so that the Appointment is for the right Patient.
11. As a Caller, I want to say vague times such as "next Tuesday" or "after lunch" and have them interpreted in the clinic's timezone, so that I get the day I meant.
12. As a Caller, I want to be offered two or three real available times, so that I can choose without hearing a long list.
13. As a Caller, I want to state a preferred Provider, or none, so that I can see my usual dentist without being forced to choose.
14. As a Caller, I want the agent to account for how long my Service takes, so that the time offered fits.
15. As a Caller, I want to correct myself mid-flow ("actually Thursday"), so that I do not have to start over.
16. As a Caller, I want the agent to read back the Service, Provider, date, time and Patient name before anything is final, so that I can catch errors.
17. As a Caller, I want the Appointment to be confirmed only after I clearly say yes, so that a stray "uh-huh" cannot commit me.
18. As a Caller, I want to be told when my chosen time was taken or my hold ran out, and be offered alternatives, so that I am never left with a false booking.
19. As a Caller, I want a Confirmation Message shown on screen at the end of the Call, so that I have a record of my Appointment.
20. As a Caller, I want the agent to refuse times outside the booking window (sooner than 2 hours, later than 60 days, or outside Clinic Hours), so that I am not given an Appointment the clinic cannot honour.
21. As a Caller who needs something sooner than 2 hours ahead, I want to be offered a Callback Request, so that staff can help.
22. As a Caller, I want to be told when no Availability exists for my request, so that I can try another day or Provider.
23. As a new Caller, I want to book without any account or verification, so that booking stays quick.

### Rescheduling and cancelling

24. As a returning Patient, I want to give my name and phone number to find my Appointment, so that only I can change it.
25. As a Caller, I want the agent to reveal nothing about any Appointment until my name and phone number both match, so that my privacy is protected.
26. As a Caller who fails identification twice, I want to be offered a Callback Request, so that I am not stuck in a loop.
27. As a Patient with several Appointments, I want the agent to ask which one I mean, so that the wrong one is not changed.
28. As a Patient, I want to move my Appointment to a new time, so that I can fit my schedule.
29. As a Patient, I want my original Appointment kept if the new time falls through, so that a failed reschedule never leaves me with nothing.
30. As a Patient, I want to cancel an Appointment with a read-back of what will be cancelled, so that I do not cancel the wrong one.
31. As a Patient, I want cancelling to be allowed at any time without penalty, so that I am not discouraged from freeing the time.
32. As a Patient, I want to be prevented from holding two overlapping Appointments, so that my schedule stays consistent.

### Clinic questions

33. As a Caller, I want to ask about hours, location, Services, price ranges and insurance accepted, so that I can decide whether to book.
34. As a Caller, I want prices given as ranges and never as a quote, so that I am not misled.
35. As a Caller, I want the agent to say when it does not have an answer and offer a Callback Request, so that I am not given a guess.
36. As a Caller, I want the agent not to give medical advice, so that I am directed to a professional instead.

### Emergencies and Urgent Cases

37. As a Caller describing a life-threatening problem, I want to hear a clear instruction to call 112 immediately, so that I get help without delay.
38. As a Caller in an Emergency, I want the agent to hand off to staff straight away during Clinic Hours, or record a Callback Request when closed, so that the clinic is aware.
39. As a Caller in an Emergency, I want this to work even in the middle of booking, so that the agent never keeps scheduling while I am in danger.
40. As a Caller with an Urgent Case such as severe pain or a knocked-out tooth, I want to be handed to staff for same-day triage, so that I am seen quickly.
41. As a Caller with an Urgent Case, I want no booking attempted and no advice given, so that a person decides what to do.
42. As clinic staff, I want an Emergency or Urgent Case Call to release any in-progress Hold, so that no time is blocked for someone who will not attend.

### Escalation

43. As a Caller, I want to ask for a human at any point and be handled promptly, so that I am never trapped with the bot.
44. As a Caller, I want a Handoff only when staff are available, so that I am not connected to nobody.
45. As a Caller outside Clinic Hours, I want to leave a Callback Request with my number and reason, so that someone rings me back.
46. As a Caller who is angry or confused, I want the agent to stay calm and escalate after a couple of failed attempts, so that things do not get worse.
47. As a Caller who goes off-topic, I want a polite redirect, so that the agent stays on task.
48. As a Caller who stays silent or hangs up mid-booking, I want any Hold to expire on its own, so that nothing is left blocked.

### Safety and abuse

49. As the clinic, I want caller speech treated as untrusted input, so that instructions hidden in it cannot change the agent's behaviour.
50. As the clinic, I want every tool argument validated by the backend, so that a manipulated agent still cannot make an invalid change.
51. As the clinic, I want the agent never to reveal its instructions, so that internals are not exposed.
52. As the clinic, I want a maximum Call duration, a per-visitor daily Call limit and a global daily minute cap, so that spend and abuse stay bounded.

### Staff and demo

53. As a demo viewer, I want a switch that forces the clinic open or closed, so that I can see both the Handoff and Callback Request paths at any time of day.
54. As clinic staff, I want to see every Call in a dashboard with its transcript, tool calls, outcome, latency and cost, so that I can audit what happened.
55. As clinic staff, I want calls, completion rate, p95 latency, cost per minute and Escalation reasons summarised, so that I can judge how the agent is doing.
56. As clinic staff, I want Patient names and phone numbers redacted in logs, so that personal data stays in the backend database only.
57. As clinic staff, I want admin and dashboard endpoints to require authentication, so that call data is not public.

### Evaluation and engineering

58. As the developer, I want to run a whole scenario suite in text mode with one command, so that I can check behaviour cheaply.
59. As the developer, I want an LLM-driven simulated Caller with configurable personas, so that I can test patient, rushed, confused and hostile behaviour.
60. As the developer, I want a small subset of scenarios run in voice mode, so that audio-specific problems are caught without large voice cost.
61. As the developer, I want scenarios to assert final database state and `must_not` rules, so that correctness is checked on outcomes, not wording.
62. As the developer, I want a metrics report of completion rate, wrong-time rate, Emergency recall, Urgent Case recall, Escalation rate, latency percentiles and cost per Call, so that quality is quantified.
63. As the developer, I want CI to fail when a prompt or model change regresses against a baseline, so that regressions are blocked automatically.
64. As the developer, I want each prompt or model change recorded with its evaluation result, so that improvement is traceable.
65. As the developer, I want per-turn latency recorded from the first working voice loop, so that optimisation is guided by data.
66. As the developer, I want failure injection for slow backend, LLM error, speech-recognition dropout, speech-synthesis failure and mid-Call disconnect, so that I can verify graceful behaviour.
67. As the developer, I want `docker compose up` to start the whole system from a clean clone, so that anyone can run it.
68. As a reviewer of the portfolio, I want a live demo, a short video and documents on architecture, latency, evaluation and failure analysis, so that I can judge the work in ten minutes.

## Implementation Decisions

### Modules

- **Web client**: a browser page that starts and ends a Call, plays agent audio, shows the Confirmation Message, enforces visible capacity messages, and carries a demo-only control to force the clinic open or closed.
- **Voice pipeline**: voice-activity detection, streaming speech recognition, LLM turn, streaming speech synthesis, with barge-in and speech starting on the first sentence. Emits a timing record per turn: speech-recognition final, LLM first token, speech-synthesis first audio, total response latency.
- **Agent core**: the call state machine, the tool layer and the safety layer. This is the module the text-mode seam exercises.
- **Call state machine**: structured state kept outside the LLM context (intent, collected fields, confirmed fields, retry counters, Escalation flags, the current Hold, whether the read-back was agreed). The LLM sees a compact summary each turn.
- **Safety layer**: an Emergency and Urgent Case detector on every Caller turn, scope limits (no medical advice), and prompt-injection defences. The Emergency script is fixed, versioned text and is never generated.
- **Tool layer**: thin wrappers over the clinic backend: check availability, hold appointment, confirm appointment, cancel appointment, reschedule appointment, look up patient, search FAQ, send confirmation message, request handoff, request callback.
- **Clinic backend**: a service owning Providers, Services, Clinic Hours, Patients, Appointments and Holds. It is the single authority on correctness. Seeded with a fictional Bengaluru clinic, a few Providers and a knowledge base.
- **Cost guard**: enforces the maximum Call duration, the per-visitor daily limit and the global daily minute cap in code, with a warning before the duration cap.
- **Observability store and dashboard**: persists Calls, transcripts, turns, tool calls, outcomes, latency and cost, and presents a list, a per-Call trace and summary metrics.
- **Eval harness**: scenario definitions, an LLM-driven simulated Caller with personas, a runner that asserts database state and `must_not` rules, a metrics report, and a baseline comparison used by CI.

### Domain rules (from the grilling session)

- The canonical term is Appointment, never booking. The glossary is authoritative.
- A Caller and a Patient are distinct. Booking for a different Patient captures that Patient's name. No guardianship or consent rules.
- Providers are modelled from the start, seeded with a small set. A caller preference is optional.
- Availability is duration-based per Service: Provider working hours, minus Appointments and unexpired Holds, fitted to the Service's duration.
- An Appointment is Held, then Confirmed, then possibly Cancelled. A Hold expires after 5 minutes. Completed and no-show states are out of scope.
- An Appointment is Confirmed only when the backend sees an unexpired Hold and a recorded read-back agreement for that exact Hold. Writes carry idempotency keys, so a retry returns the same result and never creates a second Appointment.
- If a Hold expired, Availability is re-checked before confirming. If the time was taken, alternatives are offered. A correction releases the old Hold and creates a new one.
- A reschedule never edits an Appointment. It places a Hold on the new time, and on confirmation cancels the old Appointment in the same transaction. On any failure the original Appointment is kept.
- A Patient may have several Confirmed Appointments, never two overlapping in time, regardless of Provider. If a Caller's request is ambiguous, the agent asks which Appointment.
- Appointments may be made from 2 hours ahead to 60 days ahead, within Clinic Hours. Anything sooner is offered a Callback Request. Cancellation has no penalty or notice rule.
- Identifying a Caller for reschedule or cancel requires the Patient's full name and phone number on file to both match. Two failed attempts lead to a Callback Request. New bookings are unverified. Date of birth is deliberately not used because the data is fake; this is a documented tradeoff.
- A Patient record is created when a new Caller's first Appointment is Confirmed. Held Appointments and abandoned Calls create none.
- There are two safety levels. An Emergency gets the fixed script directing the Caller to call 112, then an Escalation. An Urgent Case gets an immediate Escalation for same-day staff triage with no 112 script. Neither enters scheduling, neither gets medical advice, and any in-progress Hold is released.
- An Escalation is a Handoff or a Callback Request and always records a reason. A Handoff is offered only during Clinic Hours; otherwise a Callback Request. An Emergency outside Clinic Hours still hears the script, then a Callback Request.
- Clinic Hours are seeded configuration. A demo-only override forces the clinic open or closed.
- Two failures on the same step trigger an Escalation.
- The FAQ answers only from the knowledge base. If it has no answer, it says so and offers a Callback Request. Prices are always ranges.
- The Confirmation Message is a mocked record behind an interface, shown on screen and in the dashboard. No real SMS provider is used.
- The disclosure (AI, may be recorded) is the first thing said on every Call. An objection to recording leads to a Callback Request instead of a Call.
- There is no real phone number or telephony. There is no caller ID, so every Caller states their name and phone number.
- Clinic location: fictional, Bengaluru, timezone Asia/Kolkata, emergency number 112. Time interpretation uses this timezone.

### Cross-cutting

- Caps: a maximum Call duration of 5 minutes (warning at 4 minutes 30 seconds), a per-visitor limit of 3 Calls per day keyed on browser plus IP, and a global cap of 120 minutes per day. Hitting the visitor or global cap blocks new Calls with a clear message in the web client. All values are configuration.
- A test-mode override lifts the visitor and global caps so the concurrent load test can run. It is off by default and must never be enabled in the deployed demo.
- Every external call goes through configuration with timeouts. Secrets are never committed.
- Personal data is redacted in logs where practical. Full data lives only in the backend database.
- Admin and dashboard endpoints require authentication, and the public Call endpoint is rate limited.
- Third-party SDKs (real-time framework, speech, LLM) must be checked against current official docs before use, and dependency versions pinned. The stack proposed in the project document is confirmed in Phase 0, not here.

## Testing Decisions

A good test exercises external behaviour only: what the Caller hears, what the database contains afterwards, and what the tools were asked to do. It never asserts on prompt wording, internal function calls or intermediate state-machine fields. Tests are written alongside code.

Three seams, in order of preference:

1. **Text-mode Call (primary).** Caller turns go in; agent replies and tool calls come out; assertions are on final database state, transcript properties and `must_not` rules. Runs against the real agent core and clinic backend. All eval scenarios, the metrics report and the CI gate use this seam. Scenarios cover happy paths, corrections, ambiguous times, interruptions, no Availability, taken or expired Holds, angry and off-topic Callers, injection attempts, Emergencies, Urgent Cases, silence and dropped Calls.
2. **Clinic backend API (deterministic, no LLM).** Direct tests of appointment endpoints: confirm gating, Hold expiry, idempotent retries, concurrent confirms for one time, overlap prevention, atomic reschedule. This is where the zero wrong-time and overlap target is proven, because a conversation cannot reliably hit a race on demand.
3. **Voice pipeline (measured).** A small set of audio scenarios covering barge-in, per-turn latency and the duration warning. Produces a latency report rather than pass/fail logic, because voice minutes cost money.

There is no prior art in the repo; all three are new. The LLM-driven simulated Caller is used for breadth, while scripted turns are used wherever a result must be exactly reproducible.

Targets: task completion at least 90% on the scenario suite; wrong-time and overlapping-appointment rate 0%; Emergency recall 100%; Urgent Case recall at least 95%; false-positive rate tracked for both; latency from end of speech to start of agent speech with p50 below 800 ms and p95 below 1500 ms, measured then tuned; cost per average 3-minute Call and Escalation rate and reasons tracked and reported.

## Out of Scope

- Any real phone number, SIP or telephony.
- Medical advice or diagnosis.
- Insurance verification and payments.
- Languages other than English.
- Integration with real dental practice management systems or a real calendar.
- A real SMS provider.
- Multi-tenant support, voicemail flows, WhatsApp follow-ups.
- Guardianship and consent rules for booking on behalf of a minor.
- Completed and no-show Appointment states, cancellation penalties, and date-of-birth verification.

## Further Notes

- Emergency script (fixed text, true whether or not the clinic is open): "This sounds like a medical emergency. Please end this call and call 112 right now, or go to your nearest emergency room. I'm alerting our clinic staff as well."
- Seeded Clinic Hours: Monday to Saturday, 09:00 to 18:00 IST, closed on Sunday, so the Callback Request path is testable on a predictable day.
- Urgent Case recall target is at least 95% on the scenario suite. The false-positive rate for both Emergency and Urgent Case is tracked alongside recall, so that over-flagging cannot be used to inflate recall.
- Cap values: maximum Call duration 5 minutes with a warning at 4 minutes 30 seconds; per-visitor limit of 3 Calls per day, keyed on browser plus IP; global cap of 120 minutes per day. The global cap is a starting value to revisit once real cost per minute is measured in the voice loop phase.
- Fifteen tricky-call scenarios should be written before implementation, then grown to 50 to 100 scenarios.
- Keep a changelog of prompt and model changes, each tied to an evaluation result.
- The suggested phases (setup, voice loop with latency, backend and state, conversation quality and safety, evaluation at scale, persistence and observability, hardening, packaging) are the natural basis for breaking this spec into tickets.
