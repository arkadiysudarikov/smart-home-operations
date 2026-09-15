# Household announcement coverage

Enabled implementation:
- Existing laundry, venting, energy, bubbler, open-garage/gate, cooling/open-window,
  surplus and security-trouble reminders retain their existing policies.
- Calendar departure and video reminders are enabled for mapped Arkadiy/Maxim/Jeanne
  phones. Jeanne's identity was confirmed by the user September 15; away/stale
  phones suppress their owner's reminders.
- Rain within the next hour plus an open garage/window/slider: NWS Ventura grid,
  rain wording and precipitation probability >=60%, refresh at most every 15 minutes,
  no repeat until the forecast stops predicting rain, daytime only.
- Temperature: reported indoor ambient >=85 F or <=55 F for at least 30 minutes
  of fresh observations, once per episode, daytime only. Invalid readings are ignored.
- Internet restored: two independent HTTPS reachability probes, previous healthy
  baseline, all probes failing for >=4 minutes, then two reachable samples. Gaps
  over five minutes reset the test; startup and short failures never announce.
  This measures internet reachability from the monitor, not proof of an ISP outage.
- Dashboard help button: explicit click announces a fixed message indoors through
  the established relay. This does not contact emergency services or guarantee delivery.

Not enabled because required event sources have not been established:
- Garage close failed: needs a recorded close command plus fresh post-command position.
  An open garage alone is not failure evidence.
- Alarm did not arm: needs a recorded arming attempt plus fresh post-command panel state.
- Doorbell/package: current activity sample does not establish reliable fresh events.
- Smoke/CO and leak: no corresponding reporting detectors were found in the inspected
  Alarm.com sensor inventory. An announcement would only supplement detector alarms.
- Apple's device-level names remain separate from UniFi aliases. Jeanne's confirmed
  phone is named Jeanne's iPhone in UniFi; this does not rename it in Find My.

Tests use synthetic events, not real alarm/garage actions. New real-world triggers still
require event-to-audible acceptance; Intercom transport acceptance is not a read receipt.
