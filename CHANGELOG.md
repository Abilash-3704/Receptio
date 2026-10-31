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
