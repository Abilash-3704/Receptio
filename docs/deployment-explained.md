# How Receptio is deployed (a personal reference)

This explains what is running, where, why it was set up this way, and how to operate it. The
step-by-step runbook is `docs/deploy.md`; this file is the "how it fits together" companion.
It contains no secrets. Those live only in `.env` files.

## The short version

Receptio runs on **one small rented Linux server** (an AWS Lightsail instance in Mumbai). On it,
**Docker Compose** runs six containers. A web server called **Caddy** is the only one reachable
from the internet; it gives the site HTTPS and passes requests on. The live audio of a call does
not go through your server at all: it goes through **LiveKit Cloud**.

Live site: https://receptio-demo.duckdns.org

## The pieces and what each one is

| Piece | What it is | Where it runs |
|---|---|---|
| **Lightsail instance** `receptio` | A rented computer. Ubuntu 24.04, 2 GB RAM, 2 vCPUs, 60 GB disk, about $12 a month. Region ap-south-1 (Mumbai). | AWS |
| **Static IP** `13.126.134.105` | A fixed address for the instance. Without it the address changes when the instance restarts. | AWS |
| **Firewall** | Allows inbound ports 22 (SSH), 80 (HTTP) and 443 (HTTPS) only. | Lightsail's Networking tab |
| **DuckDNS names** | Free names that point at the static IP: `receptio-demo.duckdns.org` (call page) and `receptio-staff.duckdns.org` (dashboard). | duckdns.org |
| **Docker Compose** | Starts and connects all the containers from `docker-compose.prod.yml`. | The instance |
| **LiveKit Cloud** | A hosted media server. Carries the call audio between the visitor's browser and the voice worker. | livekit.io |
| **Deepgram, Cartesia, Anthropic** | Speech-to-text, text-to-speech, and the language model. Called by the voice worker. | Their own clouds |

## What runs in Docker (the six containers)

| Container | Job | Reachable from outside? |
|---|---|---|
| `caddy` | Takes ports 80 and 443, gets and renews the HTTPS certificates itself, routes requests. | **Yes**, the only one |
| `web` | Serves the call page and the `/token` endpoint that lets a browser join a call. | Through Caddy |
| `backend` | The clinic API: appointments, availability, call records, spending caps, and the staff dashboard. | Through Caddy, on the staff name only |
| `agent` | The receptionist's decision logic. | No |
| `voice-agent` | The **voice worker**. Connects out to LiveKit Cloud and waits; when a call starts it joins the room, listens, thinks and speaks. It must always be running. | No |
| `db` | Postgres. Stores everything. Data is in a Docker volume called `db-data`. | No |

## What happens during a call

1. The visitor opens `https://receptio-demo.duckdns.org`. Caddy passes the request to `web`.
2. They press Start call. The page asks `web` for a **token**. `web` first asks `backend` to admit
   the call, which enforces the daily caps. If allowed, `web` signs a token with your LiveKit key.
