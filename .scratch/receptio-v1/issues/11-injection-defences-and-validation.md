# 11: Prompt-injection defences and server-side validation

**What to build:** Caller speech is treated as untrusted input. The backend validates every tool argument server-side, so a manipulated agent still cannot make an invalid change. The agent never reveals its instructions, and handles off-topic and angry Callers calmly, redirecting or escalating after a couple of failed attempts.

**Blocked by:** 05: Book an Appointment end to end in text mode

**Status:** ready-for-agent

- [ ] Injection attempts in Caller speech do not change agent behaviour (code-level: verified with a fully obedient scripted agent, nothing changes; model-level: needs a real-LLM run, not done)
- [x] The backend rejects invalid tool arguments regardless of what the agent sends (tests/test_input_validation.py, tests/test_injection.py)
- [ ] The agent never reveals its system instructions (prompt rule and `revealed_system_prompt` must_not rule exist; only a real-LLM run can show it holds)
- [ ] Off-topic Callers are redirected and angry Callers escalate after repeated failures (prompt text only; escalation tooling is ticket 07, scenarios 014 and 015 do not cover escalation)
- [x] Scenarios cover injection, instruction-reveal attempts, off-topic and hostile personas (scripted mode: `injection_*` (5), `reveal_instructions_013`, `off_topic_*` (2), `medical_advice_redirect_011`, `angry_*` (4, two end in an Escalation); all pass in the suite)
