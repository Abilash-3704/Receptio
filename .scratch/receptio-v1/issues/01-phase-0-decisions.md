# 01: Phase 0 decisions, provider spend caps and tricky-call list

**What to build:** The expensive-to-change decisions are made and written down before any code. The real-time framework and the speech, LLM and voice providers are chosen and recorded with reasons, current official docs checked and versions pinned. Spend caps are set on every provider dashboard. Fifteen tricky Caller scenarios are written (corrections, ambiguous times, interruptions, unusual names, angry Caller, Emergency mid-scheduling, off-topic, injection attempt) along with the target metrics.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Framework and providers chosen, with reasons, in the architecture document (`docs/architecture.md`)
- [x] Spend caps set on every provider dashboard and listed as a checklist in the README (checklist written in `README.md`; **the caps themselves are still for the human to set**)
- [x] Fifteen tricky-call scenarios written in prose (`docs/tricky-calls.md`)
- [x] Target metrics documented (`docs/metrics.md`)
- [x] Python tooling choices (formatter, linter, type checker) agreed: uv, ruff, pyright (`docs/architecture.md`)
