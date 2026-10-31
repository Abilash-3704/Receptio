# 02: Project skeleton and a tracer text-mode Call

**What to build:** A thin path through every layer. `docker compose up` starts the clinic backend, its database and the agent. A text-mode Call can be opened, the agent opens with the AI and recording disclosure, and the Call ends cleanly. CI runs lint, type checks and tests on every change. Environment configuration uses a committed example file and no secrets.

**Blocked by:** 01: Phase 0 decisions, provider spend caps and tricky-call list

**Status:** ready-for-agent

- [ ] One command brings up backend, database and agent from a clean clone
- [ ] A text-mode Call opens with the disclosure and ends cleanly
- [ ] An automated test drives that Call through the text-mode seam
- [ ] CI runs lint, type checks and tests
- [ ] Every external call goes through configuration with a timeout
