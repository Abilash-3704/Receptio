# AI Voice Receptionist for a Dental Clinic

> Portfolio project. Goal: a **production-grade** voice agent that answers clinic phone calls, books/reschedules/cancels appointments correctly, answers FAQs, handles emergencies safely, and escalates to humans when needed.
> What makes it production-grade (and portfolio-worthy) is **evals, observability, appointment correctness, and failure handling**, not just a working demo.

---

## 0. Instructions for Claude Code (read first)

1. **Work one phase at a time.** Do not start a phase until the previous phase's exit criteria are met. Ask me before skipping or reordering anything.
2. **Before using any third-party SDK (LiveKit, Pipecat, Deepgram, Cartesia, etc.), check its current official docs.** Do not rely on memory for APIs, versions, or pricing; they change. Pin dependency versions.
3. **The LLM never invents data.** Availability, Appointments, Patient records, and clinic facts come only from tool results or the knowledge base.
4. **Instrument first, optimize later.** Latency logging exists from the first working voice loop.
5. **Test logic in text mode, test audio sparingly.** Voice minutes cost money; most evals run as text conversations against the same agent logic.
6. **Cost safety:** every external call goes through config with timeouts. Enforce a max call duration and a daily spend/minute cap in code. Never commit secrets; use `.env` and provide `.env.example`.
7. **Keep a `CHANGELOG.md`** of prompt/model changes, each tied to an eval result.
8. **Write tests alongside code.** No phase is done without passing tests and a short note in `docs/`.
9. When a decision is ambiguous, propose 2 options with tradeoffs, recommend one, and wait for my choice.

---

## 1. What we're building

### Product summary
An inbound-call AI receptionist for a single dental clinic (multi-tenant is a stretch goal). Callers reach it only through a **browser WebRTC client**; there is no real phone number and no telephony integration.

### Supported intents
| Intent | Behavior |
|---|---|
| Book appointment | Collect details, check real availability, offer 2-3 available times, hold, read back, confirm, send Confirmation Message |
| Reschedule / cancel | Find existing Appointment, offer alternatives or confirm cancellation |
| Clinic FAQ | Hours, location, services, pricing ranges, insurance accepted (from a knowledge base only) |
| Emergency | Fixed safety script + immediate human handoff. Never goes through scheduling logic |
| Out of scope / unclear | Clarify once or twice, then escalate to human or schedule a callback |

### Explicit non-goals (v1)
- No medical advice or diagnosis
- No insurance verification or payments
- No languages other than English (Hindi/Kannada/Hinglish is a post-v1 stretch)
- No integration with real dental practice management systems (use a mock clinic backend)

### Success metrics (targets)
| Metric | Target |
|---|---|
| Task completion rate (scenario suite) | >= 90% |
| Wrong-time / overlapping-appointment rate | 0% |
| Emergency detection recall (scenario suite) | 100% |
| End-of-speech to start-of-agent-speech latency | p50 < 800 ms, p95 < 1500 ms (measure, then tune) |
| Cost per call (average 3-minute call) | Tracked and reported |
| Escalation rate and reasons | Tracked and reported |

---

## 2. Architecture

```
Caller (browser WebRTC)
   -> Real-time framework (LiveKit Agents or Pipecat)
        VAD -> streaming ASR -> LLM (tool calling) -> streaming TTS
   -> Agent core
        - Call state machine (structured state OUTSIDE the LLM context)
        - Tools: check_availability, hold_appointment, confirm_appointment, cancel_appointment,
                 reschedule_appointment, lookup_patient, search_faq, send_confirmation_message, request_handoff, request_callback
        - Safety layer: emergency detector (every turn), scope limits, injection defenses
   -> Clinic backend (FastAPI + Postgres): providers, services, patients, appointments
   -> Redis: call state, idempotency keys, rate limiting
   -> Observability: per-call traces, transcripts, latency/cost metrics, dashboard
   -> Eval harness: scenario suite, simulated caller, metrics report, CI gate
```

### Proposed stack (confirm in Phase 0)
- **Language:** Python 3.11+
- **Real-time orchestration:** LiveKit Agents (preferred) or Pipecat
- **ASR:** Deepgram (streaming)
- **LLM:** a fast tool-calling model, with model name configurable via env; consider a smaller model for simple turns
- **TTS:** Cartesia or ElevenLabs (streaming)
- **Backend:** FastAPI, Postgres, Redis, SQLAlchemy/Alembic
- **Observability:** structured JSON logs + OpenTelemetry or a lightweight tracing table; simple dashboard (e.g., Grafana or a small web UI)
- **Packaging:** Docker Compose (`docker compose up` must start everything)
- **CI:** GitHub Actions running lint, unit tests, and the text-mode eval suite

