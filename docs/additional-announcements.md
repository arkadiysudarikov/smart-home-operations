# Additional indoor announcements

The additional-announcements job runs immediately after announcement-health in both
refresh plans. It uses the existing restricted indoor Intercom relay, not a mailbox
reader or a new audio player. No changes to playback controls or recipient routing.
08:00–21:00 Pacific only; routine snooze/pause is honored. Attempts are persisted
before delivery; uncertain failures are not retried. These IDs cannot be replayed by
the existing follow-up shortcut, avoiding stale doorbell and calendar disclosures.

## Laundry connection

Enabled in config/additional_announcements.json. A fresh health report must show a
laundry capture, active-source, or instrumentation fault continuously for ten minutes.
One warning per episode; recovery is announced only if the warning relay was accepted.
Stale reports and monitoring gaps reset the episode. Quiet-hour warnings are consumed,
not queued for morning. A healthy startup is silent. This diagnoses telemetry, not
the appliance's physical health, and cannot announce if the relay or monitor is down.
Relay acceptance never proves audible delivery.

## Calendar changes

Private runtime calendar config must set changesEnabled=true. The updated EventKit
reader adds recurrence metadata. A fresh baseline comparison notices time, location,
or status changes to unique non-recurring appointments in the next 24 hours. Recurring
and detached occurrences are excluded rather than guessed. New appointments, missing
events, deleted events, old-reader snapshots, stale baselines and first runs are silent.
Cancellation requires explicit EventKit cancelled status; providers that remove cancelled
events cannot produce cancellation announcements through this reader.

Only the owner's mapped iPhone at home qualifies; presence is rechecked immediately
before relay. Away changes aren't queued for arrival. Messages contain the owner's
name and "an upcoming appointment was updated/cancelled. Check Calendar for details."
No titles or locations. Fifteen-minute per-event repeat limit and at most three change
notices per check, without queuing excess changes. Baseline files contain
hashes/timestamps rather than appointment text and are mode 0600.

## Doorbell — staged, disabled

Both enabled and sourceVerified must be true and cameraId/component must be mapped.
Only an exact Alarm.com `Camera ring detected for NAME (ID)` event from that component
qualifies. Motion and generic contact events never count. Events come from the existing
read-only smart_home.sqlite event history, must be <=90 seconds old, and are deduplicated
by event key with a 30-second cooldown. First run consumes historical events silently.
Physical ring verification is still required; polling can miss events outside that window.

## Freezer — staged, disabled

No verified freezer sensor is currently mapped. Do not substitute a room thermometer.
The adapter contract is data/verified_freezer_temperature.json with ok=true, sensorId,
observedAt (timezone-aware ISO), and fahrenheit (finite number). observedAt must be the
actual sensor observation time, not the file read time. Mapping must match the explicitly
verified sensorId, with enabled and sourceVerified true.

Above 15 F for thirty minutes warns once; below/equal 10 F clears. Thresholds are
configurable convenience alerts, not food-safety assurances. Invalid or stale samples
reset the episode. No sensor adapter or hardware has been invented or enabled.

## Verification

Synthetic tests cover persistence, privacy, timing, quiet hours, false-source rejection,
deduplication and recovery. Silent runtime checks are separate from audible end-to-end
proof. No HomePod test sound or fake appointment is required to deploy this code.
