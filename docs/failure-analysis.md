# Failure analysis

Receptio's graceful behaviour when a part fails: **apologise, retry once, then offer a Callback
Request or Handoff** (spec story 66, PROJECT.md Phase 6). This page is the checklist for each
failure mode and what was actually observed. It is seeded only from what ticket 19 saw while
building and testing it. Add a row when a real Call or a new injection teaches something.

A ticked box means a test in this repository shows it, with no provider called. An unticked box
needs real LiveKit, Deepgram, Cartesia or Anthropic, and says what to check by hand.

## How failures are injected

| Seam | What it wraps | Where |
|---|---|---|
| `FlakyBackend` | the backend `httpx.Client`: per method and path, `timeout`, `timeout_after_commit` (the work is done, the reply is lost), `unavailable` (503), `connect_error` | `agent/failure_injection.py` |
| `FailingLLM` | any `LLM`: raises `LLMError` the first N requests, or always | `agent/failure_injection.py` |
| `FakeSpeaker` | the `speak` and `close` the worker hands to `VoiceResilience` | `voice/failure_injection.py` |
| `AsrDropoutWatch` | fed the Caller's speech start, end and final transcript moments | `voice/resilience.py` |

Tests: `tests/test_failure_backend.py`, `tests/test_failure_llm.py`, `tests/test_failure_voice.py`.

## What the agent does

- **The clinic backend does not answer** (timeout, unreachable, 5xx; `BackendUnavailable`). The
  tool is retried once. A write is retried under the same Idempotency-Key (and a later turn's
  retry of the same write reuses it too, as long as the body is the same), so a request that did
  commit is replayed by the backend, never done twice. If the retry succeeds the Caller hears
  "Sorry for the wait." If not, the Caller hears an apology and an offer of staff; a plain yes
  passes the Call to staff. 4xx answers are still refusals, not outages.
- **A booking or cancellation whose outcome is unknown** (confirm or cancel unanswered twice) is
  never claimed as done: "I couldn't tell whether that went through, so I can't say it is done."
- **The LLM fails** (`LLMError`, which `AnthropicLLM` raises for any `anthropic.APIError`). One
  retry after an apology, then an offer of staff. Safety, escalation rules and the Caller's
  answer to the offer are code: none of them consults the LLM.
- **Staff cannot be recorded either.** The Caller is told to call the clinic; the offer stays
  open, so a yes on the next turn tries again.
- **Storing a turn fails.** The Caller is still answered; the stored Call is incomplete.
- **Emergency with the backend down.** The fixed script is still spoken; the Escalation is
  recorded on the Caller's next Emergency turn, or never if there is none.
- **Speech recognition drops out.** First time: apology and "say it again". Second in a row:
  apology and an offer of staff. Any heard turn resets the count.
- **Speech synthesis fails.** One retry with an apology; if that cannot be spoken, a Callback
  Request is recorded (reason "the assistant's voice failed", always a Callback Request, with
  the number if one was given), the Hold is released and the Call is closed.
- **The Caller disconnects.** `TextCall.end` stores the outcome (dropped, with the step). The
  Hold lapses at its expiry (5 minutes). If the Caller had given a number, a Callback Request is
  recorded after the Call is ended (so the outcome stays dropped).

## Checklist

### Slow or failing clinic backend
- [x] Timeout on one request, retry succeeds: apology, normal reply, no Escalation
- [x] Backend stays down: apology, offer, yes records a Callback Request with the failing step
- [x] Confirm times out before the backend commits (twice): not claimed Confirmed; no Appointment, no Patient, no message; Hold lapses at expiry and stops blocking Availability; a yes to the offer releases it
- [x] Confirm times out after the backend committed: the retry (same key) returns the same Appointment; exactly one exists; one message
- [x] Confirm replies lost on both tries, Caller says "try again" next turn: a third request still returns that one Appointment
- [x] Read-back agreement not stored: not treated as a yes; confirm never attempted
- [x] Confirmation Message fails after confirm: the Appointment stands and is still reported
- [x] Escalation cannot be recorded: Caller told so, no pretence, yes retries
- [x] Emergency with the whole backend down: script spoken in full, recorded when the backend returns
- [ ] Real timeout settings (5 s per request, twice) against a real slow backend: the Caller's wait. To verify: run a voice Call against a backend with an injected delay and listen to the silence. Worst case today is two timeouts per tool, 10 s of silence plus the LLM, which exceeds the 2 s budget (docs/latency-budget.md); there is no spoken "one moment" before the retry because the voice driver speaks only after the core's whole turn

