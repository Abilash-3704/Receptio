# 10: FAQ and scope limits

**What to build:** A Caller can ask about hours, location, Services, price ranges and insurance accepted and hear answers drawn only from a seeded knowledge base. Prices are always ranges and never quotes. When the knowledge base has no answer the agent says so and offers a Callback Request. The agent politely refuses medical advice and redirects.

**Blocked by:** 07: Escalation: Handoff, Callback Request, Clinic Hours and retry caps

**Status:** ready-for-agent

- [ ] Answers come from the knowledge base and match its content
- [ ] Prices are stated as ranges, never as a quote
- [ ] An unanswerable question produces an honest "I don't have that" and a Callback Request offer
- [ ] A request for medical advice is refused politely with a redirect
- [ ] Scenarios cover each FAQ topic, an unknown question and a medical-advice request
