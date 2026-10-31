# Architecture decisions (Phase 0)

Decided 2026-10-31. Vocabulary follows `GLOSSARY.md`; ADR-0001 applies throughout. Versions were checked against PyPI and official docs on the decision date and must be pinned in the lockfile (see "Pinned versions"). Re-check docs before first use of each SDK.

## Decisions

### Real-time framework: LiveKit Agents

**Why:** it owns the parts that are expensive to get right and not what this project is about to prove: WebRTC transport, a browser client SDK, voice-activity detection, turn detection and interruption (barge-in) handling, and a plugin per provider. That puts the first working voice loop and its per-turn timing within reach of the voice-loop ticket.

**Rejected: Pipecat.** More explicit and provider-agnostic, but needs more hand-wiring for the browser client and barge-in. More control is not what this project's differentiators (evals, correctness, failure handling) need.

**Consequence to manage:** LiveKit Cloud or a self-hosted LiveKit server is needed. Self-hosting inside Docker Compose keeps "`docker compose up` starts everything" true and avoids a cloud spend line. Decide in the skeleton ticket; default is self-hosted in Compose, with Cloud as a fallback.

The agent core (call state machine, tools, safety layer) is kept independent of the framework so the text-mode seam can drive it without LiveKit. The framework only touches the voice pipeline module.

### Speech recognition: Deepgram (streaming)

Low-latency streaming ASR with interim and final results and confidence scores, which the ASR low-confidence flow needs. Used through `livekit-plugins-deepgram`.

### LLM: Claude, model name from environment

Tool calling is central. The model is never hard-coded: `LLM_MODEL` (main turns) and `LLM_MODEL_FAST` (simple turns) are read from config.

Starting values, to be tuned against latency and eval results:

| Setting | Starting value | Reason |
|---|---|---|
| `LLM_MODEL` | `claude-sonnet-5-5` | Reliable tool use for scheduling turns |
| `LLM_MODEL_FAST` | `claude-haiku-5-5` | Cheaper and faster for FAQ and simple turns |

Every model change is recorded in `CHANGELOG.md` with its eval result. Used through `livekit-plugins-anthropic`; the `anthropic` SDK is used directly for the simulated Caller and any judge.

### Speech synthesis: Cartesia (streaming)

Streaming TTS with low time-to-first-audio, which is what the latency target is most sensitive to. Used through `livekit-plugins-cartesia`. ElevenLabs was the alternative: better voices, usually higher latency and cost.

### Backend, state, storage

As in `PROJECT.md`: FastAPI, Postgres, SQLAlchemy and Alembic, Redis for call state, idempotency keys and rate limiting. No change.

## Python tooling

| Concern | Choice |
|---|---|
| Python | 3.12 (`livekit-agents` requires `>=3.10,<3.15`) |
| Environment and lockfile | uv (`uv.lock` committed) |
| Formatter and linter | ruff (`ruff format`, `ruff check`) |
| Type checker | pyright, strict on `agent/` and `backend/` |
| Tests | pytest |
| Hooks | pre-commit running ruff and pyright |

Rejected: mypy (slower, no advantage here) and poetry/black/flake8 (more tools to maintain).

## Pinned versions

Latest stable on PyPI on 2026-10-31. Pin exactly in `pyproject.toml`/`uv.lock` when the skeleton ticket creates them.

| Package | Version |
|---|---|
| livekit-agents | 1.8.10 |
| livekit-plugins-deepgram | 1.8.10 |
| livekit-plugins-cartesia | 1.8.10 |
| livekit-plugins-anthropic | 1.8.10 |
| livekit-plugins-silero | 1.8.10 |
| anthropic | 2.3.0 |
| pytest | 9.2.0 |
| ruff | 0.17.3 |
| pyright | 1.1.414 |

LiveKit plugins must share one version with `livekit-agents`. `deepgram-sdk` (7.16.0) is not needed directly while the LiveKit plugin is used.

## Cost and safety defaults

Values come from the spec: maximum Call duration 5 minutes (warning at 4 minutes 30 seconds), 3 Calls per visitor per day, 120 minutes per day globally. All are configuration. Provider spend caps are a second, independent line of defence and are tracked in the README checklist.

## Open items

- LiveKit server: self-hosted in Compose versus Cloud. Decide in the skeleton ticket.
- Hard spend caps for Deepgram, Cartesia and LiveKit are unconfirmed in official docs; see the README checklist.
