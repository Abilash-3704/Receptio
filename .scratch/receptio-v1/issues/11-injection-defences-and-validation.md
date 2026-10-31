# 11: Prompt-injection defences and server-side validation

**What to build:** Caller speech is treated as untrusted input. The backend validates every tool argument server-side, so a manipulated agent still cannot make an invalid change. The agent never reveals its instructions, and handles off-topic and angry Callers calmly, redirecting or escalating after a couple of failed attempts.

**Blocked by:** 05: Book an Appointment end to end in text mode

**Status:** ready-for-agent

- [ ] Injection attempts in Caller speech do not change agent behaviour
- [ ] The backend rejects invalid tool arguments regardless of what the agent sends
- [ ] The agent never reveals its system instructions
- [ ] Off-topic Callers are redirected and angry Callers escalate after repeated failures
- [ ] Scenarios cover injection, instruction-reveal attempts, off-topic and hostile personas
