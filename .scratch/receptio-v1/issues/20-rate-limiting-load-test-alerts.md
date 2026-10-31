# 20: Rate limiting, load test and alerts

**What to build:** The service holds up under stress. The public Call endpoint is rate limited. A load test of about 20 concurrent Calls runs with the test-mode cap override and records latency under load. Alerts fire on latency spikes and tool failure rates.

**Blocked by:** 13: Cost guard and capacity limits; 17: Call persistence and trace

**Status:** ready-for-agent

- [x] The public Call endpoint rejects excess requests (per-IP limits on `/token` and capacity admission, identification and authentication failure throttles; `tests/test_rate_limit_*.py`)
- [ ] A documented load test of about 20 concurrent Calls reports latency percentiles under load
  - Stand-in provider runs of 20 concurrent Calls are done and documented (`docs/load-test.md`: admission, DB, errors, percentiles of persisted timings), clearly labelled as making no claim about real provider latency. The real-provider run is NOT done (it spends money); the exact command, the cost estimate and the steps are in `docs/load-test.md`. Tick this when a human has run it and pasted the result.
- [x] Alerts fire on a latency spike and on elevated tool failure rate (`python -m backend.app.alerts`; tested on seeded data in `tests/test_alerts.py`, and observed firing on the stand-in runs)
- [x] A secrets-handling review is documented (`docs/security-review.md`)
