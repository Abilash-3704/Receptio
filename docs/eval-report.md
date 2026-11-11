# Evaluation report

What the scenario suite measures, how, the current real baseline, and what is not yet known.
Targets are in `docs/metrics.md`; the prompt and model history is in `CHANGELOG.md`.

## Read this first

- **The scripted suite is a regression gate, not evidence of quality.** Scripted scenarios replay a
  fixed agent script and fixed Caller turns, and were written by the same author as the rules they
  test. A scripted 100% means "nothing that used to work has broken", not "the agent handles
  Callers well". It does not call a model and does not exercise the system prompt.
- **There are no improvement cycles in this report, on purpose.** A before-and-after comparison
  needs a run in which the model's behaviour can change, that is a real-LLM run
  (`--mode simulated`). That needs `ANTHROPIC_API_KEY` and costs money. One small comparison was
  made on 2026-11-11 (see "First real-model comparison: Sonnet against Haiku planning"); it is not
  a full cycle: 18 scenarios, one persona, one run per setting.
  Scripted runs cannot show a prompt or model change helping or hurting, so any "cycle" built from
  them would be invented. Ticket 15's checkbox for two documented improvement cycles stays open
  until a real-model run exists (see "How to produce the first real cycle").
- Numbers for known gaps are reported separately and never counted toward the targets.

## Methodology

| | Scripted (CI gate) | Simulated (manual, costs money) |
|---|---|---|
| Agent | scripted LLM, replays `agent_script` | the real model (`LLM_MODEL`) |
| Caller | the scenario's fixed `turns` | an LLM playing a persona (`evals/personas.yaml`) from the scenario's `caller_goal` |
| Cost | none, no network | metered, hard caps (150 requests, 1.00 USD by default) |
| Deterministic | yes | no (tolerance 0.05 in `--check-baseline`) |
| Tells you | the code around the model did not regress | how the model behaves |

Every scenario runs on a fresh backend (in-memory SQLite, seeded at Monday 2 November
2026 08:00 Asia/Kolkata) with the agent and the backend sharing one fixed clock. The runner judges
the outcome from outside the agent: the database before and after, the tools asked, and what the
Caller heard. `must_not` rules re-check the database and the trace and do not trust the agent's own
gating. Definitions of each metric are in `docs/metrics.md`.

Scenario fields added for this suite: `category` and `tricky_calls` (coverage bookkeeping),
`advance_minutes` and `after_call_minutes` (move the shared clock before a Caller turn or after the
Call, for a Hold that lapses while the Caller thinks or after they hang up), and the final-state key
`live_holds` (Holds that are Held and not yet expired). New rule `offered_outside_clinic_hours`.

### The CI gate

`uv run python -m evals --check-baseline` (job `scripted-evals` in `.github/workflows/ci.yml`)
exits 1 and prints `REGRESSION against baseline: ...` when:

1. any gated metric is worse than `evals/baselines/scripted.json` (zero tolerance, the run is
   deterministic);
2. a target is missed: wrong-time rate must be 0%, Emergency recall 100%, completion >= 90%,
   Urgent Case recall >= 95%;
3. fewer core scenarios ran than the baseline recorded, so a failing scenario cannot be deleted or
   relabelled `known_gap` to pass.

`tests/test_scripted_gate.py` proves it end to end: the committed suite passes, and the same suite
with one Emergency scenario's phrasing broken (so the detector no longer sees it) exits 1 with
`emergency_recall` named. A second test runs the whole suite with the machine clock moved to
another day and requires it to pass (it did not until the agent and backend shared one clock; see
"Findings"). Regenerate the baseline deliberately, with a reason in the commit message:
`uv run python -m evals --write-baseline`.

## Current baseline (scripted)

Scripted LLM, scripted Caller, run on 2026-10-31 at the commit that introduced this file.

| Metric | Value | n | Target |
|---|---|---|---|
| Task completion rate | 100.0% | 102/102 | >= 90% |
| Wrong-time or overlapping-Appointment rate | 0.0% | 0/102 | 0% |
| Emergency recall | 100.0% | 22/22 | 100% |
| Emergency false-positive rate | 0.0% | 0/68 | tracked |
| Urgent Case recall | 100.0% | 12/12 | >= 95% |
| Urgent Case false-positive rate | 0.0% | 0/68 | tracked |
| Escalation rate | 40.2% | 41/102 | tracked |
| Cost per Call | not measured | | tracked (no LLM called) |
| Latency p50 / p95 | not measured | | voice only (ticket 16) |

Treat the 100% figures with the caution above: the Emergency and Urgent scenarios from ticket 09
were written together with the detector, and 4 of the 22 Emergency scenarios (`indep_emergency_*`)
and the probes below were written without reading its keywords. Even those 4 only show the
detector is not narrower than one author's phrasing.

## The suite

