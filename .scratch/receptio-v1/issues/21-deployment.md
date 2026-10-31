# 21: Deployment from a clean clone

**What to build:** A stranger can clone the repository, run one command, and get the whole system. The same system is deployed to a small VPS or free tier with the process documented. The deployed demo has the test-mode override off, working caps and authentication on the dashboard.

**Blocked by:** 18: Dashboard, authentication and PII redaction; 20: Rate limiting, load test and alerts

**Status:** ready-for-agent

- [ ] `docker compose up` starts the full system from a clean clone
- [ ] The deployed demo is reachable from the web client
- [ ] The test-mode cap override is off in the deployment
- [ ] The deployment process is documented
