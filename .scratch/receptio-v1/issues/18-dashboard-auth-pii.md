# 18: Dashboard, authentication and PII redaction

**What to build:** Clinic staff open a dashboard showing the Call list, a per-Call trace viewer, and summary metrics: Calls per day, completion rate, p95 latency, cost per minute and Escalation reasons. The dashboard and all admin endpoints require authentication. Patient names and phone numbers are redacted in logs where practical.

**Blocked by:** 17: Call persistence and trace

**Status:** ready-for-agent

- [ ] Every Call appears in the dashboard with a full trace
- [ ] Summary metrics match the stored data
- [ ] Unauthenticated requests to the dashboard and admin endpoints are rejected
- [ ] Logs contain no unredacted names or phone numbers in tested paths
