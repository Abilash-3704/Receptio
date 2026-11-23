# Receptio

An AI voice receptionist for a fictional dental clinic in Bengaluru. Callers talk to it from a web page, never a real phone number. It answers questions, books, reschedules and cancels appointments, and hands off to staff when it should.

**Live demo: https://receptio-demo.duckdns.org** — press Start call, allow the microphone (Chrome or Firefox), and try "I'd like to book a check-up tomorrow morning."
The demo is capped: 5 minutes per call, 3 calls per visitor per day, and a daily minute budget for everyone.

## What makes it interesting

- **Correctness over charm.** The LLM never decides what is true. A state machine and code-checked tools own availability, holds and read-back, and a guard blocks any time or price the tools did not return. Wrong-time rate must stay at 0 in CI.
- **Safety first.** A rule-based detector runs before the LLM and answers medical emergencies with a fixed 112 script. Its limits are documented, not hidden (`docs/safety.md`).
- **Evals as a CI gate.** 114 scenarios (including 12 known-gap probes) run deterministically on every push and fail the build on regression (a regression gate, not proof of real-model quality). An LLM-simulated Caller (4 personas) is available for real-model runs, with hard spend caps.
- **Built for failure.** Dropped calls, ASR doubts, tool failures and recording objections each have a defined path (`docs/failure-analysis.md`).
- **Cost and abuse controlled.** Per-call time limit, per-visitor and global daily caps enforced atomically in Postgres, rate limits, and a staff dashboard with traces, latency and cost per call.
- **Honest about what is unproven.** Each doc says what was measured and what was not.

## Architecture

```
Browser ──WebRTC──> LiveKit ──> voice worker: VAD → Deepgram ASR → agent core (Claude) → Cartesia TTS
                                                     │
                                     FastAPI backend + Postgres (calls, appointments, caps, dashboard)
```

The agent core (`agent/`) is plain Python with no LiveKit or Anthropic imports, so the same code runs in text mode for tests and evals. Decisions and trade-offs: `docs/architecture.md`.

## Run it

```
cp .env.example .env     # set BACKEND_SERVICE_TOKEN and DASHBOARD_PASSWORD
docker compose up --build
curl -X POST localhost:8001/text-calls/tracer    # text-mode call, no provider keys needed
```

For voice, add provider keys to `.env` and open http://localhost:8002. Tests and evals:

```
uv sync
uv run pytest
uv run python -m evals --check-baseline
```

## Docs

| | |
|---|---|
| `docs/development.md` | Full reference: testing, evals, voice mode, cost guard, dashboard, auth, rate limits |
| `docs/architecture.md` | Stack decisions and why |
| `docs/eval-report.md`, `docs/metrics.md` | Eval method, targets, known gaps |
| `docs/safety.md`, `docs/security-review.md` | Emergency handling, secrets and threat review |
| `docs/deploy.md` | Single-VM production deployment |
| `GLOSSARY.md` | Domain vocabulary |

