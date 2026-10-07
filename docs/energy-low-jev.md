# Scheduled Jev energy review and guarded low-energy alerts

User request: announce unusually low energy use on indoor HomePods, using Jev.
The refresh scheduler runs `jev_energy_monitor.py --deliver` after existing
tasks. It collects verified history, calls TypeSafe for bounded advisory reviews,
and can route a qualifying low-energy result through the existing indoor relay.
It does not change ENERGY HIGH or laundry completion rules. Scheduled model
review is not proof of an audible announcement.

## Runtime and readiness

- Fresh Sense whole-house watts supply consumption, not net grid imports.
- Owner presence requires a carried-device confirmation within 90 seconds.
  This is not a complete family-presence feed.
- HVAC requires fresh snapshot and Alarm.com source timestamps. EV requires an
  explicit fresh Sense `category-ev` reading. Missing categories stay unknown;
  an off virtual switch is not evidence that charging is absent.
- Unknown context blocks baseline collection and low-energy announcements.
  Seven matching prior days are required; elapsed time alone does not satisfy it.
- Advisory reviews now run every six hours during daytime (configurable from six
  to twenty-four hours; the default without configuration is twelve). Eligible low-energy
  candidates may instead be reviewed after six hours. Reviews cannot speak.
- Keychain service `com.arkadiy.smart-home.typesafe`, account `smart-home`.
  Each bounded request reserves $0.01 from a persistent $1 monthly local
  allocation before transmission, including failed calls; no retries. This
  conservative 100-call ceiling is not a provider-side dollar spending limit.
- Inputs contain only numeric summaries and boolean/unknown context. No mail,
  calendars, names, or free-form device labels are transmitted.
- Config: `config/jev_energy.json`. State and proof: `data/jev_energy_state.json`,
  `data/latest_jev_energy.json`, and `data/jev_budget.json` in the runtime.
- The scheduler is optional, single-owner, timeout-bounded, and runs after laundry.
  Model failures cannot suppress the existing deterministic laundry path.

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

## Remaining verification gates

1. Observe complete fresh context and accumulate seven matching history days.
2. Observe a real qualifying low-energy episode and its validated Jev decision.
3. Verify actual indoor audio and media resumption; a relay acceptance is not
   acoustic proof. Routine pause applies; follow-up replay is not enabled for
   this new alert. Garage remains excluded.

The earlier Jev announcement experiment produced one unsafe decision in ten
synthetic cases. That is why Jev cannot bypass these deterministic gates.

TypeSafe Choice contract checked September 21, 2026:
https://docs.typesafe.ai/primitives/choice

Verification: `python3 -m unittest discover -s tests -p test_energy_low.py`.
Test responses are fixtures, not live Jev outputs.
