# Load test (ticket 20)

**Status: the stand-in load runs below were done. The real-provider load test (about 20 concurrent Calls through real Deepgram, Cartesia and Claude) has NOT been run, because it spends real money. There is no real latency-under-load number anywhere in this repository.** The ticket box for it stays unticked until a human runs the command in "Running the real-provider test" and pastes the result here.

## What the stand-in runs are, and are not

`python -m loadtest standin` runs concurrent Calls through the real stack with fake providers:

| Real | Stand-in |
|---|---|
| web `POST /token`, capacity admission and settlement, the cost guard's Postgres counters | ASR: a wait drawn from a log-normal around a configured median |
| the agent core (`TextCall`: state machine, guards, tools), over real HTTP to the backend | LLM: a scripted reply after a drawn wait per step (token counts are made up) |
| the backend, Postgres 17 (podman), connection pool | TTS: a wait drawn the same way |
| persistence of every turn, tool call, LLM call and turn timing; `voice.timing.TurnTimings` fed from this process's clock | the caller's audio, LiveKit and the voice worker are absent |

So a percentile in a stand-in report is **the configured stand-in latency plus this system's own overhead**. It says nothing about Deepgram, Cartesia or Claude, and nothing about how a real Call behaves under load. What the runs do tell you: admission behaviour at 20 simultaneous arrivals, whether the database and connection pool cope, whether anything errors or deadlocks, how many tool calls fail under slot contention, and how much time our own code adds per turn (the "our overhead" row: the agent core's wall time per turn minus the stand-in LLM waits, i.e. backend round trips for tools and trace writes) with 20 Calls at once versus one at a time.

Stand-in medians used (assumptions, not measurements; chosen in the region of the two-turn smoke run in `docs/latency-budget.md`, where a tool turn took about 3.4 s): ASR 120 ms, endpointing 500 ms, one LLM step 1200 ms (a tool turn takes two or more steps), TTS first audio 180 ms, log-normal sigma 0.35. A tool turn therefore lands around 3 to 4 s by construction, far over the 800 ms / 1500 ms targets, which is why the latency alerts fire in these reports. That is the configuration, not a finding.

## Running the stand-in test (free)

From the repository root with the voice extra installed (`uv sync --extra voice`; prefix commands with `uv run`). `loadtest/` is not in the Docker image.

```
podman exec <postgres-container> psql -U <user> -d postgres -c "create database receptio_load"
python -m loadtest standin --database-url postgresql+psycopg://<user>:<password>@localhost:<port>/receptio_load --calls 20
```

Options: `--contention same` (every booker wants the same slot), `--caps` (keep the daily caps on instead of test mode), `--one-ip` (all Calls from one address), `--asr-ms --endpointing-ms --llm-step-ms --tts-ms --sigma`, `--seed`, `--out report.md`. It starts the backend and the web service itself (uvicorn, one process each, throwaway credentials made for the run), writes Calls, Patients and Appointments into the database you name (use a throwaway one), and prints the report. The rate limits are lifted for these runs so they do not shape the result (they have their own tests).

## Running the real-provider test (a human; spends money; not run)

`python -m loadtest real` is a **dry run unless `--execute` is given**. This is its actual dry-run output (nothing was contacted), with the placeholder prices:

```
Real-provider load test: 20 concurrent Calls, 3 Caller turns each.
Worst-case cost estimate: $6.73 (Deepgram $1.00, Cartesia $1.68, Claude $4.05).
Assumed per Call: Deepgram for the full 300 s cap; 3 turns x 3 LLM steps of 6000 input and 300 output tokens; 500 agent characters per turn; 60 Caller characters per turn.
WARNING: prices are the PLACEHOLDER defaults, not provider prices. ...
Spend cap: $10 (hard ceiling $25).
DRY RUN: nothing was started and no provider was contacted.
To run for real: LOADTEST_REAL_SPEND_OK=yes python -m loadtest real --calls 20 --max-cost-usd 10 --execute
```

The estimate is a ceiling under stated assumptions (every Call runs to the 5-minute cap so Deepgram bills 5 minutes; three LLM steps of 6000 input tokens per turn; 500 characters of agent speech per turn). A typical run should cost well under it. **The figure uses placeholder prices until you set `LLM_PRICES_PER_MTOK`, `DEEPGRAM_USD_PER_MINUTE` and `CARTESIA_USD_PER_1K_CHARACTERS` from the providers' price lists**; do that first, rerun the dry run, and trust the new number.