3. The browser uses the token to join a room on **LiveKit Cloud**. Audio flows browser to LiveKit.
4. LiveKit tells the already-waiting **voice worker** about the new room. The worker joins it.
5. Each turn: the worker sends the caller's speech to Deepgram, passes the text to the agent logic
   (Claude plus the backend's booking tools), and has Cartesia turn the reply into speech.
6. When the call ends, the worker reports back so the backend can record it and refund unused
   cap time. The dashboard reads those records.

If the worker is not running, steps 1 to 3 still work. The caller joins and hears nothing.

## Routing (what Caddy does)

`deploy/Caddyfile`:

- `receptio-demo.duckdns.org` goes to `web:8002`
- `receptio-staff.duckdns.org` goes to `backend:8000`

Browsers only allow microphone access on HTTPS, which is why Caddy is there. Port 80 is used so
Caddy can prove to Let's Encrypt that it controls the domain. Both DuckDNS names must point at
the server for the certificates to be issued.

## The files in the repo that matter

| File | Purpose |
|---|---|
| `docker-compose.yml` | The local development stack. Includes a dev-mode LiveKit. Not used on the server. |
| `docker-compose.prod.yml` | The server stack. No LiveKit service, only Caddy publishes ports, everything restarts on failure, and it forces production safety settings. |
| `deploy/Caddyfile` | The routing and HTTPS config. |
| `.env.example` | Names every setting. The real `.env` is never committed. |
| `docs/deploy.md` | The short runbook. |
| `tests/test_prod_compose.py` | Checks the prod compose file has no LiveKit service and exposes only Caddy. |

## Settings and secrets

The server has its own `.env` at `~/receptio/.env`. It was copied from the laptop with `scp`, then
edited on the server. The main settings:

- `POSTGRES_PASSWORD`: generated on the server, so it has never existed on the laptop.
- `BACKEND_SERVICE_TOKEN`: the shared secret the services use to talk to the backend.
- `DASHBOARD_PASSWORD`: the staff login for the dashboard (username `staff`).
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`: from the LiveKit Cloud project.
- `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `ANTHROPIC_API_KEY`: provider keys.
- `DOMAIN`, `STAFF_DOMAIN`: the two DuckDNS names.
- `GLOBAL_MINUTES_PER_DAY=30` and `TRUSTED_PROXY_HOPS=1`.

`docker-compose.prod.yml` forces `DEPLOYED_ENVIRONMENT=true`. In that mode the backend refuses
to start with a weak or default Postgres password, and the voice services refuse LiveKit's dev
keys. These are guards against deploying with defaults, not a complete security model.

## Spending protection

The call link is public and each call spends money at Deepgram, Cartesia and Anthropic, so the
backend limits it:

- A call lasts at most 5 minutes.
- 3 calls per visitor per day, and a per-IP ceiling of 10.
- 30 minutes per day for the whole demo (`GLOBAL_MINUTES_PER_DAY`).
- A rate limit on the token endpoint.

These are a cost guard, not identity. Someone who clears cookies and changes network can get
fresh visitor allowances; the global minute cap bounds total spend regardless. Also set spending
limits in each provider's own dashboard. The caps do not replace that.

## Day-to-day commands

Connect (from the laptop):

```
ssh -i ~/.ssh/id_ed25519_personal ubuntu@13.126.134.105
cd ~/receptio
```

On the server:

```
docker compose -f docker-compose.prod.yml ps                       # what is running
docker compose -f docker-compose.prod.yml logs -f voice-agent      # follow one service
docker compose -f docker-compose.prod.yml logs --tail 100 backend
docker compose -f docker-compose.prod.yml restart voice-agent
docker compose -f docker-compose.prod.yml down                     # stop everything (data is kept)
```

**Deploy a change:** push to GitHub from the laptop, then on the server:

```
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

**Back up the database:**

```
docker compose -f docker-compose.prod.yml exec db pg_dump -U receptio receptio > backup.sql
```

**Change a setting:** edit `~/receptio/.env`, then run `docker compose -f docker-compose.prod.yml up -d`
so the containers are recreated with it.

## If something breaks

| Symptom | Likely cause | What to do |
|---|---|---|
| Call connects, nobody answers | The voice worker is down, or its LiveKit key or secret is wrong | `logs voice-agent`; check `.env` |
| Microphone blocked | The page is not on HTTPS | Check DNS and `logs caddy` |
| "Too many calls" message | A daily cap was reached | Wait for the next day (clinic time, Asia/Kolkata) or raise the cap in `.env` |
| A container keeps exiting | A safety guard refused a weak or missing setting | Read its log; the message names the setting |
| Site unreachable | Instance stopped, firewall rule removed, or DNS changed | Check Lightsail, its Networking tab, and `nslookup` |
| First build killed with no error | Out of memory on 2 GB | Add swap (`fallocate -l 2G /swapfile` and friends) |

## Cost and shutting down

- The instance costs about $12 a month while it exists, **even when stopped**. To stop paying,
  delete the instance, not just stop it.
- Release the static IP too if you delete the instance. Lightsail charges for a static IP that is
  not attached.
- LiveKit Cloud has its own free tier and limits; check the dashboard for usage.
- Provider usage (Deepgram, Cartesia, Anthropic) is billed by them, separately.

## How it was set up (history)

1. Chose a single VM over a hosted platform: the voice worker must stay running and LiveKit needs
   UDP, which fits a VM better than serverless or free-tier hosting.
2. Replaced the self-hosted LiveKit with LiveKit Cloud (no UDP ports or dev keys to manage).
3. Added `docker-compose.prod.yml`, `deploy/Caddyfile` and this documentation.
4. Tried DigitalOcean (payment failed), then used AWS Lightsail.
5. Created the instance, attached the static IP, opened ports 22, 80 and 443.
6. Created the two DuckDNS names pointing at the static IP.
7. Installed Docker, cloned the public repo, copied `.env` to the server and set a fresh
   Postgres password, the domains and the demo cap.
8. Ran `docker compose -f docker-compose.prod.yml up -d --build`; Caddy obtained the certificates.

## Known limits and things to keep in mind

- One server: no redundancy. If it goes down, the demo goes down.
- Free DuckDNS names are fine for a demo but are not a professional domain.
- Turn logs are written to `~/receptio/logs` on the server. Call records are in Postgres.
- Doctor suggestions now rotate among the available doctors (a fix made before deploying), but
  they are not balanced across calls, only within the offers a single call hears.
- A dashboard password was shown in the chat while this was being set up. Changing
  `DASHBOARD_PASSWORD` in the server's `.env` (then `up -d`) is a cheap precaution.
