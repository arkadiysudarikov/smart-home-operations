# Dr. House on-demand briefings

## September 14 added voice phrases

- `Can I leave` -> `leave`: read-only reported doors/windows/locks and running laundry.
- `Why did you announce that` -> `explain`: last accepted alert, with explicit missing-trigger evidence rather than an invented cause.
- `Anything unusual` -> `unusual`: current high energy and hot/cold readings (85F/55F); not a learned anomaly detector. Unavailable readings are stated.
- `When should I leave` -> `departure`: nearest eligible appointment within three hours, verified owner phone at home, mapped destination and fresh driving ETA. Names the owner, not an assumed Siri speaker.
- `Quiet until morning` -> `quiet`: known routine announcements only, expiring at the next 8 AM Pacific. Safety/help/unknown identifiers bypass this pause. Dry runs never activate it.
- `House, good morning` -> `morning`: current forecast, next presence-eligible appointment within three hours, and reported problems.

All six were created in the Mac Shortcuts library with their SSH action text verified.
390 tests passed. Device sync, first-run permissions and acoustic verification are
separate from saved-library/backend tests; do not infer those from this record.

The voice shortcuts use Run Script Over SSH to the existing Mac SSH service.
The restricted dispatcher POSTs to the loopback-only action server:

- `Status report`: `/action/dr-house-status`
- `Why is energy high`: `/action/dr-house-energy`

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
The old Mac-only shell actions have been replaced. On September 14, the energy
shortcut completed from the iPhone UI and the indoor Intercom relay accepted it.
This does not separately verify Siri recognition or acoustic playback.

Status includes reporting open/unlocked sensors, active laundry with fresh API
heartbeat, energy, the next three-hour appointment for a verified home phone,
driving departure timing when available, and likely rain in the next hour.
Unavailable sources are omitted or explicitly described as unavailable; idle laundry
is never described as newly finished. Calendar failure suppresses personal content.

Energy replies are one short sentence, naming only the largest fresh Sense estimate
and its approximate power. Solar is excluded. Missing evidence yields cause unknown;
a cleared alert yields energy use is normal. Spoken Dr. House prefixes are removed.

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

The iPhone screenshot confirms Run Shell Script cannot execute there.
`Dr. House connection setup` remains a setup-only SSH shortcut.
`dr_house_ssh.py` is a forced-command dispatcher allowing only the eight mode names,
with no command evaluation. The Shortcuts public key must be explicitly authorized
using forced-command and no-forwarding restrictions before these can work on iPhone.
Separate Mac and iPhone Shortcuts public keys were explicitly authorized with a
forced dispatcher and restrict options. Both devices' scripting settings were
approved. A generic whoami command was rejected by the forced dispatcher.

September 14 saved-library audit: Night rounds=night, What changed=changes,
Explain that=explain, Hold my calls=hold, Discharge summary=discharge,
Any complications=complications, Status report=status. Corrected three saved
shortcuts that were still pointing at energy. Hold was not activated during testing.
New commands' iPhone sync/execution verification remains pending: iPhone Mirroring
requested the user's Mac login. No claim of all-command Siri verification is made.

Calendar dry-run September 14: reader access succeeded, zero events returned,
Arkadiy and Maxim phones reported home, no reminders due. Jeanne remains excluded
from the private phone mapping pending positive iPhone identification; her MacBook
must not be substituted. This is not an end-to-end scheduled-event delivery test.
