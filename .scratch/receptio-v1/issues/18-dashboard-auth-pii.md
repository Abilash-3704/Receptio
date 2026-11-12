# 18: Dashboard, authentication and PII redaction

**What to build:** Clinic staff open a dashboard showing the Call list, a per-Call trace viewer, and summary metrics: Calls per day, completion rate, p95 latency, cost per minute and Escalation reasons. The dashboard and all admin endpoints require authentication. Patient names and phone numbers are redacted in logs where practical.

**Blocked by:** 17: Call persistence and trace

**Status:** ready-for-agent

- [ ] Every Call appears in the dashboard with a full trace
- [x] Summary metrics match the stored data (seeded unit tests and an API/HTML comparison)
- [x] Unauthenticated requests to the dashboard and admin endpoints are rejected (every route enumerated in tests/test_auth.py)
- [x] Logs contain no unredacted names or phone numbers in tested paths (text-mode agent + backend; not a live LiveKit worker)

Not ticked: "Every Call appears in the dashboard with a full trace". The list and trace are verified through the API and the parsed HTML, but nobody has looked at the dashboard in a browser; a human must confirm it renders and reads well, including the Basic-auth prompt.
