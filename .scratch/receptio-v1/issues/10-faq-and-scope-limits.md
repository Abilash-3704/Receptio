# 10: FAQ and scope limits

**What to build:** A Caller can ask about hours, location, Services, price ranges and insurance accepted and hear answers drawn only from a seeded knowledge base. Prices are always ranges and never quotes. When the knowledge base has no answer the agent says so and offers a Callback Request. The agent politely refuses medical advice and redirects.

**Blocked by:** 07: Escalation: Handoff, Callback Request, Clinic Hours and retry caps

**Status:** ready-for-agent

- [x] Answers come from the knowledge base and match its content (verified: `search_faq` is the only source; amounts and clock times the agent says must appear in facts looked up this Call, else withheld. Not verified: that the real model paraphrases faithfully)
- [x] Prices are stated as ranges, never as a quote (KB holds only ranges plus an "estimate, not a quote" note; a single invented amount is withheld by code. A real model could still say "about Rs 1,500", an amount present in a range; not caught)
- [x] An unanswerable question produces an honest "I don't have that" and a Callback Request offer (said by code, verbatim; acceptance goes through ticket 07's Escalation path)
- [ ] A request for medical advice is refused politely with a redirect (code-level only: narrow regexes refuse in code before the LLM; a phrasing they miss relies on the prompt rule, which a fake LLM cannot prove. Left unticked on purpose)
- [x] Scenarios cover each FAQ topic, an unknown question and a medical-advice request (scripted-LLM mode)
