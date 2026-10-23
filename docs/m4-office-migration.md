# M4 Office mini migration - October 19, 2026

Coordinator: m4-office-mini.local. Existing Homebridge identities and pairing retained.
Monitor, actions, Calendar reminders, follow-ups, display controller and guard run here.
Laundry capture uses SSH to this same M4 host to retain its discovery context.
Runtime: ~/Library/Application Support/SmartHomeMonitor
Source: ~/SmartHomeSource, branch codex/m4-office-migration
Dashboard: http://m4-office-mini.local:18766/displays

M5 and Garage monitor/action agents stopped, disabled and archived. M4 action
tunnel to M5 stopped and archived. M5 runtime state/history copied to M4;
Garage history remains on Garage as an inactive archive.
Three restricted Dr House SSH public-key entries installed on M4.

UniFi, ChargePoint and Jev credentials imported through the desktop session.
Background-session credential access, Calendar access and Calendar scheduler passed.
The source Sense keychain item was unavailable, but scheduled Sense capture passed.

Final scheduled refresh: 15:27:51-15:28:28 Pacific, 22 steps, all successful.
15 focused tests and display policy validation passed. Fast scheduled analysis
uses runtime data; explicit full refresh retains external-file scanning.
Action health, dashboard health, laundry capture and announcement source health pass.
Audible HomePod delivery has not been tested during this migration.

Voice shortcut migration completed: all 32 house SSH shortcuts were reopened
and verified with host m4-office-mini.local after restarting Shortcuts. This
includes the backup copies and Dr. House connection setup. Five text-only
shortcuts were repaired by adding the configured SSH action with their existing
command: House, good morning; Quiet until morning; When should I leave;
Backup - Anything unusual; Backup - Anything unusual 2.

Live Check the laundry shortcut completed successfully. M4 recorded
announcementId=dr_house_diagnostics with relay acceptance at 15:55:36 Pacific.
This verifies shortcut execution and relay acceptance, not audible playback.
Phone iCloud synchronization and Siri recognition have not been independently tested.

Rollback property lists retained under ~/Library/Application Support/SmartHomeMigrationBackup
on each host. Old runtime files retained. Avoid enabling duplicate coordinators.

## October 23 verification and repairs

- CM-15 USB microphone detected as M4's default input; microphone and local
  speech-recognition permissions are granted. Initial relay test was accepted,
  but its phrase was not recognized acoustically. Audible delivery is pending.
- Reviewed the installed SmartHQ 0.9.0 door-history delta: it preserves existing
  heartbeat fields and adds `lastOpenedAt` only on changed open-door events.
  The health check now recognizes that exact reviewed digest and still rejects
  unreviewed code or versions.
- Disabled `laundry_host` remote delegation on the M4 coordinator. Capture and
  the local recovery watchdog now have the same owner. The watchdog restarted
  only the validated SmartHQ child on port 40893, then all three appliance
  heartbeats became fresh and recovery reported `recovered`.
- Recovered the malformed history database into a separate database and verified
  SQLite integrity before replacing it. The original, recovered database,
  unassigned records, and config backup remain in
  `~/Library/Application Support/SmartHomeMigrationBackup/database-recovery-20261023-164632`.
  Recovery does not establish that every historical record is intact.
- The post-recovery refresh completed with no required failures; its only
  optional failure was announcement health sampled before SmartHQ recovery.
  A subsequent live announcement health check passed with no faults. The
  16:51:27–16:52:16 and 16:54:16–16:55:06 Pacific scheduled refreshes then
  completed with no required or optional failures.
- Re-registered Apple's Mail application after repeated relay failures logged
  a missing Mail action definition. Both direct and action-server test calls
  were accepted. The same Mail log warning also occurred during a successful
  send, so it does not establish the cause of the earlier failures. Actual
  Intercom playback and recurrence remain unverified.
- Three fresh 120-second CM-15 recordings completed with microphone and on-device
  speech permissions granted. None recognized the synthetic announcement phrase.
  The third recording used input gain 100%; this is not audible-delivery proof.
  A local speaker calibration temporarily unmuted M4 speakers, then restored
  their original mute setting.
- iPhone Mirroring connected and showed synced household shortcuts, but input
  failed with `noWindowsAvailable`. Restarting Mirroring returned to its Touch ID
  lock; receiver automation inspection and iPhone execution remain unfinished.

- Existing PR #97 separately fixes Siri acknowledgement returning HTTP 500 after
  clearing reminders. The deployed action server contains the shared direct
  acknowledgement handler for both URLs; this verification preserves that fix.
