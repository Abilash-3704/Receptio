# Voice latency investigation (2026-11-06)

What was slow, what was found, what was changed, and what has and has not been measured. Companion to `docs/latency-budget.md` (the measurement definitions) and `CHANGELOG.md` (the model and prompt change log).

**Target** (`docs/metrics.md`): p50 under 800 ms, p95 under 1500 ms from the end of the Caller's speech to the first agent audio.

**Status in one line:** the call is still about 3.3 s at the median, so the target is not met. Several correctness bugs were found and fixed, one Haiku speed-up is measured, and the last fixes (speaking each piece at once, and the fixed filler phrase) have not been tried on a real call.

All numbers come from developer-laptop calls (one machine, one network). Sample sizes are small and are stated each time. No number here is a p50/p95 over the required 20+ turns.

---

## 1. Baseline: where the time went

Source: `logs/voice-turns.jsonl` from 2026-11-03, 33 completed turns (`scripts/latency_summary`).

| Stage | Share of `total_ms` | Typical |
|---|---|---|
| Claude requests (`llm_ms`) | **90%** | median 1.96 s per request |
| Speech synthesis (Cartesia) | 4% | about 150 ms |
| Backend tool calls | 0.2% | 0 to 40 ms |
| Speech recognition (Deepgram) | about 0 | finalises about 160 ms before the voice detector's end of speech |

Overall: p50 3609 ms, p95 16525 ms.

Each extra Claude request in a turn added about 2 s:

| Claude requests in the turn | Turns | Median `total_ms` |
|---|---|---|
| 1 | 15 | 2.5 s |
| 2 | 13 | 3.9 s |
| 3 | 2 | 6.4 s |
| 4 or more | 2 | 10.0 s and 16.5 s |

Conclusion: backend, database and tool latency are not the problem. The Claude requests are.

---

## 2. Issues found

### Issue 1: Claude requests were slow and sequential
- Every reply waited for the whole model response (non-streaming) before anything was spoken.
- `place_hold` was always followed by a second model request whose only job was to ask for `read_back`, which the prompt makes mandatory and which needs no input from the model.
- The fast model (`LLM_MODEL_FAST`) was configured but unused.

### Issue 2: the model's default thinking ate the token cap (correctness and latency)
- `claude-sonnet-5-5` and `claude-haiku-5-5` think by default. Thinking tokens count against `max_tokens` (400). `AnthropicLLM` set no `thinking` or `effort`.
- Symptoms on call 16, turn 12 (a price question): the first request used all 400 tokens, took 4.0 s, and sent `search_faq` with no `question` (`bad arguments`). The Caller then heard "Sorry, could you say that again?". The turn took 9.2 s over 3 requests. An older log shows a 17.9 s `search_faq` turn with the same pattern, which is consistent with this cause but was not confirmed.
- Reproduced with real requests (Sonnet, a Caller saying yes and asking the price, 8 requests per setting):

| Setting | Ended at `max_tokens` with only a thinking block | Called `search_faq` | Median |
|---|---|---|---|
| Default | **2 of 8** | 6 of 6 that finished | 4.0 s |
| Thinking off | 0 of 8 | 8 of 8 | 2.3 s |

- `effort=low` did not help: thinking still ran.

### Issue 3: the agent spoke a tool call out loud, then went silent (caused by the Issue 2 fix)
- Call 17: the Caller heard `(tool call) {"tool": "place_hold", "args": {"option": 2}}` and the agent then did nothing.
- Cause: past tool calls were sent to Claude as assistant text of the form `(tool call) {...}`. With thinking on the model resisted copying that format. With thinking off it copied it as its reply, so no tool ran.
- Reproduced by replaying that exact turn (10 requests each):

| History format | Said the tool call aloud | Made the real `place_hold` call |
|---|---|---|
| Old (text) | 10 of 10 | 1 (alongside the spoken text) |
| New (tool blocks) | 0 of 10 | 10 of 10 (option 2) |

### Issue 4: the "Let me look that up" line was not heard early
- After streaming, the model often says a short line before a tool call. It should play at once.
- Call 18 data: on turns with such a line, first audio came 1.1 to 2.3 s after the text was ready (about when the whole answer was ready). On turns without one it came 0.15 to 0.28 s after.
- Likely cause: the speech pipeline holds a lone sentence until more text arrives or the stream ends. This is the explanation that matched the data; it has not been confirmed independently.

