# SmartHQ 0.8.1 heartbeat repair — September 28, 2026

The installed 0.8.1 plugin had no `recordSmartHQHeartbeat` instrumentation.
Homebridge was receiving changing remaining-time readings while the monitor's
heartbeat file still reported September 23. The notifier correctly refused to
announce from data it classified as stale. Repeated targeted recovery restarts
could not restore missing instrumentation.

The legacy patch bundled unrelated duration/auth/combo changes whose targets no
longer match newer upstream versions. Use the new isolated mode:

```
node scripts/patch_smarthq_remaining_duration.js --heartbeat-only
node scripts/patch_smarthq_remaining_duration.js --heartbeat-only --apply
```

It preserves upstream settings imports (including MAX_TIMER_MS), records successful
REST reads, and records genuine websocket arrivals in the default device handler
used by washer/dryer/combo. Cached getLiveErd reads never refresh the heartbeat.
An unrecognized websocket handler fails before patching. This is local plugin
instrumentation, not an upstream release; future plugin replacement requires
checking and reapplying it. Do not weaken require_smarthq_heartbeat as a workaround.

Deployment: backed up installed device.js to device.js.before-heartbeat-20260928,
applied only heartbeat changes, checked JavaScript syntax, copied the patch tool
to SmartHomeMonitor, and restarted only the validated SmartHQ listener on 40893.

Live verification at 10:03 Pacific:
- Washer, dryer, and combo API-success timestamps advanced to September 28.
- Washer remained in use with about 30 minutes remaining; no false completion sent.
- Notifier dry run: fresh=true, heartbeatFresh=true, actions=[].
- Recovery monitor: classification=recovered, stale=false, awaitingRecovery=false.

This verifies restored laundry source freshness, not an audible HomePod finish
announcement. Await an actual completion for that end-to-end claim. Other
announcement sources and the iPhone Intercom relay were not changed.
