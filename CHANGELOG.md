# Changelog of prompt and model changes

PROJECT.md rule 7: every prompt or model change is logged here with its evaluation result. Add an
entry in the same commit as the change, newest first.

**How to read the "Eval result" lines.** The scripted suite (`uv run python -m evals`) replays a
fixed agent script, so it does not call the model and does not read the prompt's effect on
behaviour; it is a regression gate for the code around the model. A change to the system prompt or
the model name can therefore only be judged by a real-model run (`--mode simulated`, see README).
Until such a run exists, the entry says "not evaluated" rather than reporting a number it does not
have. Numbers are never invented here; `docs/eval-report.md` holds the methodology and the current
baseline.

## Format

```
### YYYY-MM-DD  short title  (commit)
- Change: what changed, in the prompt (agent/anthropic_llm.py SYSTEM_PROMPT) or the model settings
- Eval result: the run (mode, persona, scenarios, date) and its numbers, or "not evaluated"
- Decision: kept / reverted / follow-up
```

## Entries

### 2026-11-11  `record_patient` phrase; time to the answer logged; optional fast planning (off)
- Change: `record_patient` now has the fixed phrase "One moment." (the 4.8 s turn in call 20 was silent). The turn-timing line has a new field `answer_ready_ms` (ASR final to the answer, not the filler before it); the line is written when the core finishes, and `scripts/latency_summary` shows it. New setting `llm_fast_planning` (default false) sends every step to the fast model, not only wording after a tool. The eval harness now builds the LLM as production does (fast model, and `auto_read_back=True` for simulated runs; before, it never tested the chained read-back).
- Not changed: input-token trimming. Measured on the real API, one request each: 2.6k input tokens 2.2 s, 3.1k 2.8 s, 3.7k 2.9 s, 4.9k 2.8 s median. Flat past the first few turns, so there is nothing to gain.
- Eval fixes (`evals/`): simulated runs give the Caller `sim_turn_headroom` (4) extra turns, since the scenarios' `max_turns` assume the terse scripted Caller; the simulated Caller answers a read-back with a short yes (it restated every detail, which ADR-0001 refuses twice, so every booking ended in a handoff) and hangs up when the agent says nothing (a 400 from the API); a crashed scenario is a failure, not an abort of the run; the LLM is built as production builds it (fast model, `auto_read_back=True`).
- Eval result: `--mode simulated --persona patient`, the same 18 scenarios (bookings, cancels, reschedule, FAQ, hedged yes, handoff), one run each, 2026-11-11. Sonnet planning: 14 of 18 completed, 1.27 USD. Haiku planning: 10 of 18, 0.56 USD. Haiku failed ordinary bookings that Sonnet completed (happy path, exact time, reschedule after a hold expired) and recorded no escalation for a wrong-identity cancel. Both failed `probe_yes_cancel_it_015`, `off_topic_then_books_015` (the system-prompt detector fires on the words "full name") and `human_requested_mid_booking_015` (crash: a 7-digit phone number is a 422 on the escalation). One run each is not a distribution: a first run, before the caller fixes, gave 11 and 10.
- Decision: `llm_fast_planning` stays off; Haiku is not good enough to plan. Open: the 422 on a short phone number when escalating; "thank you very much" is not accepted as filler in a yes.

### 2026-11-11  text written with a tool call is never spoken; the code says a fixed phrase
- Change: call flow, not the prompt text. Call 19: the Caller said "Yeah. That's correct. And how much does this cost?" (a yes with a question is not a yes, by design); the model wrote its reasoning next to its tool calls ("The state now shows the caller agreed..., so I can confirm." / "I'm sorry, my last step was wrong.") and the streaming path spoke it. Streaming of the reply into speech is removed: a step's text is spoken only when the whole step is a plain reply and has passed the guards, and text that comes with a tool request is dropped. In voice Calls the code says a fixed phrase ("Let me check that for you.", "One moment.") while `search_faq`, `check_availability`, `identify_patient`, `confirm_appointment` or `cancel_appointment` runs (`TOOL_FILLER` in `agent/text_mode.py`). No claim is made that anything was done.
- Eval result: not evaluated against the scenario suite. Probe on the real API, call 19's turn replayed, 8 requests per setting: the model wrote text alongside its tool calls 8 of 8 times, with and without a prompt rule telling it not to, so the rule was not kept (with it the model also tried `confirm_appointment` in 7 of 8 runs, against 1 of 8 without it). The prompt is unchanged. The code drops that text.
- Live result, call 20 (2026-11-11, 12 turns, outcome `completed`): no reasoning or tool text spoken; the fixed phrases were spoken on the lookup turns; a yes that came with a question (turn 24, "Yeah. And do you have any insurance policy?") was answered and not taken as the confirm, and the booking was confirmed on the next plain "Yeah. Confirm.". 12 turns is not a distribution.
- Decision: kept. The earlier "stream replies" entry's streaming part is superseded; chaining `read_back` and the fast model stay. Follow-up: `record_patient` has no fixed phrase and took 4.8 s in call 20 (two Sonnet requests, 2.3 s and 2.1 s): add a phrase or chain it with the next step.