114 scenarios: 102 core (counted in the targets) and 12 known-gap probes (reported apart). The
range in the spec is 50 to 100; the suite is over it by 14, each scenario testing a distinct
behaviour (see the table). If cost per simulated run matters more than breadth, prune the
`negative_*` false-positive checks first (18 core, mostly one phrase each).

| Category | Core | Known-gap probes |
|---|---|---|
| Happy paths | 4 | 0 |
| Interruptions | 2 | 0 |
| Corrections | 3 | 0 |
| Ambiguous times | 4 | 0 |
| No Availability | 4 | 0 |
| Time taken during a Hold | 2 | 0 |
| Angry Callers | 4 | 0 |
| Off-topic and scope | 3 | 0 |
| Emergencies | 22 | 3 |
| Urgent Cases | 12 | 6 |
| Injection | 6 | 0 |
| Silence or dropped Call | 2 | 0 |
| Identification, cancel, reschedule | 7 | 0 |
| FAQ | 6 | 0 |
| Escalation | 2 | 1 |
| Unusual names | 1 | 0 |
| False-positive checks | 18 | 2 |

### The 15 tricky calls

Each is covered by at least one core scenario. Where the table lists two or more, the extra ones
test the same call from another side (a code guard against a careless agent, a closed clinic).

| # | Tricky call | Scenarios |
|---|---|---|
| 1 | Correction mid-flow | `correction_thursday_001` |
| 2 | Correction after the read-back | `correction_after_readback_015`, `hedged_yes_cannot_confirm_015` |
| 3 | "Next Tuesday" said on a Sunday | `ambiguous_next_tuesday_sunday_001` |
| 4 | "After lunch, sometime this week" | `vague_after_lunch_this_week_015` |
| 5 | Interruption while times are offered | `interruption_second_one_015`, `interruption_unsure_asks_which_015` |
| 6 | Unusual name and ASR mishearing | `unusual_name_spelling_015` |
| 7 | Booking for someone else | `book_cleaning_happy_path_001` |
| 8 | Angry Caller whose request cannot be met | `angry_same_hour_handoff_001`, `angry_same_hour_callback_001`, `angry_unmeetable_request_015` |
| 9 | Emergency in the middle of scheduling | `emergency_mid_scheduling_009`, `emergency_mid_scheduling_closed_009` |
| 10 | Urgent Case described casually | `urgent_knocked_out_tooth_casual_010`, `urgent_knocked_out_tooth_closed_010` |
| 11 | Off-topic chat and a medical advice request | `off_topic_redirect_014`, `medical_advice_redirect_011` |
| 12 | Prompt-injection attempt | `injection_admin_mode_012`, `injection_obeyed_worst_case_012`, `reveal_instructions_013`, `injection_fake_staff_skip_readback_015`, `injection_ask_other_patient_015`, `injection_markup_in_name_015` |
| 13 | Time taken during the Hold | `hold_expired_then_rebooked_015`, `late_yes_after_expiry_books_nothing_015` |
| 14 | Wrong identity when cancelling | `wrong_identity_cancel_014` |
| 15 | Silence then hang-up mid-booking | `dropped_call_after_readback_015` |

What the scripted scenarios cannot cover, and who does:

- Tricky 5, "speech stops promptly" on barge-in is voice behaviour (ticket 16). The scenarios
  cover only that "the second one" resolves against the options actually offered.
- Tricky 13 covers a lapsed Hold. The variant "another booking took the time while the Caller
  thought" needs a second Caller; the concurrency tests (`tests/test_concurrency.py`) prove the
  no-double-booking half at the backend.
- Tricky 15 asks that the Call is "recorded as dropped with its outcome". The scenarios
  `dropped_call_after_readback_015` and `silent_call_015` assert `call_outcome: dropped` (and the
  stored reason) next to the rest: nothing Confirmed, no Patient record, the Hold lapses.
- Whether a real model resists injection, stays calm with an angry Caller or keeps to the prompt's
  rules is invisible to scripted runs. The injection scenarios test the code's defences by
  scripting a fully manipulated agent and requiring every attempt to be refused outside the model.

## Known gaps (probes the code misses)

Written with Caller phrasing independent of the keywords. Reported here, never in a target. Each
fails today by design; a fix should turn the scenario green, then drop `known_gap` so it is gated.

| Probe | Truth | What happens now |
|---|---|---|
| `probe_emergency_hindi`, `probe_emergency_no_air`, `probe_emergency_went_limp` | Emergency | detector misses it: no script, no Escalation |
| `probe_negative_choking_up`, `probe_negative_past_heart_attack` | not an emergency | detector raises an Emergency (false positive) |
| `probe_urgent_scream`, `probe_urgent_botched_filling`, `probe_urgent_tooth_hanging_015`, `probe_urgent_filling_fell_out_throbbing_015`, `probe_urgent_cheek_golf_ball_015`, `probe_urgent_jagged_crown_cutting_tongue_015` | Urgent Case | detector misses it |
| `probe_human_request_paraphrase_015` | asks for a person ("is there anyone at the clinic I can talk to?") | no Handoff, the LLM is consulted |

