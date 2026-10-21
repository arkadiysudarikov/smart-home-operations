# Laundry and garage repair, October 20, 2026

Washer door changes now retain the last open time in the SmartHQ heartbeat, so an open-and-close between monitor polls clears unload reminders. Fresh open-door evidence clears the pending spoken repeat and snooze, resets the two reminder switches, and prevents another unload reminder. Starting another cycle also clears older pending reminders. Unknown door readings remain unknown; they never imply a closed door.

UltraFresh venting (cycle code 35) is tracked separately from washing. Its long countdown cannot create a fabricated wash completion. A known wash transitioning into venting still produces the wash-finished event.

The detergent reservoir signal is ERD 0x203c: 01 is low, 02 is full. A low transition sends a desktop notification and an indoor announcement during configured waking hours. A night-time low waits until morning for speech. Refill clears the repeating low announcement. The current live reservoir reports full; no false low alert was sent for testing.

Failed laundry notification transport attempts retry up to three times within 15 minutes. Unloading or starting a newer cycle cancels stale completion retries. Transport acceptance is recorded separately from actual audible delivery.

The garage monitor adopts a fully bright light that was turned on directly through Alarm.com without a running timer. It then uses the existing five-minute inactivity timer. Phone association can extend this only up to ten minutes after physical activity; a phone remaining on garage Wi-Fi cannot hold the light on indefinitely. Manual dimming is preserved.

## Deployment and verification

The active SmartHomeMonitor runtime received these changes. The SmartHQ child was restarted after applying the door-history patch to the installed plugin. Reapply `scripts/patch_laundry_door_history.py --apply` after replacing the SmartHQ plugin; the patch makes a backup and is idempotent.

528 automated tests passed, including brief door openings, quiet-hours tank alerts, stale evidence, retry cancellation, venting transitions, garage adoption, and the phone hold limit. A real garage timer expired at 17:57:51 Pacific; a separate Alarm.com read confirmed the light was off.

The dryer had detected a real completion at 17:01:56. A distinctly labeled delivery test reached the receiving mailbox, but microphone capture did not establish audible speech. The iPhone-side receiver remains blocked from inspection by the macOS iPhone Mirroring password lock. Do not call audible delivery verified until that receiver is inspected and a test is heard or independently recognized.

The existing one-week Sideyard/Backyard outage-announcement pause remains in place.
