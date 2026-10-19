# M4 Office mini migration - October 19, 2026

Coordinator: m4-office-mini.local. Existing Homebridge identities and pairing
state retained. Monitor, action server, follow-up scheduler, display controller
and guard now run there. Laundry capture uses SSH to the same M4 host.
Runtime: ~/Library/Application Support/SmartHomeMonitor
Source copied from M5: ~/SmartHomeSource
Dashboard: http://m4-office-mini.local:18766/displays

M5 and Garage monitor/action agents stopped, disabled and archived. The M4 tunnel
to M5 was stopped and archived. Runtime history/state copied from M5. Garage
history remains there as an inactive archive. Three restricted Dr House SSH
public-key entries installed on M4. Phone shortcuts targeting M5 need their SSH
host changed to M4.

Verified action health, LAN dashboard health, live washer/dryer/combo capture and
announcement source health. Audible delivery untested. Initial scheduled refresh
had a snapshot timeout and laundry discovery failure; subsequent SSH-worker
capture succeeded. Full scheduled refresh validation remains pending.

Pending: unlock M4 login keychain to transfer UniFi, ChargePoint and Jev credentials.
Sense credential unavailable in source keychain. Grant Calendar full access to
Smart Home Calendar Reader on M4; request open. Calendar LaunchAgent not loaded
until access is granted. Verify audible relay after permissions are resolved.

Rollback property lists retained under ~/Library/Application Support/SmartHomeMigrationBackup
on each host. Old runtime files retained. Avoid enabling multiple coordinators.

Update: desktop-session import installed UniFi, ChargePoint and Jev credentials.
Desktop probe confirmed Calendar reads and UniFi key access. Calendar job is
enabled and logs successful checks. Scheduled snapshot, laundry and announcement
health checks pass. Fast energy analysis now uses runtime data, avoiding iCloud
resource-deadlock errors; explicit full refresh retains external-file scanning.
15 focused refresh-energy tests pass. Tell me more SSH host saved as M4; other
phone shortcut destinations remain pending because UI automation timed out.