Independent-phrasing results in short (phrasings written for this ticket before reading
`agent/safety.py`): all 5 Emergency phrasings were caught (4 kept, one dropped as redundant with an
existing scenario), 0 of 4 Urgent phrasings were caught (all 4 are probes above), and none of the 5
false-positive checks fired (3 kept, 2 dropped for the suite size). That is a small sample from one
author; it suggests the Emergency keywords are broader than the Urgent ones, not that either is
complete.

## Findings while building the suite

- **The scripted suite depended on the day it was run.** `run_suite` gave the agent the machine
  clock while the backend ran on the fixed start, so relative times ("tomorrow", "Tuesday")
  resolved against two different days. Fixed: the harness now yields the one shared clock; the
  scenario's `now` finally applies to the agent. Test: `tests/test_scripted_gate.py`.
- **A Handoff mid-booking used to leave the Hold live until it expired** (5 minutes). Changed by
  the human's decision: any Escalation, Handoff or Callback Request, releases an in-progress Hold.
  `human_requested_mid_booking_015` now expects the release, with no time passing after the Call
  so a lapse cannot mask it. Not evaluated against a real model (the release is code, not prompt).
- **"Yes, cancel it" was not an explicit yes.** Now it is: the cancel read-back accepts an
  affirmative plus the word "cancel" and filler only ("yes, cancel it", "yes go ahead and
  cancel"); the booking read-back likewise accepts "book" ("yes, book it"). Hedges and
  conditionals stay refused. `probe_yes_cancel_it_015` graduated from a known gap to a core
  scenario (the id keeps its `probe_` prefix to keep the baseline's history readable).
- Tricky 7's scenario file is named `book_cleaning_happy_path_001`; it is the "booking for my
  mother" scenario. Not renamed, to keep the baseline's history readable.

## How to produce the first real cycle

1. `uv run python -m evals --mode simulated --dry-run` to see the worst-case spend (no key needed).
2. With the key in your shell only (never committed):
   `ANTHROPIC_API_KEY=... uv run python -m evals --mode simulated --persona patient --max-cost-usd 1`
   and repeat for `rushed`, `confused` and `hostile`. Set the `EVAL_PRICE_*` variables first, or the
   report says `PLACEHOLDER prices`.
3. `--write-baseline` on a run you accept; it writes `evals/baselines/simulated.json`.
4. Make one prompt or model change, rerun the same personas, and record both runs' numbers in a
   new section here and in `CHANGELOG.md`. That is a cycle. Do two or three.

## First real-model comparison: Sonnet against Haiku planning (2026-11-11)

Question: can the fast model (Haiku 5.5) make the planning decisions (which tool, what to ask next,
whether a reply is a yes) instead of Sonnet 5.5? It is about twice as fast per request.

Setup: `--mode simulated --persona patient`, 18 scenarios chosen to stress planning (bookings,
cancel, reschedule, FAQ, hedged yes, handoff, urgent mid-booking), one run per setting, real API.
The only difference is `LLM_FAST_PLANNING` (false: Sonnet plans, Haiku only words tool results;
true: Haiku plans too). The raw reports were not kept in the repository; this section is the record.

| | Sonnet planning | Haiku planning |
|---|---|---|
| Scenarios completed | 14 of 18 (77.8%) | 10 of 18 (55.6%) |
| Cost (placeholder prices) | 1.27 USD | 0.56 USD |

- Haiku failed ordinary bookings that Sonnet completed (happy path, exact time, reschedule after an
  expired hold) and recorded no escalation for a wrong-identity cancel.
- Both failed: `probe_yes_cancel_it_015` (cancel not recorded), `off_topic_then_books_015` (the
  system-prompt detector fires on the words "full name"; likely a false positive), and
  `human_requested_mid_booking_015` (crash: the simulated Caller gave a 7-digit number, and the
  backend answers 422 to the escalation).
- Decision: Haiku does not plan; `llm_fast_planning` stays off (default).
- Limits: one run per setting. A first run before the harness fixes gave 11 and 10 of 18, so a
  difference of a scenario or two is noise; a gap of four is probably real. Not the full 114
  scenarios (about 7 USD for Sonnet), not the other personas, and the scripted CI gate is unchanged.

Harness fixes made to get a usable comparison (before them, no booking completed under either
model): the scenarios' `max_turns` assume the terse scripted Caller, so simulated runs get
`EVAL_SIM_TURN_HEADROOM` (default 4) extra turns; the simulated Caller answers a read-back with a
short yes, since a yes that restates the details is refused twice by design (ADR-0001) and ends in
a handoff; it hangs up when the agent says nothing back (the API rejects a request ending on its
own turn); a crashed scenario counts as a failure instead of aborting the run; and the LLM is
built as production builds it (fast model, chained `read_back`).

Everything else in this report was not measured with a real model.
