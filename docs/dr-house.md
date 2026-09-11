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
