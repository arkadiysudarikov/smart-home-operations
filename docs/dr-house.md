# Dr. House on-demand briefings

Two local Mac shortcuts POST to the existing loopback-only action server:

- `Dr. House, status report`: `/action/dr-house-status`
- `Dr. House, why is energy high`: `/action/dr-house-energy`

Verification on September 11: with explicit user approval, Allow Running Scripts was
enabled in Shortcuts. The stalled Get Contents of URL actions were replaced with Run
Shell Script using `/usr/bin/curl --fail --silent --show-error --max-time 120 -X POST`
against the corresponding `http://127.0.0.1:18765` endpoint. Run as Administrator is off.
Both shortcuts completed from the Shortcuts UI with HTTP success and Intercom relay
acceptance (energy at 16:47, status at 16:48 Pacific). Their saved output and the relay
event log agree. Siri speech recognition and acoustic output were not separately tested.
No network exposure was changed.

Both use the existing indoor iPhone Intercom relay. This does not change device
settings, music routing, scheduled announcements, or the server's network exposure.
The shortcut entry points work on this Mac only. iPhone/HomePod invocation is not
configured by these localhost shortcuts, even if iCloud sync copies them.

Status includes reporting open/unlocked sensors, active laundry with fresh API
heartbeat, energy, the next three-hour appointment for a verified home phone,
driving departure timing when available, and likely rain in the next hour.
Unavailable sources are omitted or explicitly described as unavailable; idle laundry
is never described as newly finished. Calendar failure suppresses personal content.

Energy explains current demand versus the dynamic threshold, identifies only fresh
Sense estimates as suspected contributors, excludes solar from consuming loads,
and never invents the cause of an alert that is no longer active.

Tone: short clinical house rounds, not medical advice or fictional emergency claims.
On-demand requests are not restricted by automatic-announcement quiet hours.
No microphone-based or individual-speaker acoustic verification is implied by relay acceptance.

## Additional commands

The backend accepts these modes, with matching `/action/dr-house-<mode>` POST routes:

- `complications`: Any complications? Only reported openings/unlocked locks, high
  energy, or unavailable critical readings. No speech when those readings are normal.
- `discharge`: Discharge summary. Read-only departure check, running laundry, and
  the existing presence-gated appointment/departure summary. Does not lock or arm.
- `night`: Night rounds. Reporting openings/locks and running laundry; no scene changes.
- `changes`: What changed? Compare equipment states against the last successful
  rounds, at most 24 hours old. Missing readings are never treated as normal. No
  calendar titles or locations are persisted in the comparison baseline.
- `explain`: Explain that. Use the last accepted, nonsuppressed non-Dr-House
  announcement within two hours. Energy gets a current evidence-based assessment;
  personal appointments are re-gated by current presence; other supported events
  distinguish the recorded message from fresh evidence and state missing provenance.
- `hold`: Hold my calls. One-hour pause of known routine Intercom identifiers only;
  on-demand Dr. House, help requests, and unknown safety identifiers bypass the pause.
  No detector settings or alarms are changed. Suppressed events are not queued for replay.

The iPhone screenshot confirms Run Shell Script cannot execute there. A draft
`Dr. House connection setup` uses Run Script Over SSH to the existing Mac SSH service.
`dr_house_ssh.py` is a forced-command dispatcher allowing only the eight mode names,
with no command evaluation. The Shortcuts public key must be explicitly authorized
using forced-command and no-forwarding restrictions before these can work on iPhone.
No new SSH key has yet been authorized. Cross-device key availability must be verified;
the Mac Shortcuts key must not be assumed to be the iPhone key.