Guards in the command (`loadtest/real.py`, tested with a spy in place of the launcher, so no test contacts a provider): at most 20 Calls; the estimate must be at or under `--max-cost-usd` (default 10) or it refuses; that cap cannot be set above 25 whatever the flag; `--execute` is refused unless `LOADTEST_REAL_SPEND_OK=yes` is also in the environment; the three provider keys are read from the environment only, never printed (a missing one is named); the provider spend caps in the README checklist are the independent second line.

Steps:

1. Confirm the provider spend caps (README, "Provider spend caps checklist") and set the three prices (above).
2. Bring the stack up with real keys in your local `.env`: `docker compose up --build` (LiveKit, the voice worker, the backend). Note the highest Call id: `curl -u staff:$DASHBOARD_PASSWORD localhost:8000/dashboard/api/calls?limit=1`.
3. Dry run, read the number: `python -m loadtest real --calls 20`.
4. Run: `LOADTEST_REAL_SPEND_OK=yes python -m loadtest real --calls 20 --max-cost-usd 10 --execute`. It starts one synthetic Caller per Call (`scripts/voice_smoke.py`, which itself uses Cartesia to synthesise the Caller's speech; included in the estimate), all at once, and waits. These Calls join LiveKit directly, so the cost guard's admission is not exercised in this mode.
5. Summarise what was stored: `python -m loadtest summarise --database-url <the backend's DATABASE_URL> --since-call-id <id from step 2>`, and `python -m backend.app.alerts --window-minutes 60`. Paste both here under a new heading with the date, machine, network, settings (`MIN_ENDPOINTING_DELAY`, `LLM_MODEL`), the sample size, and whether any Call errored; then tick the second box in `.scratch/receptio-v1/issues/20-rate-limiting-load-test-alerts.md`.

Not verified: `execute_with_voice_smoke` (the launcher) has never been run, since running it spends money. Its `scripts/voice_smoke.py` is the same script that produced the two-turn smoke numbers in `docs/latency-budget.md`, but 20 of them at once on one machine may be limited by that machine (audio synthesis and 20 LiveKit clients), which would then show up as client-side latency, not backend latency. If so, run the Callers from a second machine or in batches.

## Results

All three runs: 2026-10-31, one developer laptop (macOS, Apple silicon) running everything (backend, web, the load generator and Postgres in a podman VM), no other load except the run itself, Postgres 17.11, SQLAlchemy default pool (5 + 10 overflow), backend and web as one uvicorn process each. A different run of the same configuration varies: during development, repeated runs of Run A gave "our overhead" p50 between 59 and 211 ms depending on what else the laptop was doing. Read the overhead figures as order of magnitude, and the sample sizes (50 turns, 20 Calls) as small: p95 and p99 of 50 samples are the 3rd-highest and the highest values, not stable estimates.

Findings, valid for this laptop and these stand-ins only:

- **Admission.** 20 simultaneous token requests with the daily caps lifted: 20 admitted, no errors. With the caps on and all 20 from one address (Run C): exactly 10 admitted and 10 refused with `ip_limit` (the per-IP daily ceiling), none over. The token request (web, including the backend admission round trip) took 41 ms p50 and 59 ms p95 in Run A.
- **Database.** Zero deadlocks in all three runs, no connection-pool exhaustion (peak 15 connections, the pool's 5 + 10 ceiling, reached in all three runs; no request timed out waiting for one). Lock waits were seen only in Run C (peak 4 sessions waiting on a lock at once), the 20 arrivals updating the same cost-guard counter rows; they cleared at once. Hitting the pool ceiling means 20 concurrent Calls is close to what the default pool serves without queueing: a larger concurrency should raise it.
- **Correctness under contention.** Run B (every booker asks for the same slot): exactly one of the 15 bookers got it; the other 14 were told it was unavailable and none was double booked. The `tool_failure_rate` and `dropped_call_rate` alerts fired on that run (the report's alert line evaluates all Calls stored in the database, including the 5 baseline Calls, so its rates are 42% and 56%, against 52% of the loaded Calls' tool calls), which is the alert doing its job on real persisted data.
- **Our overhead per turn** (agent core time minus stand-in waits, so backend round trips for tools and trace writes): p50 59 ms and p95 93 ms with 20 Calls at once (Run A), against 77 ms and 112 ms for Calls one at a time on the same run. No sign of our own code getting slower under 20 concurrent Calls on this machine. This says nothing about what real provider latency does under load.
- **Errors in the virtual Calls:** 0 in all runs. Failed tool calls in Run A (4 of 80) are real slot collisions between different Service/day combinations that overlap on the same Provider.

The reports as generated follow.


### Run A: 20 concurrent Calls, spread slots, daily caps lifted

**STAND-IN RUN.** ASR, LLM and TTS were waits drawn from the configured distribution below, not Deepgram, Cartesia or Claude. The web token endpoint, capacity admission, the agent core, the backend, Postgres and the persisted turn timings were real. Every latency in this report is the configured stand-in plus this system's own overhead, so it makes no claim about real provider latency or about latency of a real Call under load. The real-provider run has not been performed.

Conditions:

- Date: 2026-10-31
- Machine: Darwin arm64, all processes on one host
- Database: Postgres 17.11 (Debian 17.11-1.pgdg13+2), SQLAlchemy pool defaults (5 + 10 overflow)
- Servers: backend and web under uvicorn, one process each, over real HTTP on loopback
- Cost guard: CAPS_TEST_MODE=true (daily caps lifted)
- Rate limits: lifted (1,000,000 per minute) so they do not shape this run
- Concurrent Calls: 20 (after 5 baseline Calls run one at a time)
- Contention: spread
- Caller addresses: a distinct address per Call
- Seed: 1
- Stand-in medians (ms): ASR 120, endpointing 500, LLM step 1200 (a tool turn takes two or more), TTS first audio 180; log-normal sigma 0.35. Assumed, not measured.

Admission (web `/token` through the backend's cost guard):

- admitted: 20

Per-turn timing, read back from the persisted `call_turn_timings` rows (nearest-rank percentiles):

| Measure (ms) | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| load: total_from_speech_end_ms | 50 | 3716 | 7547 | 8088 | 8088 |
| load: asr_final_ms | 50 | 114 | 223 | 250 | 250 |
| load: llm_first_token_ms | 50 | 3308 | 7189 | 7803 | 7803 |
| load: tts_first_audio_ms | 50 | 169 | 372 | 450 | 450 |
| load: total_ms | 50 | 3550 | 7306 | 8014 | 8014 |
| Calls alone, one at a time: total_from_speech_end_ms | 13 | 3811 | 5390 | 5390 | 5390 |
| load: our overhead per turn (core time minus stand-in LLM waits) | 50 | 59 | 93 | 103 | 103 |
| Calls alone: our overhead per turn (core time minus stand-in LLM waits) | 13 | 77 | 112 | 112 | 112 |
| token request (web, incl. admission) | 20 | 41 | 59 | 59 | 59 |

Calls stored: 20; outcomes: {'completed': 16, 'dropped': 4}; turn timings stored: 50 (interrupted: 0).
Tool calls: 80; failed (not refused): 12 {'confirm_appointment': 4, 'place_hold': 4, 'read_back': 4}; refused by the state machine: 0.
Stand-in LLM script fallbacks (script no longer fitted the Call): 4.
Postgres: peak 15 connections, peak 0 sessions waiting on a lock (133 samples), deadlocks 0, rollbacks 355, commits 1266.
Errors in the virtual Calls: 0.
Alerts evaluated over the persisted data: latency_p95: p95 of speech end to first agent audio is 5977 ms (limit 1500 ms); latency_p50: p50 of speech end to first agent audio is 3763 ms (limit 800 ms)

### Run B: 20 concurrent Calls all wanting the same slots, daily caps lifted

**STAND-IN RUN.** ASR, LLM and TTS were waits drawn from the configured distribution below, not Deepgram, Cartesia or Claude. The web token endpoint, capacity admission, the agent core, the backend, Postgres and the persisted turn timings were real. Every latency in this report is the configured stand-in plus this system's own overhead, so it makes no claim about real provider latency or about latency of a real Call under load. The real-provider run has not been performed.

Conditions:

- Date: 2026-10-31
- Machine: Darwin arm64, all processes on one host
- Database: Postgres 17.11 (Debian 17.11-1.pgdg13+2), SQLAlchemy pool defaults (5 + 10 overflow)
- Servers: backend and web under uvicorn, one process each, over real HTTP on loopback
- Cost guard: CAPS_TEST_MODE=true (daily caps lifted)
- Rate limits: lifted (1,000,000 per minute) so they do not shape this run
- Concurrent Calls: 20 (after 5 baseline Calls run one at a time)
- Contention: same
- Caller addresses: a distinct address per Call
- Seed: 1
- Stand-in medians (ms): ASR 120, endpointing 500, LLM step 1200 (a tool turn takes two or more), TTS first audio 180; log-normal sigma 0.35. Assumed, not measured.

Admission (web `/token` through the backend's cost guard):

- admitted: 20

Per-turn timing, read back from the persisted `call_turn_timings` rows (nearest-rank percentiles):

| Measure (ms) | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| load: total_from_speech_end_ms | 50 | 3330 | 8057 | 9305 | 9305 |
| load: asr_final_ms | 50 | 113 | 206 | 246 | 246 |
| load: llm_first_token_ms | 50 | 3030 | 7781 | 9013 | 9013 |
| load: tts_first_audio_ms | 50 | 173 | 371 | 449 | 449 |
| load: total_ms | 50 | 3254 | 7988 | 9212 | 9212 |
| Calls alone, one at a time: total_from_speech_end_ms | 13 | 3822 | 5401 | 5401 | 5401 |
| load: our overhead per turn (core time minus stand-in LLM waits) | 50 | 52 | 94 | 105 | 105 |
| Calls alone: our overhead per turn (core time minus stand-in LLM waits) | 13 | 79 | 125 | 125 | 125 |
| token request (web, incl. admission) | 20 | 62 | 67 | 70 | 70 |

Calls stored: 20; outcomes: {'completed': 6, 'dropped': 14}; turn timings stored: 50 (interrupted: 0).
Tool calls: 80; failed (not refused): 42 {'confirm_appointment': 14, 'place_hold': 14, 'read_back': 14}; refused by the state machine: 0.
Stand-in LLM script fallbacks (script no longer fitted the Call): 14.
Postgres: peak 15 connections, peak 0 sessions waiting on a lock (140 samples), deadlocks 0, rollbacks 383, commits 1238.
Errors in the virtual Calls: 0.
Alerts evaluated over the persisted data: latency_p95: p95 of speech end to first agent audio is 7563 ms (limit 1500 ms); latency_p50: p50 of speech end to first agent audio is 3518 ms (limit 800 ms); tool_failure_rate: 42% of tool calls failed (limit 20%); worst: read_back; dropped_call_rate: 56% of Calls ended dropped (limit 50%)

### Run C: 20 concurrent Calls from ONE address, daily caps ON

**STAND-IN RUN.** ASR, LLM and TTS were waits drawn from the configured distribution below, not Deepgram, Cartesia or Claude. The web token endpoint, capacity admission, the agent core, the backend, Postgres and the persisted turn timings were real. Every latency in this report is the configured stand-in plus this system's own overhead, so it makes no claim about real provider latency or about latency of a real Call under load. The real-provider run has not been performed.

Conditions:

- Date: 2026-10-31
- Machine: Darwin arm64, all processes on one host
- Database: Postgres 17.11 (Debian 17.11-1.pgdg13+2), SQLAlchemy pool defaults (5 + 10 overflow)
- Servers: backend and web under uvicorn, one process each, over real HTTP on loopback
- Cost guard: daily caps ON
- Rate limits: lifted (1,000,000 per minute) so they do not shape this run
- Concurrent Calls: 20 (after 5 baseline Calls run one at a time)
- Contention: spread
- Caller addresses: one shared address
- Seed: 1
- Stand-in medians (ms): ASR 120, endpointing 500, LLM step 1200 (a tool turn takes two or more), TTS first audio 180; log-normal sigma 0.35. Assumed, not measured.

Admission (web `/token` through the backend's cost guard):

- admitted: 10
- refused:ip_limit: 10

Per-turn timing, read back from the persisted `call_turn_timings` rows (nearest-rank percentiles):

| Measure (ms) | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| load: total_from_speech_end_ms | 28 | 3430 | 7565 | 8076 | 8076 |
| load: asr_final_ms | 28 | 113 | 198 | 207 | 207 |
| load: llm_first_token_ms | 28 | 3112 | 7307 | 7792 | 7792 |
| load: tts_first_audio_ms | 28 | 175 | 339 | 438 | 438 |
| load: total_ms | 28 | 3322 | 7470 | 8004 | 8004 |
| Calls alone, one at a time: total_from_speech_end_ms | 13 | 3830 | 5369 | 5369 | 5369 |
| load: our overhead per turn (core time minus stand-in LLM waits) | 28 | 65 | 90 | 92 | 92 |
| Calls alone: our overhead per turn (core time minus stand-in LLM waits) | 13 | 75 | 91 | 91 | 91 |
| token request (web, incl. admission) | 20 | 102 | 112 | 114 | 114 |

Calls stored: 10; outcomes: {'completed': 9, 'dropped': 1}; turn timings stored: 28 (interrupted: 0).
Tool calls: 46; failed (not refused): 3 {'confirm_appointment': 1, 'place_hold': 1, 'read_back': 1}; refused by the state machine: 0.
Stand-in LLM script fallbacks (script no longer fitted the Call): 1.
Postgres: peak 15 connections, peak 4 sessions waiting on a lock (133 samples), deadlocks 0, rollbacks 263, commits 732.
Errors in the virtual Calls: 0.
Alerts evaluated over the persisted data: latency_p95: p95 of speech end to first agent audio is 5369 ms (limit 1500 ms); latency_p50: p50 of speech end to first agent audio is 3739 ms (limit 800 ms)
