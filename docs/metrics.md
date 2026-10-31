# Target metrics

From the spec. Measured by the eval harness on the scenario suite unless noted. Recall is always reported next to its false-positive rate so over-flagging cannot inflate it.

| Metric | Target | Notes |
|---|---|---|
| Task completion rate | >= 90% | Scenario suite, text mode |
| Wrong-time or overlapping-Appointment rate | 0% | Whole suite, after every prompt or model change. Also proven deterministically at the backend API seam |
| Emergency recall | 100% | Emergency scenarios |
| Urgent Case recall | >= 95% | Urgent Case scenarios |
| Emergency and Urgent Case false-positive rate | Tracked | Reported alongside recall |
| End-of-speech to start-of-agent-speech latency | p50 < 800 ms, p95 < 1500 ms | Voice pipeline. Measure first, then tune |
| Cost per average 3-minute Call | Tracked and reported | Provider cost per Call, per minute |
| Escalation rate and reasons | Tracked and reported | By Handoff versus Callback Request and by reason |

## Definitions

- **Task completion:** the scenario's expected final database state is reached and no `must_not` rule fired.
- **Wrong-time:** a Confirmed Appointment whose Provider, Service, date or time differs from what the Caller agreed in the read-back, or one that overlaps another Confirmed Appointment of the same Patient.
- **Emergency recall:** share of Emergency scenarios where the fixed script was delivered, an Escalation was made, no scheduling was attempted and any Hold was released.
- **Urgent Case recall:** same, without the 112 script.
- **Latency:** time from the end of Caller speech, as detected by voice-activity detection, to the first audio of the agent's reply (`total_from_speech_end_ms` in the turn log). This is what the Caller experiences and what the spec targets. The ASR-final-to-first-audio figure (`total_ms`) is logged as a diagnostic only, because the speech recogniser's final usually arrives before the VAD closes, which makes it read lower than the Caller's real wait.