### 2026-11-06  speak each piece of the reply at once (`FlushSentinel`)
- Change: call flow in the voice pipeline, not the prompt or model. Call 18 showed a line spoken before a tool call ("Let me look that up for you.") was heard only after the whole answer was ready: on turns with such a line, first audio came 1.1 to 2.3 s after the text, against 0.15 to 0.28 s on turns without one, because the speech pipeline holds a lone sentence until more text arrives. `ReceptioAgent.llm_node` (`voice/worker.py`) now yields a `FlushSentinel` after each piece the core hands over (`VoiceTurnDriver.segments` in `voice/driver.py`), so each piece is its own speech segment.
- Eval result: not part of the scenario suite (it touches the voice pipeline only). Unit tests cover the segments and the flush. Live, call 20 (2026-11-11, 12 turns): `tts_first_audio_ms` 91 to 226 ms on every turn, median 98 ms, including the lookup turns. Caveat: on lookup turns the first audio is now the fixed phrase, so `total_ms` (median 2.4 s in call 20, against 3.3 s in call 18 and 3.6 s before) no longer measures the wait for the answer; model time to the answer on those turns was still about 3.2 to 3.8 s (two requests). Not checked: whether separate synthesis requests per sentence sound choppy.
- Decision: kept, follow-up: listen for gaps between sentences; log the time to the answer, not only to the first audio.

### 2026-11-06  tool calls sent as tool blocks; a spoken "(tool call)" is refused in code
- Change: a bug from the entry below, found on a live call (call 17: the Caller heard `(tool call) {"tool": "place_hold", ...}` and the agent then stopped). History was sent to Claude with past tool calls flattened into assistant text "(tool call) {...}"; with thinking off the model copied that format as its reply instead of calling the tool. `to_api_messages` now sends real `tool_use` / `tool_result` blocks (a tool whose reply was spoken gets a placeholder result), and the core withholds any reply that looks like a tool call written out as text (`TOOL_TALK` in `agent/text_mode.py`) and tells the model so.
- Eval result: not evaluated against the scenario suite. Replay of call 17's last turn (Caller: "Eleven thirty AM works.") on the real API, 10 requests each: old format, the model wrote the tool call as spoken text 10 of 10 times and made the real call once; new format, a real `place_hold` call 10 of 10 times, nothing spoken. Unit tests cover the history conversion and the guard.
- Decision: kept, follow-up: `--mode simulated` run; the thinking-off entry below is only safe with this change.

### 2026-11-06  thinking switched off for the receptionist's requests
- Change: model settings, not the prompt text. `claude-sonnet-5-5` and `claude-haiku-5-5` think by default and thinking tokens count against `max_tokens` (400). Requests now send `thinking: {"type": "between_tools"}` to Sonnet 5.5 (it rejects `disabled`) and `{"type": "disabled"}` to Haiku 5.5; any other model name gets no `thinking` setting. Code in `agent/anthropic_llm.py` (`THINKING_OFF`).
- Eval result: not evaluated against the scenario suite. Probe on 2026-11-06 with the real API, one scenario (Caller says yes to the read-back and asks the price), 8 requests per setting on Sonnet: default settings 2 of 8 ended at `max_tokens` with only a thinking block (the Caller heard "could you say that again"), median 4.0 s; thinking off 0 of 8, `search_faq` called every time, median 2.3 s. Haiku answering after `search_faq`: median about 2.5 s with defaults (one request hit `max_tokens`), about 1.4 s with thinking off. Eight to ten requests are not a distribution.
- Decision: kept, follow-up: run `--mode simulated` to check booking quality without thinking, and the human latency run.

