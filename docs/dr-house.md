# Dr. House on-demand briefings

Two local Mac shortcuts POST to the existing loopback-only action server:

- `Dr. House, status report`: `/action/dr-house-status`
- `Dr. House, why is energy high`: `/action/dr-house-energy`

Verification on September 11: both backend endpoints returned relay acceptance in
live tests. Both shortcut entries were created, but the energy shortcut's Get Contents
of URL action stalled before contacting the server, with both loopback IP and hostname.
Voice invocation is therefore NOT verified or ready. Script execution is disabled in
Shortcuts; enabling it as an alternate entry path requires user approval. No permission
or network exposure was changed. The stalled shortcut run was stopped.

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
