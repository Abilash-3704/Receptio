# 02: Project skeleton and a tracer text-mode Call

**What to build:** A thin path through every layer. `docker compose up` starts the clinic backend, its database and the agent. A text-mode Call can be opened, the agent opens with the AI and recording disclosure, and the Call ends cleanly. CI runs lint, type checks and tests on every change. Environment configuration uses a committed example file and no secrets.

**Blocked by:** 01: Phase 0 decisions, provider spend caps and tricky-call list

**Status:** ready-for-agent

- [x] One command brings up backend, database and agent from a clean clone
- [x] A text-mode Call opens with the disclosure and ends cleanly
- [x] An automated test drives that Call through the text-mode seam
- [ ] CI runs lint, type checks and tests
- [x] Every external call goes through configuration with a timeout

Notes: compose verified with `docker compose up` via podman-compose on arm64 (agent -> backend -> Postgres, tracer Call ended). CI workflow written but not yet run on GitHub. Redis and LiveKit are not in Compose yet (not needed for the tracer).
