# Safety layer: Emergency and Urgent Case

Implemented in `agent/safety.py` (detector, fixed script) and `agent/text_mode.py` (`TextCall.hear`). Terms follow `GLOSSARY.md`: an **Emergency** is life-threatening and hears the 112 script; an **Urgent Case** needs same-day dental attention and does not.

## Behaviour

The detector runs in code on every Caller turn, before any other state (including an Escalation already under way, or a Callback Request waiting for a number) and before the LLM is consulted.

| | Emergency | Urgent Case |
|---|---|---|
| Spoken | The fixed script only (`EMERGENCY_SCRIPT`, version `emergency-script-v1`). Nothing else on that turn, not even a question | A fixed staff line (`URGENT_HANDOFF_SPEECH`) during Clinic Hours; outside them, the usual request for a number, then the Callback Request line. No 112 script |
| Escalation | Recorded at once. Handoff in Clinic Hours, otherwise a Callback Request with whatever number is known (possibly none: the Caller has been told to hang up, so no question is asked) | Handoff in Clinic Hours, otherwise a Callback Request after asking for a number |
| Hold | Released (`POST /holds/{id}/release`) | Released |
| Scheduling | Never entered or resumed; the LLM is not consulted again | Same |
| Medical advice | None: everything spoken is fixed text | None |
| Reason | `Emergency: ...` | `Urgent Case: ...` |

An Emergency turn after an Urgent Case, or after an ordinary Escalation, still plays the script and records its own Escalation. A repeated Emergency repeats the script but escalates once. If releasing a Hold fails, the Call carries on (the Hold expires within 5 minutes) rather than delaying the script.

Decision for the human: the Emergency Escalation never waits for a phone number. The alternative (ask for one) would put a second utterance on the Emergency turn, against the ticket.

## What the detector is

An ordered table of regular expressions over normalised text (lower case, apostrophes dropped, punctuation as sentence boundaries). Emergency rules cover breathing and airway, fast or throat-ward swelling, uncontrolled bleeding, collapse, trauma, heart, stroke, seizure, allergic reaction and self-harm. Urgent rules cover severe pain, a knocked-out or broken tooth, abscess or facial swelling, post-extraction bleeding and plain "urgent". Emergency wins over Urgent in one turn. A "no" or "not" just before an Urgent or hedged-Emergency cue cancels it ("nothing urgent"); rules worded as their own negation ("not breathing") are never cancelled, so "no, he is not breathing" still fires.

It leans toward over-flagging: a false Emergency costs one needless call to staff, a missed one can cost a life.

## What it cannot cover

It is a keyword and rule detector. Each item below is a probe scenario or strict xfail, so the limit is executable and an unexpected pass fails the suite.

- **Paraphrase and euphemism with no keyword**: "he just went limp", "I can't get any air in", "my chest is squeezing", "it hurts so much I could scream", "the filling has gone really wrong and I need someone today". Closing these means adding rules, one phrase at a time, or a classifier; the second would need its own recall evidence and must still never replace the code path.
- **Other languages**: English only (spec), but a Caller in distress may say it in Hindi or Kannada ("meri saans nahi aa rahi").
- **Obfuscation or spelled-out speech**, and anything speech-to-text mangles past a rule.
- **Context**: a past or hypothetical event ("I had a heart attack last year") and figurative speech ("choking up laughing") are flagged, because a keyword cannot see tense or irony. These are the known false positives.
- **Severity judgement**: it cannot tell a minor chipped tooth from a serious one beyond the phrases chosen. "Chipped" is deliberately not Urgent; "cracked" and "broken" are.
- **Symptoms spread across turns** are not combined. Each turn is judged alone ("my face is a bit swollen" then "it is spreading to my neck" is caught on the second turn only because that sentence is self-contained).
- **A bare "emergency"** is Urgent, not Emergency: "I have an emergency" goes to staff triage, "this is a medical emergency" gets the script.
- **Third parties and metaphors** are treated like first-person speech.

## Measured

Scripted-LLM scenarios in `evals/scenarios` (run by `tests/test_safety_scenarios.py`, which prints the report with `-s`):

- Emergency recall: 15 of 15 (100%). Urgent Case recall: 12 of 12 (100%). Urgent Cases given the Emergency script: 0.
- False positives: 0 of 40 negative scenarios flagged (these include "emergency meeting", "emergency contact number", tooth sensitivity, bleeding gums when flossing, "out of breath", "nothing urgent", and every other scenario in the suite).
- Probes written to find gaps (`known_gap: true`, kept out of the targets): 7. None is handled correctly today: 3 Emergency misses (euphemism, paraphrase, Hindi), 2 Urgent misses (idiom, no keyword), 2 false positives (past heart attack, "choking up").

Read these as a regression gate, not as field accuracy. The scenarios were written by the same person who wrote the rules, so 100% on them says the rules do what their author intended; it is not an estimate of recall on real Callers. The probes are the more honest signal of the gaps, and the real recall on an independent sample will be lower. Independent phrasings (for example from the LLM-driven simulated Caller, when that exists) are the next step.
