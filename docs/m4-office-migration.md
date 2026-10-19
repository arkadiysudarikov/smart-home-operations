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

Remaining: Tell me more SSH host saved as m4-office-mini.local. Other voice-shortcut
hosts still point to the old coordinator. Shortcuts UI automation timed out;
remaining shortcut changes and phone sync are unverified.

Rollback property lists retained under ~/Library/Application Support/SmartHomeMigrationBackup
on each host. Old runtime files retained. Avoid enabling duplicate coordinators.
