# 20: Rate limiting, load test and alerts

**What to build:** The service holds up under stress. The public Call endpoint is rate limited. A load test of about 20 concurrent Calls runs with the test-mode cap override and records latency under load. Alerts fire on latency spikes and tool failure rates.

**Blocked by:** 13: Cost guard and capacity limits; 17: Call persistence and trace

**Status:** ready-for-agent

- [ ] The public Call endpoint rejects excess requests
- [ ] A documented load test of about 20 concurrent Calls reports latency percentiles under load
- [ ] Alerts fire on a latency spike and on elevated tool failure rate
- [ ] A secrets-handling review is documented