### LLM error
- [x] One error: apology and retry, Caller hears the answer
- [x] Down: apology and offer; yes reaches staff; the LLM is not asked again for that
- [x] Down mid-booking: staff take over and the Hold is released
- [x] Down: Emergency script and record, Urgent triage; LLM never asked
- [x] A declined offer: the next turn asks the LLM again
- [x] The Anthropic adapter raises `LLMError` for connection and timeout errors
- [ ] A real Anthropic outage, rate limit (429) and overload (529): only connection and timeout errors are exercised, with a stub. The adapter's own `max_retries=1` (SDK) plus ours means up to four requests, and `llm_timeout_seconds` is 15 s each. To verify: point the key at a bad endpoint and time a Call

### Speech-recognition dropout (voice)
- [x] First dropout: apology and a request to repeat, stored with the Call
- [x] Second in a row: apology and offer; yes reaches staff
- [x] A heard turn resets the count; noise shorter than 1 s and a recognised utterance are not dropouts
- [ ] Deepgram or LiveKit actually reporting a dropout: the worker listens to `session` `error` events that are not recoverable (`stt_error`) and to speech with no final transcript within 4 s. To verify: mute or break the Deepgram key mid-Call and check that either path fires, once, and not while the agent is speaking. Thresholds (1 s, 4 s) are guesses

### Speech-synthesis failure (voice)
- [x] Retry succeeds: the apology is spoken
- [x] Retry fails: Callback Request recorded, Hold released, room closed once, nothing spoken again
- [x] A second error report after the apology gives up instead of looping
- [ ] Cartesia or LiveKit actually raising: `session.say(...).wait_for_playout()` may not raise at all (LiveKit may report only through the `error` event), so the retry may happen only through `tts_error`. To verify: break the Cartesia key mid-Call. Also check whether LiveKit closes the session itself on a non-recoverable error, which would end the Call (dropped, with a follow-up) before our handler runs
- [ ] The apology is not stored as an agent turn (the core is not involved when speech is what failed), so the stored Call lacks it

### Mid-Call disconnect
- [x] Mid-booking: outcome dropped with the step, Callback Request with the number given, no stuck Hold (free again after expiry), no Appointment
- [x] No number given: dropped, no Escalation (nobody to ring)
- [x] After a finished booking: completed, no Escalation
- [x] `/end` slow: retried once, outcome stored
- [ ] A real browser closing, a network drop, and the worker being killed: `finish()` in `voice/worker.py` calls `driver.end()` at job shutdown. To verify: close the tab mid-booking, then query the Call and `/escalations`. A killed worker never runs `finish()`, so that Call has no outcome and the Hold only lapses; nothing sweeps those Calls yet

## What actually broke (observed in this ticket)

Run against the code as it was before ticket 19, with the injected failures:

1. A timeout on `POST /holds/{id}/confirm` raised out of `TextCall.hear`: the Caller would hear
   nothing, and the turn was lost whether or not the Appointment was committed.
2. A 503 from `read-back` was treated as a refusal (`BackendError`), the same as the backend
   saying no, so the LLM was told "error" and nothing distinguished "no" from "no answer".
3. A backend that was down failed the storage of the Caller's own turn, so even the Emergency
   script was lost: `hear` raised before anything was spoken.
4. An LLM error raised out of `hear`; likewise a 503 on `/availability` and on `/escalations`.
5. `TextCall.end` had no retry, so a slow `/end` lost the Call's outcome.
6. The dropped-Call follow-up (ticket 17's open item) did not exist; the `dropped_call_after_readback_015`
   scenario asserted there was no Escalation. It now asserts a Callback Request.

How each was handled is in the checklist above.

## Interactions with ticket 13 (voice/worker.py)

Ticket 13's worker wiring (playout waits in `speak`, `ctx.delete_room` at close, settle on
shutdown) is still untested against LiveKit, and this ticket adds to the same file. What was
added: an `error` event handler, a 0.5 s polling task for the recognition watch (cancelled at
shutdown like the limits task), and a `VoiceResilience` that reuses ticket 13's `speak` and
`close`. Things to check by hand together:

- `close` (room delete) runs from the synthesis-failure path as well as from the duration cap;
  both end in `finish()`, which settles the admission once. Check that a Call closed by the
  failure path is settled, and that `driver.end()` then adds nothing (the escalation already
  exists, so the outcome is escalated, not dropped).
- A recognition apology or offer is spoken with `speak`, which does not allow interruptions and
  waits for playout, so the Caller cannot barge in on it.
- An offer made by the agent after a failure is answered through the normal turn path; if the
  duration cap's warning is also due, both are spoken.
