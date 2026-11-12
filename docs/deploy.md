# Deploying the demo on one VM

One small Linux VM runs everything with `docker-compose.prod.yml`. LiveKit Cloud carries the call
audio (no self-hosted LiveKit, no UDP ports to open), and Caddy provides HTTPS, which browsers
require for the microphone.

```
Browser --HTTPS--> Caddy --> web :8002 (page, /token)        STAFF_DOMAIN --> backend :8000 (dashboard)
Browser <--audio--> LiveKit Cloud <--> voice-agent (worker) --> agent core --> backend --> Postgres
```

Only Caddy publishes ports (80, 443). The worker must be running for a Call to be answered: it
connects out to LiveKit and waits for rooms.

## 1. Accounts and keys

- **LiveKit Cloud**: create a project; note the `wss://<project>.livekit.cloud` URL, API key and secret.
- **Deepgram, Cartesia, Anthropic**: create API keys. Set a spending limit on each; the demo is public.

## 2. The VM

1. Create an Ubuntu 24.04 VM (about 2 GB RAM) with your SSH key.
2. Firewall: allow 22, 80 and 443 only.
3. Point two names at the VM's IP (A records, or free DuckDNS subdomains): `DOMAIN` for the call
   page and `STAFF_DOMAIN` for the dashboard. Check with `ping <name>`.
4. `ssh root@<ip>`, then `curl -fsSL https://get.docker.com | sh`.

## 3. Configure and start

```
git clone <repo-url> receptio && cd receptio
cp .env.example .env
nano .env
docker compose -f docker-compose.prod.yml up -d --build
```

Set in `.env` (`openssl rand -hex 24` makes a good secret). Compose refuses to start if a required
one is missing:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | strong password (not `receptio`) |
| `BACKEND_SERVICE_TOKEN`, `DASHBOARD_PASSWORD` | random secrets |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | from LiveKit Cloud (secret at least 32 characters) |
| `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `ANTHROPIC_API_KEY` | provider keys |
| `DOMAIN`, `STAFF_DOMAIN` | the two host names |

Optional: `GLOBAL_MINUTES_PER_DAY` (default 120), `VISITOR_CALLS_PER_DAY` (3), `CALL_MAX_SECONDS`
(300), and the price settings in `.env.example`. The prod file forces `DEPLOYED_ENVIRONMENT=true`,
`CAPS_TEST_MODE=false` and `DEMO_CONTROLS_ENABLED=false`.

## 4. Check

```
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f voice-agent   # registered with LiveKit
```

Open `https://<DOMAIN>`, press Start call, allow the microphone. The dashboard is
`https://<STAFF_DOMAIN>/dashboard` (user `staff`, password `DASHBOARD_PASSWORD`).

## Operating it

- Update: `git pull` and re-run the `up -d --build` command.
- Logs: `docker compose -f docker-compose.prod.yml logs -f <service>`.
- Backup: `docker compose -f docker-compose.prod.yml exec db pg_dump -U receptio receptio > backup.sql`.

## Troubleshooting

- Page loads, call connects, nobody answers: the worker is down or its LiveKit key/secret is wrong
  (`logs voice-agent`).
- Microphone blocked: the page is not on HTTPS; check DNS and `logs caddy`.
- A service exits at start: its message names the weak or missing secret it refused.
