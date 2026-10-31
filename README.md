# Receptio

An AI voice receptionist for a fictional dental clinic in Bengaluru. Callers reach it through a web client, never a real phone number. See `GLOSSARY.md` for vocabulary, `docs/architecture.md` for decisions and `docs/metrics.md` for targets.

> Status: a Caller can book an Appointment in a text-mode Call (scripted LLM in tests). Voice pipeline arrives in later tickets.

## Quickstart

```
cp .env.example .env
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

The voice and LLM SDKs are optional extras (`--extra voice`, `--extra llm`, mutually exclusive because `livekit-plugins-anthropic` 1.8.10 needs `anthropic<2`). LiveKit is not in Compose yet (TODO, ticket 12).

### Text-mode booking and scenarios

The agent core (`agent/`) never imports LiveKit or the Anthropic SDK. The LLM sits behind `agent/llm.py`: tests and CI use `agent/scripted_llm.py` (deterministic, no network, no keys). The real model, `agent/anthropic_llm.py`, needs `uv sync --extra llm`, `ANTHROPIC_API_KEY`, and `LLM_MODEL` / `LLM_MODEL_FAST`; it is not run in CI.

```
uv run pytest tests/test_scenario_runner.py     # runs evals/scenarios/*.yaml with the scripted LLM
uv run pytest tests/test_safety_scenarios.py -s # Emergency / Urgent Case recall and false positives
```

The safety layer (Emergency and Urgent Case detector, fixed 112 script) and what it cannot cover are in `docs/safety.md`.

Friction: the `llm` extra pulls in `httpx2`, which makes Starlette's `TestClient` subclass `httpx2.Client` instead of `httpx.Client`. Tests cast it back (see `tests/conftest.py`); the agent's backend code does not depend on httpx exception classes for that reason.

## Provider spend caps checklist

Set these by hand before any code calls a provider. Tick each box once done and note the cap value. Caps are a second line of defence behind the in-code Call duration and daily minute limits.

- [x] **Anthropic** (confirmed in official docs): Claude Console, Billing page, Spend limits, "Set limit" or "Adjust limit". Cannot exceed the tier cap. Optionally put this project's key in its own Workspace with its own limit (not possible on the default Workspace). When reached, requests fail with HTTP 400 `invalid_request_error`. Cap: ____
- [x] **Deepgram:** Console, project billing settings. Official docs do not describe a hard cap. Look for a budget or usage-alert setting; if none exists, use a small prepaid balance with auto-recharge off. Cap: ____
- [x] **Cartesia:** dashboard billing. Official docs do not describe a hard cap. Use a small prepaid or credit plan with auto top-up off, or ask support@cartesia.ai. Cap: ____
- [x] **LiveKit** (only if using LiveKit Cloud): quotas are per project and overage is billed. No hard cap found in docs; set billing alerts and confirm with LiveKit support. Skip if self-hosting. Cap: ____
- [x] Each provider's API key is stored only in `.env` (never committed) and each has its own key for this project, so it can be revoked alone.

Where a provider has no hard cap, record what you did instead (prepaid balance, alert, support reply) next to the box.
