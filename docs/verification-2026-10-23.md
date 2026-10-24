# Live verification, October 23, 2026

M4 Office Mini runs Homebridge, the action server, the two-minute monitor, calendar delivery and follow-up agents. M5 Office Studio has no loaded smart-home agents. Its old runtime remains as an archive. M5 is still a monitored display target; that does not make it the coordinator.

A read-only comparison identified older database keys present only in the M5 archive. A consistent SQLite backup of that source is now retained on M4 at data/backups/m5-history-reconciliation-20261023/m5-source.sqlite; the pre-reconciliation M4 backup is alongside it. Reconciliation attempted 4,546 snapshot rows, 239 home events and ten session pairs. A later comparison still found older snapshot/event keys absent from the active database, whose oldest capture advances around a fourteen-day boundary. The archive preserves them; do not claim every historical row remains in the active database. All database quick checks passed. Original M5 data was left intact.

The garage had a natural light-on adoption at 17:53 and a successful automatic off at 17:58, independently read back as off. Natural sustained camera occupancy remains under the separate quiet verification monitor. Washer door open, unload reminder switches cleared, detergent full; dryer is still running, so another real completion has not been observed during this audit.

The existing acoustic delivery record matches the stored Office recording hash and records recognition of the synthetic phrase amber beacon lighthouse at 17:33:24. This is one Office HomePod test, not proof of every individual speaker or every alert.

The Homebridge Alarm.com plugin treats generic VideoCameraTriggered (71) as a ring and logs Camera ring detected. That event does not establish a button press. Disabled the spoken-ring source verification and cleared its stale repeat rather than narrating motion as someone ringing. Physical button chime and video recording remain separate and enabled.

Created Announcement Acknowledged as an alias of the existing Acknowledge announcements shortcut. The server acknowledgment returns HTTP 200. Local shortcut execution is blocked by macOS Shortcuts Allow Running Scripts being off; permission was requested before changing that setting. iPhone Mirroring is in use from another Mac, so phone-side sync/recognition has not been verified here.
