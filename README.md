# Receptio

An AI voice receptionist for a fictional dental clinic in Bengaluru. Callers reach it through a web client, never a real phone number. See `GLOSSARY.md` for vocabulary, `docs/architecture.md` for decisions and `docs/metrics.md` for targets.

> Status: skeleton and a tracer text-mode Call. Voice pipeline arrives in later tickets.

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

The voice and LLM SDKs are optional extras (`--extra voice`, `--extra llm`, mutually exclusive because `livekit-plugins-anthropic` 1.8.10 needs `anthropic<2`). LiveKit is not in Compose yet (TODO, ticket 12).

## Provider spend caps checklist

Set these by hand before any code calls a provider. Tick each box once done and note the cap value. Caps are a second line of defence behind the in-code Call duration and daily minute limits.

- [x] **Anthropic** (confirmed in official docs): Claude Console, Billing page, Spend limits, "Set limit" or "Adjust limit". Cannot exceed the tier cap. Optionally put this project's key in its own Workspace with its own limit (not possible on the default Workspace). When reached, requests fail with HTTP 400 `invalid_request_error`. Cap: ____
- [x] **Deepgram:** Console, project billing settings. Official docs do not describe a hard cap. Look for a budget or usage-alert setting; if none exists, use a small prepaid balance with auto-recharge off. Cap: ____
- [x] **Cartesia:** dashboard billing. Official docs do not describe a hard cap. Use a small prepaid or credit plan with auto top-up off, or ask support@cartesia.ai. Cap: ____
- [x] **LiveKit** (only if using LiveKit Cloud): quotas are per project and overage is billed. No hard cap found in docs; set billing alerts and confirm with LiveKit support. Skip if self-hosting. Cap: ____
- [x] Each provider's API key is stored only in `.env` (never committed) and each has its own key for this project, so it can be revoked alone.

Where a provider has no hard cap, record what you did instead (prepaid balance, alert, support reply) next to the box.
