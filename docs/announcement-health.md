# Announcement health and SmartHQ update guard

Every fast/full energy refresh runs `announcement_health.py` after the laundry
notifiers, before long provider fetches. It writes
`data/latest_announcement_health.json` and `reports/announcement_health.md`.
No email, audio test, microphone recording, or HomePod controls are used.

The report separates source freshness, latest relay acceptance/failure/skipping,
and audible delivery. Relay acceptance never becomes delivery proof; audible
delivery remains explicitly unverified. The relay entry retains its timestamp
and age so old success is not presented as a current test. Corrupt latest log
records are not hidden by earlier successful records.

The update guard locates the actual SmartHQ listener and Node executable. It
checks the installed plugin version and device.js digest against the reviewed
0.8.1 heartbeat repair. Removed instrumentation or changed plugin code marks
the refresh step degraded and the recovery report as instrumentation-review-
required; it blocks futile restart loops. Future versions are NOT automatically
patched or assumed compatible. Review the replacement, apply an isolated tested
hook, and update the allowlisted digest after live verification.

This detects updates that would silently break the heartbeat dependency; it does
not make a local modification survive every upstream update. The long-term fix
is an upstream-supported freshness signal. The report is diagnostic, not a new
audible failure announcement. A scheduler that stops entirely cannot diagnose
itself; check generatedAt before trusting this report.
