# Low-energy alert with Jev — staged, not enabled

User request: announce unusually low energy use on indoor HomePods, using Jev.
This change contains the detector, minimized Jev Choice request, response
validation, and unit tests only. It does not call TypeSafe, schedule a job,
change ENERGY HIGH, or send announcements. No live Jev low-energy result or
audible delivery has been verified.

## Initial policy

- Measure verified whole-house consumption, never net grid imports. Solar and
  batteries reducing imports must not look like a low-consumption event.
- Use a single consistent source and matching home/HVAC/EV context, within an
  hour of the same local time over the preceding 21 days. Require seven distinct
  days and give each day's median equal weight.
- Candidate: at most 50% of the baseline and at least 0.3 kW below it, sustained
  for 15 minutes. Require readings at most three minutes old and gaps no longer
  than five minutes. Repeated timestamps cannot advance the timer.
- Missing, zero/near-zero (<0.05 kW), invalid, or stale readings are not savings
  announcements. They need separate sensor/outage diagnosis.
- Routine alerts only when someone is confirmed home, 08:00–21:00 Pacific.
  One attempt per episode, six-hour cooldown, and recovery at 65% of baseline
  to rearm. Never infer device failure from low consumption.
- Jev selects unusual_low, expected_low, or review from numeric summaries and
  three booleans only. No names, calendar/email contents, precise location,
  timestamps, or free-form device labels leave the detector.
- Fail closed on absent/invalid responses, mismatched request hashes, model
  changes, or confidence/selected probability below 0.9. Confidence is a
  heuristic, not proof of correctness. Fixed rules retain final control.

Contract: `scripts/energy_low.py:evaluate` returns state plus a decision. A
`needs_jev` result includes a request and its hash. Supply that hash plus the
actual response as `reply` to a second evaluation of the same sample. Recheck
with the actual current time to reject stale samples. `eligible` is not proof
of delivery. Persist returned state atomically before attempting delivery so
uncertain delivery cannot repeat. Runtime scheduling must be single-owner and
serialize evaluations.

## Remaining activation gates

1. Provision TypeSafe runtime credentials securely (never commit or paste keys
   into chat) and approve an API spending cap. Enforce the cap and a request
   cooldown persistently before enabling network calls.
2. Map actual fresh source timestamps and home/HVAC/EV states into this contract;
   reject source disagreements. Verify there is enough matching history.
3. Add bounded API transport, timeout handling, atomic state persistence, and
   single-owner scheduler integration. Runtime remains disabled without them.
4. Route only the fixed announcement text through the existing indoor
   announcement envelope. Register energy_low with pause/snooze/follow-up
   handling. Preserve indoor targets and exclude Garage.
5. Run live synthetic Jev cases, including unknown history and stale telemetry,
   before a controlled end-to-end HomePod test with media resumption verification.

The earlier Jev announcement experiment produced one unsafe decision in ten
synthetic cases. That is why Jev cannot bypass these deterministic gates.

TypeSafe Choice contract checked September 21, 2026:
https://docs.typesafe.ai/primitives/choice

Verification: `python3 -m unittest discover -s tests -p test_energy_low.py`.
Test responses are fixtures, not live Jev outputs.
