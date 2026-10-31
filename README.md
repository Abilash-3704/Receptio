# Receptio

An AI voice receptionist for a fictional dental clinic in Bengaluru. Callers reach it through a web client, never a real phone number. See `GLOSSARY.md` for vocabulary, `docs/architecture.md` for decisions and `docs/metrics.md` for targets.

> Status: a Caller can book an Appointment in a text-mode Call (scripted LLM in tests). A voice loop (LiveKit, Deepgram, Claude, Cartesia) runs end to end with a synthetic Caller; the human mic and browser checks are listed under "Voice mode" and are not yet done.

## Quickstart

```
cp .env.example .env                 # then set BACKEND_SERVICE_TOKEN and DASHBOARD_PASSWORD (below)
docker compose up --build            # db, clinic backend (:8000), agent (:8001)
curl -X POST localhost:8001/text-calls/tracer   # opens a text-mode Call, shows the disclosure, ends it
```

If host ports 8000 or 8001 are taken, set `BACKEND_PORT` and `AGENT_PORT`. Text mode needs no provider keys.

Development (needs [uv](https://docs.astral.sh/uv/); Python 3.12 is installed by uv):

```
uv sync
uv run ruff format --check agent backend tests && uv run ruff check agent backend tests
uv run pyright
uv run pytest
```

By default the backend tests run on in-memory SQLite. Concurrency tests need a real Postgres and skip without one (CI provides one via a service container, so they run there); the same variable runs the whole backend suite through the Alembic migrations on Postgres:

```
docker run -d --name receptio-test-pg -e POSTGRES_USER=receptio -e POSTGRES_PASSWORD=receptio -e POSTGRES_DB=receptio -p 55432:5432 postgres:17-alpine
TEST_DATABASE_URL=postgresql+psycopg://receptio:receptio@localhost:55432/receptio uv run pytest
```

The test database's `public` schema is dropped and rebuilt for every test, so point it at a throwaway database only. The backend applies Alembic migrations (`backend/migrations`) on startup for Postgres.

The Anthropic SDK is a normal dependency, held at 1.x because `livekit-plugins-anthropic` 1.8.10 needs `anthropic<2`. The LiveKit voice packages are an optional extra (`uv sync --extra voice`, needed for `voice/`; CI installs it).

### Text-mode booking and scenarios

The agent core (`agent/`) never imports LiveKit or the Anthropic SDK. The LLM sits behind `agent/llm.py`: tests and CI use `agent/scripted_llm.py` (deterministic, no network, no keys). The real model, `agent/anthropic_llm.py`, needs `ANTHROPIC_API_KEY`, and `LLM_MODEL` / `LLM_MODEL_FAST`; it is not run in CI.

```
uv run pytest tests/test_scenario_runner.py     # runs evals/scenarios/*.yaml with the scripted LLM
uv run pytest tests/test_safety_scenarios.py -s # Emergency / Urgent Case recall and false positives
```

The safety layer (Emergency and Urgent Case detector, fixed 112 script) and what it cannot cover are in `docs/safety.md`.

### Eval harness and metrics report

One command runs the scenario suite and writes the metrics table (Markdown and JSON) to `evals/reports/` (git-ignored):

```
uv run python -m evals                          # scripted: deterministic, free, no network, no key
uv run python -m evals --write-baseline         # save this run as evals/baselines/scripted.json
uv run python -m evals --check-baseline         # exit 1 if worse than the baseline (ticket 15's gate)
```

The suite is 114 scenarios (102 core, 12 known-gap probes); `docs/eval-report.md` has the methodology, the current baseline, which scenario covers which of the 15 tricky calls, and the known gaps.

**CI gate.** The `scripted-evals` job in `.github/workflows/ci.yml` runs `uv run python -m evals --mode scripted --check-baseline` on every push and pull request. It fails (exit 1, `REGRESSION against baseline: ...`) if any gated metric is worse than `evals/baselines/scripted.json`, if wrong-time rate is not 0, Emergency recall is not 100%, completion is under 90% or Urgent Case recall under 95%, or if fewer core scenarios ran than the baseline recorded. Run it locally before pushing. When a metric legitimately changes, regenerate with `--write-baseline` and say why in the commit. CI never runs a real-model or simulated-Caller run: it has no key and that spends money.

The scripted run uses the scripted LLM and the scripted Caller turns, both written by the author of the rules. Its 100% is a **regression gate, not evidence of real-model quality**. For meaningful recall and completion numbers, an LLM plays the Caller (personas `patient`, `rushed`, `confused`, `hostile`, in `evals/personas.yaml`; add your own with `--personas FILE`) and the real model plays the agent. That calls the real API and costs money, so it is opt-in.

#### Manual real-model runs

Run these by hand, from your own machine, with the key in your shell only (never in a committed file, and there is no CI secret for it):

```
uv sync
uv run python -m evals --mode simulated --dry-run                  # prints worst-case spend, calls nothing, needs no key
ANTHROPIC_API_KEY=... uv run python -m evals --mode simulated --persona hostile --limit 3 --max-cost-usd 0.25
```

Log every prompt or model change, with the run that justifies it, in `CHANGELOG.md` (PROJECT.md rule 7). `docs/eval-report.md` says how to produce the first real before-and-after cycle; none has been run yet.

- Needs `ANTHROPIC_API_KEY` in the environment (never in a file that is committed). Without it the command refuses and exits 2.
- Hard caps, defaults 150 requests and 1.00 USD (`--max-requests`, `--max-cost-usd`, or `EVAL_MAX_REQUESTS`, `EVAL_MAX_COST_USD`). When one is hit the run stops, a partial report is written marked ABORTED, and the exit code is 3. The scenario in flight is dropped.
- The simulated Caller works from each scenario's `caller_goal` (or its `description`), never from the scripted `turns`. Only 23 scenarios have a written `caller_goal` with names and numbers; the rest rely on the description, so booking scenarios without one will often fail for lack of details. Add `caller_goal` where you want a scenario to be meaningful in simulated mode.
- The CI and the tests never call the API; the simulator, metering and report are tested with a fake client (`tests/fake_anthropic.py`).

What the report measures (targets in `docs/metrics.md`): task completion rate, wrong-time or overlapping-Appointment rate, Emergency recall and Urgent Case recall each next to its false-positive rate, escalation rate with Handoff versus Callback Request and reasons, cost per Call. Scenarios marked `known_gap: true` (probes of what the detector cannot see) are listed under "Known gaps" and never move a target. Latency is a clearly marked `not measured` field: it needs the voice pipeline (tickets 12 and 16).

Cost per Call = (input tokens x input price + output tokens x output price) per model, summed over every request of the run (agent and simulated Caller) and divided by the Calls run. Token counts are real, taken from the API's `usage`. Prices come from `EVAL_PRICE_MAIN_INPUT_PER_MTOK`, `..._MAIN_OUTPUT_...`, `..._FAST_INPUT_...`, `..._FAST_OUTPUT_...` (USD per million tokens; `LLM_MODEL` is priced as main, `LLM_MODEL_FAST` and `EVAL_SIM_MODEL` as fast). **The built-in defaults are placeholders, not provider prices**: until you set those variables the report says `PLACEHOLDER prices`. Per-minute and per-3-minute figures assume 20 s per Caller turn (`EVAL_SECONDS_PER_CALLER_TURN`, also a placeholder), because text mode has no audio. Speech, telephony and hosting costs are not included.

The baseline file is `{"schema_version": 1, "mode": ..., "scenarios": core_count, "metrics": {name: value}}` holding the gated metrics (higher or lower is better) and the number of core scenarios, which may grow but not shrink; `evals.baseline.compare_to_baseline(report, baseline, tolerance)` returns the regressions, empty meaning pass. Simulated runs are noisy, so `--check-baseline` allows a 0.05 tolerance there.

Friction: Starlette's `TestClient` subclasses `httpx2.Client` instead of `httpx.Client` in this dependency set. Tests cast it back (see `tests/conftest.py`); the agent's backend code does not depend on httpx exception classes for that reason.

## Voice mode

`voice/` is the voice pipeline. It drives the same `TextCall` agent core as text mode (safety detector first, code-recorded read-back yes, Escalation rules), and the core never imports LiveKit.

```
voice/worker.py      LiveKit agent worker: Silero VAD -> Deepgram ASR -> agent core (Claude) -> Cartesia TTS; barge-in by LiveKit
voice/driver.py      one Caller turn through the core, yielded sentence by sentence (no LiveKit import)
voice/timing.py      per-turn timing record, one JSON line per turn (no LiveKit import)
voice/summary.py     p50/p95 over those lines; scripts/latency_summary is its command line
voice/web.py         token endpoint + static page (voice/web/index.html, livekit-client 2.23.0 from jsDelivr)
deploy/livekit.yaml  self-hosted LiveKit server config (run with --dev: devkey/secret, local only)
```

Run it (provider keys in `.env`; the LiveKit values are in `.env.example`):

```
docker compose up --build     # adds livekit (:7880, 7881/tcp, 7882/udp), voice-agent, web (:8002)
open http://localhost:8002    # Start call, allow the microphone
```

Without Docker: run a LiveKit server (`livekit-server --dev`), the backend, `python -m voice.worker start` and `uvicorn voice.web:create_app --factory --port 8002`. Turn timings go to `logs/voice-turns.jsonl` (ignored by Git) and the worker's stdout. `WORKER_PORT` moves the worker's health port (default 8081) if it is taken.

Design notes:

- The agent core answers a whole LLM step at a time, because its guards (times Availability did not return, prices `search_faq` did not return) must see the full text before the Caller hears any of it. So speech starts on the first *sentence* of the reply, not the first token, and "LLM first token" in the log is when the first sentence became available.
- Preemptive generation is off: the core mutates Call state (Holds, Escalations) when it answers, so it must run exactly once per Caller turn.
- `ReceptioAgent.llm_node` replaces LiveKit's LLM step. The session still needs an LLM object (LiveKit refuses to generate a reply without one), so a `livekit-plugins-anthropic` instance is passed that is never called.
- If the Caller barges in while the core is mid-step, the step still finishes (it runs in a thread) and its effects stand: the state machine, not what was heard, is the Call's truth. The stored transcript records what the agent said, not how much of it was heard.
- The token endpoint is unauthenticated and each token starts a Call that spends provider money, so the cost guard (below) sits in front of token issuance. Still run `DEPLOYED_ENVIRONMENT=true` and keep real provider keys out of any environment you have not capped.

### Cost guard (ticket 13)

All values are configuration (`.env.example`); defaults are the specified caps.

- **Duration.** A Call lasts at most 5 minutes (`CALL_MAX_SECONDS`). At 4:30 (`CALL_WARNING_SECONDS`) the agent says the Call will end in 30 seconds and offers a Callback Request; "yes" goes through the forced Callback Request path (a Callback Request even during Clinic Hours). At 5:00 it says so and ends. The logic is in the agent core (`agent/call_limits.py`, injectable clock), so text mode has it too; voice adds a timer (`voice/driver.py: watch_call_limits`) for a Caller who says nothing. If the Caller is mid-way through giving a number at 5:00 the Call still ends.
- **Per-visitor and global caps.** The token endpoint asks the backend to admit each Call (`POST /capacity/admissions`) and issues no token on refusal (HTTP 429, or 503 if the backend cannot be reached: it fails closed). 3 Calls per visitor per day (`VISITOR_CALLS_PER_DAY`), 120 minutes per day overall (`GLOBAL_MINUTES_PER_DAY`), plus a coarser per-IP ceiling (`IP_CALLS_PER_DAY`, default 10). The day is the clinic's (Asia/Kolkata). The global cap reserves 5 minutes per admitted Call and the worker hands back the unused part when the Call ends (`/capacity/admissions/<room>/settle`); a worker that dies leaves its Call charged in full.
- **Who is a visitor.** The browser is a random cookie the server issues; the IP is the connection's own address. `X-Forwarded-For` is ignored unless `TRUSTED_PROXY_HOPS` is set, and then only the entry your outermost proxy appended is used. Limits, honestly: this is a cost guard, not identity. Someone who clears cookies and changes IP gets fresh visitors; the per-IP ceiling bounds cookie clearing from one address and the global cap bounds total spend. People behind one shared address share its ceiling.
- **Where counters live.** Postgres, in the backend (`backend/app/capacity.py`), not Redis: the stack has no Redis, and exact counting under concurrency is one conditional `UPDATE ... WHERE used + n <= limit`, which Postgres serialises per row. Tested against Postgres with simultaneous Calls (`tests/test_capacity_concurrency.py`); SQLite cannot show this, so that file skips without `TEST_DATABASE_URL`.
- **Test mode.** `CAPS_TEST_MODE=true` lifts the three daily caps (not the Call duration) for the load test. It is off by default and the backend refuses to start with it while `DEPLOYED_ENVIRONMENT=true`. Set that flag in the deployed demo; nothing else stops a person who controls the environment from leaving it unset, so treat it as a guard against mistakes, not against the operator.
- **Recording objection.** A Caller who objects to recording ("I don't want to be recorded") is offered a Callback Request, always a Callback Request, and nothing more of the Call is stored from that point (the objection and later turns are not written to the backend; earlier turns, normally just the disclosure, remain). The audio was never recorded (`record=False`).
- **The page** disables Start call and shows the message on a 429 until reloaded. It was exercised by running its script under node with a stub DOM, not in a browser: how it looks has not been seen by a human.

Checked without a human (what was measured is in `docs/latency-budget.md`): the self-hosted LiveKit server starts, the worker registers and joins a room, the token endpoint mints a room-scoped token, and `scripts/voice_smoke.py` (a synthetic Caller publishing Cartesia-generated speech) gets the disclosure first, has its speech transcribed by Deepgram, gets an answer from the core and Cartesia audio back, with timing lines logged. The timing and summary logic is unit tested on synthetic logs.

`scripts/voice_smoke.py "phrase" ...` spends a few cents of real provider money per run. CI never runs it.

### Manual checklist (needs a human with a microphone and a browser)

1. `docker compose up --build`, open `http://localhost:8002` in Chrome or Firefox, press Start call, allow the microphone.
2. The first thing you hear is the AI and recording disclosure, before you say anything. Do not talk over it (it is not interruptible).
3. Ask "What are your opening hours?" and confirm a spoken answer. Then book a Cleaning end to end by voice and check the Appointment (`curl -u "staff:$DASHBOARD_PASSWORD" localhost:8000/calls/<id>`, or open the dashboard).
4. Barge-in: while the agent is mid-sentence (ask for something long, such as the list of Services), start talking. The agent must go silent at once, not finish the sentence. Note the delay you feel.
5. Say an Emergency sentence ("my father collapsed and is not breathing") and confirm the fixed 112 script, spoken without the LLM pause.
6. Press End call; confirm the Call is `ended` in the backend.
7. Latency: hold Calls totalling 20 or more turns (they append to the same log), then run `scripts/latency_summary logs/voice-turns.jsonl --min-turns 20`. Paste the output into `docs/latency-budget.md` with the date, machine, network and settings. Then tick the last two boxes in `.scratch/receptio-v1/issues/12-voice-loop-and-latency.md`.

## Call persistence and trace

Every Call, text or voice, is stored through one path: the agent core (`TextCall`) writes to the backend, and the voice worker adds its latency lines to the same Call. Tables (migration `0006`): `calls` (mode, outcome, outcome reason, cost), `call_turns` (the transcript), `call_tool_calls`, `call_llm_calls` (provider-reported tokens and the cost at the configured price) and `call_turn_timings` (voice latency per Caller turn). `backend.app.trace.load_trace(session, call_id)` reads one Call back whole; the trace viewer and the eval harness use that and nothing else. There is deliberately **no HTTP endpoint for it**: it carries names and phone numbers in full.

Outcome: `completed`, `escalated` (the Escalation's reason is copied onto the Call) or `dropped` (the Caller never spoke, or left mid-booking, mid-cancellation or before giving a callback number; the reason says which step). The backend overrules a claimed outcome when it holds an Escalation. A Hold left behind by a dropped Call is not released: it lapses on its own (tricky call 15, asserted by the scenarios `dropped_call_after_readback_015` and `silent_call_015`). A dropped Call whose Caller had given a number in the Call also gets a Callback Request, recorded after the Call is ended so the outcome stays `dropped` (ticket 19). Failure handling (apology, one retry, then an offer of staff) and the failure-injection seams are in `docs/failure-analysis.md`. Calls stored before `0006` keep their transcript and have no outcome.

Cost per Call, and how much of it is real:

| Component | Quantity | How known |
|---|---|---|
| Claude | input and output tokens | **measured**: the `usage` the API returned for each request (real LLM only; scripted runs record none) |
| Cartesia | characters of agent speech | **counted** by us from the transcript; the invoice may differ when speech is cut off by barge-in |
| Deepgram | seconds | **estimated**: the Call's length stands in for audio streamed, which the provider does not report back |

Prices are configuration (`LLM_PRICES_PER_MTOK`, `DEEPGRAM_USD_PER_MINUTE`, `CARTESIA_USD_PER_1K_CHARACTERS`). **The defaults are placeholders, not provider prices**; the stored cost carries `placeholder_prices: true` until all are set. A model without a price is stored with its tokens and `usd: null` rather than a guess. Text Calls have no audio, so no Deepgram or Cartesia cost. Nothing here was checked against a real provider invoice or a real voice Call.

Personal data: transcript text and tool arguments (names, phone numbers) are stored in full in these tables, which is the database record. Protection of the endpoints that return it, the dashboard and log redaction are in the next section.

## Dashboard, authentication and PII (ticket 18)

**Dashboard.** `http://localhost:8000/dashboard` (log in as `staff` with `DASHBOARD_PASSWORD`; the browser prompts). It lists every Call (newest first, paged), links each to a trace viewer (transcript, tool calls with arguments and results, outcome, latency per turn, cost per turn and per Call), and shows summary metrics: Calls per day (clinic day, Asia/Kolkata), completion rate (completed over ended Calls), p95 latency, cost per minute and Escalation reasons. JSON for the same data: `/dashboard/api/metrics`, `/dashboard/api/calls`, `/dashboard/api/calls/{id}` (the last is `backend.app.trace.load_trace`, the single read model). Latency is `total_from_speech_end_ms` (nearest-rank p95); cost shows a "placeholder price" flag wherever a price was never configured, and counts Calls whose cost left out an unpriced component. The metric code is `backend/app/metrics.py`, unit-tested on seeded rows. Server-rendered HTML, no JavaScript; everything from a Caller is HTML-escaped, the pages carry `Content-Security-Policy: default-src 'none'` and nothing is cacheable.

**Who may call what** (`backend/app/auth.py`, default deny). Two secrets, environment only, never committed, no defaults; a secret that is unset, empty or shorter than 16 characters counts as not configured and the routes it guards answer 503 (fail closed). Both are compared in constant time (`hmac.compare_digest`).

| Caller | Credential | Routes |
|---|---|---|
| Agent and voice worker (internal) | `X-Service-Token: $BACKEND_SERVICE_TOKEN` | open a Call and write its turns, tool calls, LLM calls, timings, Escalations, end; the clinic tools (availability, holds, appointments, FAQ) |
| Clinic staff | HTTP Basic `staff:$DASHBOARD_PASSWORD` | the dashboard and its API, `GET /calls/{id}` (transcript), `GET /escalations`, `GET /confirmation-messages`, `PUT /demo/clinic-override`, and any route not listed otherwise |
| Anyone | none | `GET /health`; `POST /capacity/admissions` and `.../settle` (the Caller-facing cost guard, as ticket 13 built it) |

No credentials: 401. Credentials of the other kind: 403 (the agent can write a transcript but never read one back; staff cannot call the agent routes). A route added without a decision is a staff route. `tests/test_auth.py` walks every route of the app and fails if one answers without credentials. `/docs` and `/openapi.json` are switched off. Residual risks: the capacity routes are open to anything that can reach the backend (it could use up the daily caps), so keep the backend port off the public internet and behind your reverse proxy; one shared service token means a leaked token is leaked for every agent process (rotate by changing the env var and restarting); HTTP Basic over plain HTTP sends the password in the clear, so serve the dashboard over HTTPS; there is no rate limit or lockout on password guesses, so use a long random password.

**Logs.** First-party code logs Call ids, counts and timings, never what was said. As a safety net, `backend/app/redaction.py` wraps the logging record factory, so every record of a process (ours, uvicorn, httpx, LiveKit; message, arguments and tracebacks) has phone numbers (7 to 15 digits) and known Patient names replaced by `[phone]` and `[name]`. The backend and the agent tell it every name they validate or are given; the voice worker, web service, agent service and eval command install it at start. It is also applied to transcripts in eval reports. Tested by capturing log output for a Call with a name and a phone number (`tests/test_log_redaction.py`). **Not covered:** the database, the trace and the dashboard keep everything in full on purpose; a name the process was never told is not recognised in free text, a number spoken as words is not a digit run, and the backend and agent keep separate name lists; `print()` and direct file writes bypass logging (the voice worker's JSONL turn log holds numbers only); redaction was exercised through the text-mode agent and the backend, not through a live LiveKit worker.

**What a human still has to check.** Nobody has looked at the dashboard in a browser: the tests parse the HTML and read its elements, so layout, readability and the browser's Basic-auth prompt are unseen.

## Provider spend caps checklist

Set these by hand before any code calls a provider. Tick each box once done and note the cap value. Caps are a second line of defence behind the in-code Call duration and daily minute limits.

- [x] **Anthropic** (confirmed in official docs): Claude Console, Billing page, Spend limits, "Set limit" or "Adjust limit". Cannot exceed the tier cap. Optionally put this project's key in its own Workspace with its own limit (not possible on the default Workspace). When reached, requests fail with HTTP 400 `invalid_request_error`. Cap: ____
- [x] **Deepgram:** Console, project billing settings. Official docs do not describe a hard cap. Look for a budget or usage-alert setting; if none exists, use a small prepaid balance with auto-recharge off. Cap: ____
- [x] **Cartesia:** dashboard billing. Official docs do not describe a hard cap. Use a small prepaid or credit plan with auto top-up off, or ask support@cartesia.ai. Cap: ____
- [x] **LiveKit** (only if using LiveKit Cloud): quotas are per project and overage is billed. No hard cap found in docs; set billing alerts and confirm with LiveKit support. Skip if self-hosting. Cap: ____
- [x] Each provider's API key is stored only in `.env` (never committed) and each has its own key for this project, so it can be revoked alone.

Where a provider has no hard cap, record what you did instead (prepaid balance, alert, support reply) next to the box.
