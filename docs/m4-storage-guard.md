# M4 UniFi storage guard

On November 3, 2026, M4's Data volume reached approximately 101 MB free.
Homebridge exited with ENOSPC, and the monitoring agents also failed writes.
The UniFi desktop client's DataModel.sqlite-wal was approximately 18 GiB.
Closing that client and completing SQLite wal_checkpoint(TRUNCATE) reduced
its WAL to zero while retaining the 299 MiB database. Both that database and
the Smart Home database passed quick_check. Clearing regenerable Google,
gh and npm caches supplied the initial working space. Approximately 19 GiB
was free after recovery.

The deployed com.arkadiy.smart-home-storage-guard LaunchAgent runs every five
minutes and at login. It checks the specific UniFi desktop database WAL.
At 1 GiB, or 128 MiB when less than 5 GiB is free, it closes the optional
UniFi desktop client if running and requests a SQLite truncate checkpoint.
It never deletes SQLite WAL files, kills the network controller, or changes
Homebridge. Busy or failed checkpoints remain failures in
`data/latest_unifi_storage_guard.json`; they are not treated as recovery.
Free space below 5 GiB is also reported as a failure.

The guard's first deployed run passed with a zero-byte WAL and approximately
19 GiB free. Tests verify threshold boundaries and preservation of committed
rows during a real SQLite WAL checkpoint. Individual HomePod playback and
post-repair iPhone Siri speech are separate verification surfaces.
