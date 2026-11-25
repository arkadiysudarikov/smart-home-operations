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

## November 25 SmartHQ 0.10.1 recovery

The active SmartHQ child was running an uninstrumented 0.10.1 module after
a Node/plugin change. That release still sends real REST results and WebSocket
arrivals through the heartbeat-only patch points. The reviewed 0.10.1 hashes
identify both the uninstrumented and instrumented module. Build the patch
against a staging copy, run `node --check`, and retain the original before
replacing the installed module and restarting the SmartHQ child. The patch
retains the last observed open time after a laundry door closes.

A household laundry replay through Mail and iPhone Intercom was recorded by the
Office CM-15 at 11:55 Pacific. The replay used the text of the real November 24
washer finish. The M4 now asks macOS to open Mail in the background if the app
is closed before invoking the sending shortcut. A timeout remains uncertain.

Continuous Delivery tradeoff: the instrumentation still modifies a third-party
module on the M4. It restores local evidence without waiting for upstream support,
at the cost of reviewing future module updates. The exact hashes, isolated child
restart, staged syntax check, and original module backup constrain that risk.
The Mail preflight adds a bounded local check and startup delay to cold sends.