### Key design rules
- **Call state object** (intent, collected fields, confirmed fields, retry counters, escalation flags) lives in code/Redis, not just in the prompt. The LLM sees a compact summary each turn.
- **Appointment flow:** collect -> check availability -> offer times -> hold -> read-back -> explicit "yes" -> confirm (idempotent) -> Confirmation Message. Never skip the read-back.
- **Idempotency:** every write tool takes an idempotency key; retries must not double-book.
- **Retry caps:** two failures on the same step -> escalate (Handoff or Callback Request).
- **Emergency check runs on every caller turn**, even mid-scheduling.
- **Disclosure:** the agent states it is an AI and that the call may be recorded, at the start of every call.
- **PII:** redact phone numbers and names in logs where practical; store full data only in the backend DB.

### Suggested repo layout
```
voice-receptionist/
  PROJECT.md
  CHANGELOG.md
  README.md
  docker-compose.yml
  .env.example
  agent/
    main.py                # entrypoint for the voice agent worker
    pipeline.py            # VAD/ASR/LLM/TTS wiring
    state.py               # call state machine
    prompts/               # versioned system prompts
    tools/                 # tool definitions (thin wrappers over backend API)
    safety/                # emergency detection, injection defenses
    metrics.py             # per-turn latency/cost logging
  backend/
    app/                   # FastAPI app (clinic API)
    models.py
    migrations/
    seed.py                # providers, services, Clinic Hours, FAQ content
  evals/
    scenarios/             # YAML/JSON scenario definitions
    simulator.py           # LLM-driven caller (text mode + voice mode)
    runner.py              # runs suite, asserts DB state, outputs metrics
    reports/               # generated metrics reports
  dashboard/               # call logs + metrics UI
  docs/
    architecture.md
    latency-budget.md
    eval-report.md
    failure-analysis.md
  tests/
```

---

## 3. Roadmap

Timeline is ~6-7 weeks part-time. Each phase lists **tasks** and **exit criteria**. Check boxes as you go.

### Phase 0: Setup and scoping (2-3 days)
**Goal:** decide everything expensive to change later.

- [ ] Write a one-page spec in `docs/` (intents, out-of-scope list, emergency policy)
- [ ] Choose framework (LiveKit Agents vs Pipecat) and providers (ASR, LLM, TTS); record the decision and reasons in `docs/architecture.md`
- [ ] Initialize repo, Python tooling (formatter, linter, type checker), pre-commit, `.env.example`
- [ ] Set provider spend caps on every dashboard (manual step; add a checklist item to README)
- [ ] Create `docs/tricky-calls.md` with 15 tricky caller scenarios (corrections, ambiguous times, interruptions, unusual names, angry caller, emergency mid-scheduling, off-topic, injection attempt)
- [ ] Define target metrics in `docs/metrics.md` (from Section 1)

**Exit:** spec, repo skeleton, decisions recorded, metrics defined.

### Phase 1: Voice loop with latency instrumentation (Week 1)
**Goal:** talk to a bot in the browser and see where every millisecond goes.

- [ ] Browser WebRTC client + agent worker connected
- [ ] Streaming pipeline: VAD -> ASR -> LLM -> TTS; start TTS on the first sentence
- [ ] Barge-in: agent stops speaking immediately when interrupted
- [ ] Per-turn timing log: ASR final, LLM first token, TTS first audio, total response latency
- [ ] AI disclosure + recording notice at call start
- [ ] Max call duration cap enforced in code
- [ ] Script to summarize p50/p95 latency from logs

**Exit:** working voice chat; latency summary over 20+ turns saved in `docs/latency-budget.md`.

### Phase 2: Clinic backend, tools, and state (Week 2)
**Goal:** book correctly, verified in text mode first.

- [ ] FastAPI + Postgres clinic backend with seed data (providers, services, working hours, Clinic Hours, patients)
- [ ] Endpoints and tools: `check_availability`, `hold_appointment`, `confirm_appointment`, `cancel_appointment`, `reschedule_appointment`, `lookup_patient`
- [ ] Hold-then-confirm with expiry; idempotency keys on all writes
- [ ] Call state machine with structured state, retry counters, and compact per-turn summary for the LLM
- [ ] Read-back confirmation step enforced in code (confirm tool rejects calls without confirmed state)
- [ ] Text-mode runner: feed a scripted conversation to the agent logic, assert final DB state
- [ ] First 15 scenarios implemented (from `docs/tricky-calls.md` plus happy paths)

**Exit:** text-mode agent passes all 15 scenarios; wrong-time rate is 0.

### Phase 3: Conversation quality and safety (Week 3)
**Goal:** survive messy, real callers.

- [ ] Corrections ("actually Thursday"), relative/ambiguous times ("next Tuesday", "after lunch"), date/time normalization with the clinic's timezone
- [ ] Name/phone confirmation with spelling or digit read-back
- [ ] ASR low-confidence handling and "could you repeat that" flows
- [ ] FAQ retrieval over a small knowledge base (hours, location, services, pricing ranges, insurance)
- [ ] Emergency detector on every turn; fixed script ("please call emergency services") + human handoff; never enters scheduling logic
- [ ] `request_handoff` and `request_callback` flows
- [ ] Scope limits: no medical advice; polite refusal + redirect
- [ ] Prompt-injection defenses (caller speech is untrusted input; tools validate all arguments server-side)
- [ ] Retry caps: two failures on a step -> escalate