### Issue 5: the agent spoke its own reasoning (found on call 19)
- The Caller heard: "The state now shows the caller agreed to the read-back, so I can confirm. I'll also look up the price range." and then "I'm sorry, my last step was wrong. The read-back hasn't been fully confirmed yet. Let me read the details back to you again."
- What happened: the Caller said "Yeah. That's correct. And how much does this cost?". A yes that contains a question is not an explicit yes (by design, ADR-0001), so the code did not record the agreement. The model misjudged the state, wrote its reasoning as text next to its tool calls, and called `confirm_appointment`, which the code refused. The safeguard worked; the speech was wrong.
- Cause: with thinking off the model narrates its reasoning as visible text before and between tool calls. The streaming path spoke each sentence as it arrived, before it was known that a tool call followed, so it could not tell reasoning from an answer.
- Replayed on the real API (call 19's turn, 8 requests per setting): the model wrote text with its tool calls 8 of 8 times. A prompt rule telling it not to made no difference (still 8 of 8) and made its decisions worse (it tried `confirm_appointment` in 7 of 8 runs against 1 of 8), so no prompt change was made.

---

## 3. Fixes made

| # | Fix | Where | Evidence |
|---|---|---|---|
| 1 | ~~**Streaming**: each finished sentence goes straight to speech while the model writes the rest.~~ **Removed (2026-11-11), see issue 5 and fix 8.** It gained little (time to first token, not output length, dominates; see section 4) and could not be made safe. | was `agent/anthropic_llm.py`, `agent/text_mode.py` | Superseded. |
| 2 | **Chained `read_back`**: after a successful `place_hold` the code runs `read_back` itself (`auto_read_back`, voice calls only). The Caller's "yes" still gates `confirm_appointment` (ADR-0001). | `agent/text_mode.py`, `voice/admission.py`, `voice/worker.py` | Call 18: the `place_hold` + `read_back` turn used 1 model request and took 2.2 s (2.8 to 3.5 s before). |
| 3 | **Fast model after tool results**: `LLM_MODEL_FAST` words the step after `search_faq`, `confirm_appointment` and `cancel_appointment`. Planning steps stay on Sonnet. | `agent/anthropic_llm.py` (`FAST_AFTER_TOOLS`) | Haiku requests averaged 0.95 s against 2.17 s. |
| 4 | **Thinking switched off** (Sonnet 5.5: `{"type": "between_tools"}`, Haiku 5.5: `{"type": "disabled"}`; any other model gets no setting). | `agent/anthropic_llm.py` (`THINKING_OFF`) | Fixes the `max_tokens` cut-offs (largest output in call 18: 105 tokens, against 400). |
| 5 | **Real `tool_use` / `tool_result` blocks in the history**, replacing the `(tool call)` text. A tool whose reply was spoken directly gets a placeholder result. | `agent/anthropic_llm.py` (`to_api_messages`) | The 10 of 10 replay in Issue 3. Required for fix 4 to be safe. |
| 6 | **Code guard**: a reply that looks like a tool call or result written out as text is never spoken, and the model is told so. | `agent/text_mode.py` (`TOOL_TALK`, `_withheld`) | Unit test. |
| 7 | **Speak each piece at once**: `ReceptioAgent.llm_node` yields a `FlushSentinel` after each piece the core hands over, so each piece is its own speech segment. | `voice/driver.py` (`segments`), `voice/worker.py` | Unit tests only. **Not measured on a real call.** |
| 8 | **Never speak text that comes with a tool call; the code says a fixed phrase instead.** The adapter drops any text sent with a tool request. In voice Calls the core says "Let me check that for you." (or "One moment.") while `search_faq`, `check_availability`, `identify_patient`, `confirm_appointment` or `cancel_appointment` runs. Streaming into speech is removed. | `agent/anthropic_llm.py`, `agent/text_mode.py` (`TOOL_FILLER`) | Unit tests; real-API replay of call 19 (issue 5). **Not measured on a real call.** |

Changes 4 to 6 and 8 are logged in `CHANGELOG.md` as "not evaluated" against the scenario suite, per the project's rule. Fix 7 is described in `docs/latency-budget.md`.

---

## 4. Measured effect so far

Call 18 is the first call with fixes 1 to 6: 11 turns, a full booking, outcome `completed`. Fix 7 came after it.

| | Before | Call 18 |
|---|---|---|
| Median `total_ms` | 3.6 s (33 turns) | **3.3 s** (11 turns, range 2.1 to 4.3 s) |
| Sonnet, average per request | 1.97 s (n = 15) | 1.94 s (n = 26) |
| Haiku, average per request | 2.17 s (n = 3) | **0.95 s** (n = 6) |
| Largest model output | 400 tokens | 105 tokens |
| "Sorry, could you say that again?" replies | present | none |
| Leaked tool text | present (call 17) | none |

What this says, plainly:
- **No meaningful latency gain yet.** 3.6 s to 3.3 s is within the noise for 11 turns.
- **Correctness improved a lot.** No cut-off tool calls, no spoken tool text, and `search_faq` was called correctly 4 times.
- **Sonnet did not get faster in production.** Its output halved but its time did not: about 2 s per request is time to first token, which streaming and thinking-off cannot reduce. The earlier probe's "4.0 s to 2.3 s" came from avoiding long-thinking outliers on a hard question, so it overstated the average gain.
- **The one clear speed-up is Haiku** (about 1 s faster per request) and the chained `read_back` (one fewer request, about 2 s).
- **Input tokens are larger than first estimated:** 2.5k to 4.7k per request, growing over a call, not about 1.5k.

---

## 5. Not done, and not verified

Not verified:
- **Fix 7 (speak each piece at once)** has no real-call result. Expected: `tts_first_audio_ms` on tool turns falls from 1.1 to 2.3 s to about 0.2 s, while `llm_first_token_ms` stays the same. A risk to check by listening: each piece is a separate synthesis request, so there may be gaps or choppy prosody between sentences.
- **Booking quality without thinking** was tested on a few scenarios only. Run `--mode simulated` before trusting it (see `evals/` and the README).
- **Everything above is from a few calls.** A proper run needs 20 or more turns, ideally with `logs/voice-turns.jsonl` rotated first so old turns do not mix in (`scripts/latency_summary logs/voice-turns.jsonl --min-turns 20`).

## 5b. Update, 2026-11-11: call 20 and what was done about the remaining ideas

Call 20 (12 turns, outcome `completed`, first call with fixes 1 to 8): median `total_ms` 2.4 s, p95 4.8 s; speech synthesis started in a median 98 ms after the first text (fix 7 works); no reasoning or tool text spoken. 12 turns is not a distribution. `total_ms` is flattered on lookup turns, because the fixed phrase is the first audio while the answer follows 3 to 4 s later.

| Idea below | Done | Result |
|---|---|---|
| 1. Check the phrase on a real call | yes, call 20 | played at once; no tool failed during the call, so the failure case is still unchecked |
| 2. Haiku for planning | tried, not adopted | `LLM_FAST_PLANNING` (default off). Real-model eval, 18 scenarios: Sonnet 14 completed, Haiku 10 (see `docs/eval-report.md`). Haiku missed ordinary bookings |
| 3. Trim input tokens | measured, not done | one request each: 2.6k input tokens 2.2 s, 3.1k 2.8 s, 3.7k 2.9 s, 4.9k 2.8 s median. Flat once the history is non-trivial, so no gain to expect |
| 4. Chain more in code | partly | `record_patient` has a fixed phrase ("One moment."). It was the 4.8 s silent turn in call 20 (two Sonnet requests, 2.3 s and 2.1 s). The two requests remain; only the silence is gone |

New measure: `answer_ready_ms` in the turn-timing line is ASR final to the answer itself (the last piece of speech the core hands over), so the phrase no longer hides the wait. `scripts/latency_summary` shows it. Expect it to be higher than `total_ms` on lookup turns. Not yet seen on a real call. These changes are not live until the voice-agent image is rebuilt.

Expectation, plainly: these changes make long turns feel better and the metric more honest; they do not make the model faster. Each Sonnet request is about 2 s, so a two-request turn costs 3 to 4 s. Reaching the docs' p50 target of 800 ms needs a faster planning model, and Haiku did not pass the eval.

Remaining ideas, in the order I would try them (items 1 to 4 were handled as above):
1. **Check the new fixed phrase on a real call** (fix 8): that it plays at once and does not sound wrong before a tool that then fails.
2. **Haiku for planning steps** too (about 0.95 s against about 2 s per request). Risk: worse booking decisions, so evaluate first.
3. **Trim the input tokens** (shorter history and state summary) to reduce time to first token. The size of the gain has not been measured.
4. **Chain more fixed sequences in code**: `record_patient` then `check_availability`, and the spoken wording after `confirm_appointment`.
5. **Turn detection**: `preemptive_generation` is off, and the silence window plus endpointing delay (about 1 s combined) is not in `total_ms`. About 7 of 40 turns were cut off before any audio, so tuning needs care.

---

## 6. Other things learned

- **Stale container:** `docker-compose.yml` bakes the code into the image (no mounted source). A rebuild reused cached `COPY` layers and the container ran old code. Use `podman-compose build --no-cache voice-agent && podman-compose up -d --force-recreate voice-agent`, then compare file checksums against the container.
- **Backend port:** `.env` sets `BACKEND_PORT=8010`, so the dashboard is at `http://localhost:8010/dashboard`, not 8000.
- **Pre-existing test failures:** 60 tests fail on a clean copy of HEAD as well as with these changes (the same 60 each time). They appear to be date-related (booking tests ask for "tomorrow" and expect 2026-11-03), but this was not investigated.
- **Other containers:** backend, agent and web still run older images. Only the voice worker uses the changed code.
- **Cost of the probes:** about 60 small real requests on the Anthropic key in total.

## Appendix: how to repeat the measurement

```bash
mv logs/voice-turns.jsonl logs/voice-turns-<label>.jsonl   # start a clean log
# make a call of 20+ turns: include a price or FAQ question and a full booking
scripts/latency_summary logs/voice-turns.jsonl --min-turns 20
```

Per-request model, tokens and duration are in the database: `select turn_seq, model, output_tokens, duration_ms from call_llm_calls where call_id = <id>`. Per-turn `llm_steps`, `llm_ms`, `tool_ms` and `tools` are in each log line.
