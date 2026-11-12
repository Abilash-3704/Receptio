# Secrets handling and exposure review (ticket 20)

Reviewed 2026-10-31 on the ticket 20 branch, which includes ticket 18 (authentication, PII redaction) and tickets 02-15 and 17. Only what was actually checked is listed, with how. "Not checked" at the end says what was left out. Nothing here claims a deployed environment is safe: no deployment exists yet (ticket 21).

## What was checked, and the result

### 1. Secrets in tracked files and history

- `git grep` over every tracked file (excluding `uv.lock`, which holds package hashes, and `.agents/`, which is skill text) for provider-key shapes (`sk-...`, `AKIA...`, `xox*-`, `ghp_...`, PEM private-key headers) and for `*API_KEY|SECRET|TOKEN|PASSWORD` assignments with a value of 12 or more characters. **No hits** outside `tests/`, where the credentials are fixed fake strings (`tests/support.py`).
- Tracked files with `env` in the name: only `.env.example`. Its secret slots hold `CHANGE-ME`, which the backend treats as not configured (shorter than `MIN_SECRET_CHARS` = 16), so a copied file does not accidentally authenticate anyone. The provider-key lines are commented out.
- History: `git log --all --diff-filter=A` finds no `.env`, `*.pem`, `*.key` or `*.p12` ever added, and `git log -S"BEGIN PRIVATE KEY"` is empty.
- Result: no secret is committed. This is a pattern scan, not a guarantee against a secret in an unusual shape.

### 2. `.env` handling