**Exit:** all tricky-call scenarios pass in voice; emergency path triggers reliably in tests.

### Phase 4: Evaluation at scale (Week 4)
**Goal:** turn testing into a system. This is the portfolio centerpiece.

- [ ] Grow suite to 50-100 scenarios (happy paths, interruptions, corrections, no availability, time taken during hold, angry caller, off-topic, emergencies, injection attempts, silence/dropped call)
- [ ] LLM-driven simulated caller with configurable persona (patient, rushed, confused, hostile)
- [ ] Text-mode runs for the full suite; voice-mode (TTS caller, optional background noise) for a 10-15 scenario subset
- [ ] Metrics report: completion rate, wrong-time rate, emergency recall, escalation rate, latency percentiles, cost per call
- [ ] CI gate: run text-mode suite on every prompt/model change; fail on regression vs. baseline
- [ ] 2-3 documented improvement cycles with before/after numbers in `docs/eval-report.md`

**Exit:** one-command eval run outputs a metrics table; improvement history documented.

### Phase 5: Persistence and observability (Week 5)
**Goal:** behave like a service, not a script.

- [ ] Persist calls, transcripts, turns, tool calls, outcomes, latency, and cost in Postgres
- [ ] Dashboard: calls/day, completion rate, p95 latency, cost per minute, escalation reasons, per-call trace viewer
- [ ] Confirmation Message (mocked behind an interface, shown on screen and in the dashboard; no real SMS provider) + follow-up for dropped calls
- [ ] PII redaction in logs

**Exit:** every call appears in the dashboard with a full trace.

### Phase 6: Hardening and deployment (Week 6)
**Goal:** prove it holds up under stress.

- [ ] Load test (target ~20 concurrent calls); record latency under load
- [ ] Failure injection: calendar timeout, LLM error, ASR dropout, TTS failure, mid-call disconnect; verify graceful behavior (apology, retry once, offer callback/human)
- [ ] Rate limiting, auth on admin/dashboard endpoints, secrets handling review
- [ ] Alerts for latency spikes and tool failure rates
- [ ] `docker compose up` brings up the full system from a clean clone
- [ ] Deploy to a small VPS or free tier; document the process

**Exit:** deployed system, documented load test, passing failure-injection checklist.

### Phase 7: Portfolio packaging (a few days)
- [ ] Live demo link (web client)
- [ ] 2-minute demo video including an interruption and a correction
- [ ] README: overview, architecture diagram, latency budget, tradeoffs, quickstart
- [ ] `docs/eval-report.md`: methodology, metrics, before/after
- [ ] `docs/failure-analysis.md`: what broke, why, how it was fixed
- [ ] Optional blog post

**Exit:** a stranger can clone, run, and understand the project in 10 minutes.

---

## 4. Eval scenario format (proposed)

Each scenario is a YAML file under `evals/scenarios/`:

```yaml
id: book_with_correction_001
description: Caller books a cleaning, then changes the day mid-flow
persona: polite_rushed
turns:            # scripted caller turns, or a goal for the LLM simulator
  - "Hi, I'd like to book a cleaning for Tuesday afternoon"
  - "Actually, can we do Thursday instead?"
  - "Yes, that works. My name is Priya Sharma"
expected:
  final_state:
    appointment_confirmed: true
    appointment_day: thursday
    overlapping_appointment: false
  must_not:
    - invented_time
    - confirm_without_readback
  max_turns: 14
```

Emergency, injection, and failure scenarios use the same format with `must_not` rules such as `scheduling_attempted_during_emergency` or `revealed_system_prompt`.

---

## 5. Definition of done (whole project)

- [ ] Wrong-time rate is 0 across the full suite, including after every prompt change
- [ ] Emergency recall is 100% on the emergency scenarios
- [ ] Latency percentiles measured and documented, including under load
- [ ] Every call fully traceable (ASR text, LLM calls, tool calls, TTS timing, cost)
- [ ] CI blocks regressions automatically
- [ ] One-command local run and a deployed demo
- [ ] Docs: architecture, latency budget, eval report, failure analysis

---

## 6. If time runs short

Cut in this order: dashboard polish -> load test size -> multi-provider support.
**Never cut:** evals, latency instrumentation, appointment correctness, emergency handling.

---

## 7. Stretch goals (after v1 is done)

- Hindi/Kannada/Hinglish with language auto-detection
- Multi-tenant (several clinics with separate calendars and FAQs)
- Voicemail and missed-call callback flow
- WhatsApp follow-ups
- Real calendar integration (Google Calendar) alongside the mock backend