### 2026-11-06  stream replies, chain read_back, fast model after tool results
- Change: model settings and call flow, not the prompt text. `AnthropicLLM` streams and hands each sentence to the core's guards and then to speech; the core runs `read_back` itself after a successful `place_hold` (voice Calls); `LLM_MODEL_FAST` answers the step after `search_faq`, `confirm_appointment` and `cancel_appointment`. Text the model writes before a tool request is now spoken.
- Eval result: not evaluated (no real-model run; the scripted suite does not call the model). Unit tests cover the adapter, the sentence guards and the chained read-back.
- Decision: chaining `read_back` and the fast model kept (call 18: the `place_hold` + `read_back` turn took 2.2 s with one request, against 2.8 to 3.5 s; Haiku requests averaged 0.95 s against 2.17 s). The streaming of the reply into speech was removed on 2026-11-11 (see that entry): it spoke the model's reasoning, and it gained little because time to first token, not output length, dominates. Follow-up: run `--mode simulated` and the human latency run.

### 2026-10-31  Hold released on any Escalation; "yes, cancel it" accepted
- Change: (1) any Escalation, not only an Emergency or Urgent Case, now releases an in-progress
  Hold through the existing release path, as soon as the Escalation starts (also while a Callback
  Request is still waiting for a number). (2) The cancel read-back accepts an affirmative plus the
  word "cancel" and filler ("yes, cancel it", "yes please cancel it", "yes cancel", "yes go ahead
  and cancel"); the booking read-back accepts "book" the same way ("yes, book it"). The action word
  must match the read-back asked, and any other word ("but", "not", "actually", "no", a question
  mark) still refuses. Code in `agent/text_mode.py`; the prompt is unchanged.
- Eval result: not evaluated against a real model. Scripted suite: 102 core scenarios pass
  (`probe_yes_cancel_it_015` graduated from known gap; `human_requested_mid_booking_015` now
  expects release with no lapse); baseline `scenarios` 101 to 102, no metric value changed.
- Decision: kept. Follow-up: watch real-model runs for hedges phrased outside the allowlist, which
  are refused (one extra question) by design, ADR-0001.

### 2026-10-31  Prompt: untrusted input, off-topic and angry Callers  (c8baf1e)
- Change: added the rules that Caller speech is untrusted, that instructions are never revealed,
  that off-topic chat gets one or two polite redirects, that angry Callers get a calm and brief
  reply, and that the backend re-validates every tool call.
- Eval result: not evaluated against a real model. Scripted suite at the time: injection
  scenarios `injection_admin_mode_012`, `injection_obeyed_worst_case_012` and
  `reveal_instructions_013` passed. They check the code's defences (tool and backend refusals) and
  that scripted agent speech does not repeat the prompt, so they say nothing about whether the
  model follows the new rules.
- Decision: kept. Follow-up: a real-model run with the `hostile` persona and the injection
  scenarios.

### 2026-10-31  Prompt: FAQ facts and medical advice  (f5391c2)
- Change: clinic facts only from `search_faq`, prices only as the ranges it returns, no medical
  advice (no diagnosis, medication, dosage or home remedy), staff callback when the knowledge base
  has no answer.
- Eval result: not evaluated against a real model. The `invented_price` and `medical_advice` rules
  run on the scripted FAQ scenarios and pass; the code-level price and time guards, not the
  prompt, are what those scenarios exercise.
- Decision: kept.

### 2026-10-31  Prompt: identification, cancel and reschedule  (6c9467b)
- Change: ask for name and phone and call `identify_patient`; say nothing about any Appointment
  until it succeeds; `select_appointment`, `request_cancel`, `start_reschedule` flow.
- Eval result: not evaluated against a real model. Scripted `wrong_identity_cancel_014`,
  `cancel_identified_016`, `reschedule_identified_016` and `reschedule_declined_016` pass; the
  `revealed_before_identification` rule re-checks the database and the trace.
- Decision: kept.

### 2026-10-31  Model: the real LLM is added, `claude-sonnet-5-5` for turns  (44a84ef)
- Change: `agent/anthropic_llm.py` (first system prompt: offer only returned times, hold,
  read back, confirm only after the state shows agreement). Settings in `agent/config.py`:
  `llm_model = claude-sonnet-5-5` for main turns, `llm_model_fast = claude-haiku-5-5` for simple
  turns (declared, not used yet). The simulated Caller (`evals/config.py` `sim_model`) is
  `claude-haiku-5-5`.
- Eval result: not evaluated. No real-model run of the suite has been made; the model was only
  exercised against a fake client in `tests/test_anthropic_llm.py`.
- Decision: kept as the starting point. The first real-model run becomes the first baseline for
  `--mode simulated` (`uv run python -m evals --mode simulated --write-baseline`) and the first
  before/after comparison in `docs/eval-report.md`.