- `.gitignore` ignores `.env`, `.env.*` (except `.env.example`), `.scratch/spend-caps.env` (the spend-cap wizard's output) and `logs/` (raw turn timings).
- `.dockerignore` excluded `.env` but not `.env.*`; fixed in this ticket (`.env.*` added, `.env.example` kept). It was not exploitable: the `Dockerfile` copies only `agent/`, `backend/` and `voice/`, so no `.env*` ever entered an image.
- Provider keys are `SecretStr` in settings (`agent/config.py`, `voice/config.py`) and `.get_secret_value()` is called only where a secret is used: the provider client constructors (`agent/anthropic_llm.py`, `voice/worker.py`: Anthropic, Deepgram, Cartesia), the service-token header (`agent/backend_client.py`) and the length check in `backend/app/config.py` (checked with grep). In `docker-compose.yml` the Deepgram, Cartesia and Anthropic keys are given to the `voice-agent` service only, not to `backend`, `web` or `agent`. (`ANTHROPIC_API_KEY` is read by the agent core inside the voice worker.)

### 3. Development credentials, and the deployed guard

| Default | Where | Status |
|---|---|---|
| Postgres password `receptio` (user `receptio`) | `docker-compose.yml`, `.env.example`, `BackendSettings.database_url`, CI | **Dev only.** With `DEPLOYED_ENVIRONMENT=true` the backend refuses to start on a URL whose password is empty or one of `receptio, postgres, password, admin, secret, changeme`, or whose user is `postgres` or missing (`backend/app/config.py`). Tested in `tests/test_deployed_config.py`. This is a list of known-bad values, not a strength check. |
| LiveKit `devkey` / `secret` | `docker-compose.yml` (`${LIVEKIT_API_KEY:-devkey}`), `.env.example`, `deploy/livekit.yaml`, `VoiceSettings` defaults | **Dev only.** With `DEPLOYED_ENVIRONMENT=true` the web service and the voice worker (both build `VoiceSettings` at start-up) refuse to start on `devkey`, `secret`, an empty value, or a secret under 32 characters (`voice/config.py`). Tested. The compose file now passes `DEPLOYED_ENVIRONMENT` to both. |
| LiveKit server started with `--dev` | `docker-compose.yml` | **Dev only, not enforced by code.** `--dev` makes the server accept `devkey`/`secret`. Our services refuse those keys when deployed, so a deployed stack with `--dev` fails closed (tokens do not verify), but a deployment must also drop `--dev`; that is a ticket 21 item. |
| `CAPS_TEST_MODE` | `BackendSettings` | Off by default; refused with `DEPLOYED_ENVIRONMENT=true` (ticket 13). |
| `DEMO_CONTROLS_ENABLED` | `BackendSettings` | Off by default; now also refused with `DEPLOYED_ENVIRONMENT=true` (it exposes `PUT /demo/clinic-override`). |
| `BACKEND_SERVICE_TOKEN`, `DASHBOARD_PASSWORD` | ticket 18 | No default; unset or under 16 characters means the protected routes answer 503 (fail closed). |

The guard is a flag the operator must set: nothing stops a person who controls the environment from leaving `DEPLOYED_ENVIRONMENT` unset. It protects against mistakes, not against the operator.

### 4. Logs

- Every `logger.*` call in `agent/`, `backend/` and `voice/` (listed with `git grep`) logs Call ids, counts, states and timings; none logs transcript text, names, phone numbers or keys. The worker logs the length of an ASR transcript, never its text (`voice/worker.py`). Turn-timing lines hold numbers only. Alert lines (`backend/app/alerts.py`) hold rule names, counts, rates and tool names.
- Ticket 18's `backend/app/redaction.py` is a safety net on top: a record factory that scrubs phone-number digit runs and names the process was told about, from every logger including uvicorn's. Its own docstring lists what it does not cover (names it was not told, phone numbers spoken as words, `print`, the database).
- uvicorn's access log records method, path and status. The paths carry ids and rooms, no names or numbers; request bodies are not logged by any code here.
- The cost guard stores only SHA-256 digests of the IP and browser id (`backend/app/capacity.py`). The new in-process limiters hold the client IP in clear in memory while it is inside a window (the token and admission limiters, and the authentication-failure limiter); the identification throttle holds digests only. None of it is written anywhere.

### 5. CORS and cross-origin

- No `CORSMiddleware` and no `allow_origins` anywhere in `agent/`, `backend/` or `voice/` (grep). The page is served by the web service and calls `/token` on its own origin; the browser never calls the backend. So no cross-origin read access is granted. This was not tested with a browser.
- The visitor cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` only when the request URL scheme is https (`voice/web.py`). **Caveat from reading the code:** behind a TLS-terminating proxy the web service sees `http` unless uvicorn is told to honour the proxy's scheme, in which case the cookie is not marked `Secure`. Deployment (ticket 21) must pass `--proxy-headers` and `--forwarded-allow-ips` for the proxy, or set it explicitly.

### 6. Endpoints that answer without credentials

Backend, enumerated from the running app's route table and `backend/app/auth.py` (and enforced by `tests/test_auth.py`, which fails when a route answers without credentials):

| Route | Why open | Residual risk |
|---|---|---|
| `GET /health` | liveness, no data | none |
| `POST /capacity/admissions` | the public token endpoint calls it for a visitor with no credentials | Anyone who can reach the backend port can post admissions with invented IPs and use up the global daily budget (24 Calls of reserved time a day). This is denial of service on the demo, not provider spend. **The backend port must not be public**; compose currently publishes it on all interfaces for local use. Per-IP rate limiting (below) bounds one address, not many. |

Web service (`voice/web.py`): `GET /`, `GET /health` and `POST /token` are public by design. `/token` starts a Call that spends provider money, so it sits behind the cost guard and now a per-IP rate limit. `/docs`, `/redoc` and `/openapi.json` are disabled on the backend (ticket 18); the web service and the agent service are plain `FastAPI(...)` apps (`voice/web.py:57`, `agent/main.py:19`), so they still serve FastAPI's default `/docs`, `/redoc` and `/openapi.json`; those list only their own few routes, but a deployment may want them off. Agent service (`agent/main.py`): `GET /health` and `POST /text-calls/tracer`, which opens and ends a text Call against the backend with the service token and spends nothing; it is published on port 8001 in compose and must not be public.

Review fixes after the code review of the integration branch:

- **Settlement is a service route.** `POST /capacity/admissions/{room}/settle` used to be public and refunded the global cap by a client-supplied `used_seconds`, so anyone who could reach the backend could keep the 120 minutes a day from filling. It now needs the service token (the voice worker has one), and the backend also checks the claim against its own clock: a Call is charged at least the time since its admission (`call_admissions.admitted_at`, migration 0007) and at most what it reserved. Admissions made before the migration have no time and settle on the claimed length alone, under the service token.
- **Releasing a Hold.** `POST /holds/{id}/release` is a service route (the token is the first check). A Hold for a new booking has no owner yet, so the service token is the whole rule. A reschedule Hold belongs to the Patient of the Appointment it replaces, so releasing it needs that Patient's name and phone in the body, as read-back and confirm already do: a missing or wrong identity gets the same uniform 403 and counts against the identification throttle, and the Hold is left untouched. An unknown Hold id is a 404 (as before), and ids outside the 4-byte range are a 422.
- **Failed-authentication throttle behind a proxy.** The throttle is keyed on the client address. With `TRUSTED_PROXY_HOPS=0` behind a proxy every client shares the proxy's address, so one client's failures can lock out the others. The throttle was not weakened (a client-supplied header must never choose the address, and a locked address is refused even with correct credentials, otherwise the lockout would be a password oracle); instead the backend logs one warning, naming the fix, the first time it sees `X-Forwarded-For` on a failed authentication while `TRUSTED_PROXY_HOPS=0`. Set `TRUSTED_PROXY_HOPS` to the number of proxies you run.
- **Configuration faults are loud.** Compose refuses to start `backend`, `agent` and `voice-agent` while `BACKEND_SERVICE_TOKEN` is unset, and the voice worker logs one line (503: the backend has no usable token; 401 or 403: tokens differ) instead of a traceback. A token shorter than 16 characters (such as the `CHANGE-ME` in `.env.example`) passes compose but is treated as unset by the backend, which answers 503, so the same worker line appears.

### 7. What this ticket added

- Per-IP rate limit on `POST /token` (default 10 a minute) and on capacity admission (default 30 a minute per IP), before the cost guard is asked, so a refused request touches none of ticket 13's counters (tested, including the visitor allowance, the global budget and that the guard is never called).
- Throttling of failed identification attempts at `/appointments/lookup`, `read`, `cancel`, `reschedule` and the read-back and confirm of a reschedule Hold, closing the name+phone brute-force path from the earlier audit: failed attempts per name, per phone and overall lock further attempts out for a window (defaults 5, 5 and 30 per 10 minutes). Successes never count. Cost: someone who knows a Patient's name can lock that name out for the window by failing on purpose.
- Throttling of failed authentication (wrong staff password or service token) per client address (default 10 per 10 minutes); a locked address is refused before its credentials are compared.
- Deployed-environment refusals of the development credentials and demo controls (section 3).

## Limits and findings to carry forward

1. **In-process limiters.** State is per process and lost on restart; with several replicas the effective limit is the configured one times the replica count. Fine for the single-instance demo; use a shared store before scaling out.
2. **The backend sees the agent, not the Caller.** Identification throttling therefore cannot be per Caller address; it is per name, per phone and overall. A distributed guesser is bounded by the overall bucket, at the price that guessing can lock identification for everyone for the window (a Caller then gets a Callback Request). Staff-password guessing from many addresses is bounded only by the password's length (16-character minimum), not by the per-IP throttle.
3. **`TRUSTED_PROXY_HOPS` applies to the backend too** (new `trusted_proxy_hops` setting, same meaning as on the web service; default 0 uses the socket address). Behind a proxy, set it on both services or all authentication failures will be counted against the proxy's address (the backend logs a warning when it sees that).
4. **Backend and agent ports are published on all interfaces in `docker-compose.yml`.** Fine for a laptop; a deployment must publish only the web service and LiveKit.
5. **Names and numbers are in the database in full** by design (ticket 17/18); the dashboard that shows them is behind the staff password.

## Not checked

Dependency vulnerability scanning; container image contents and base-image patches; TLS and security headers on a real deployment (none exists); the LiveKit server's own configuration beyond `deploy/livekit.yaml`; the provider dashboards' spend caps (`scripts/set-spend-caps.sh` exists, its effect was not verified here); Postgres roles and network exposure of a deployed database; backups; whether `.env` files exist on any developer machine; the browser behaviour of the cookie flags.
