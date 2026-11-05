# Laundry completion and unload reminders

Washer, dryer and garage combo tiles retain their existing accessory IDs and use
appliance-specific Cycle Finished / Unload Reminder names. Cycle Finished remains
a two-minute event for automation. Unload Reminder activates after the existing
20-minute delay and stays active until a fresh door-open observation, explicit
per-appliance unload acknowledgement, or a newly started load supersedes it.
A stale snapshot cannot clear the reminder. Old expired reminder pulses migrate
to latched state when the persisted state still says unloading is pending.

Siri shortcuts: “I unloaded the washer”, “I unloaded the dryer”, and
“I unloaded the combo”. Their fixed commands use the existing restricted SSH
entry point and the POST-only laundry-unloaded endpoint. Each acknowledges only
its named appliance and clears its pending finish/unload deliveries and repeat
announcements. No global announcement acknowledgement is used.

The system infers unloading from an opened door; it does not detect clothing.
User acknowledgement is recorded separately as unloadedBy=user. Transport
failure is reported separately from recording acknowledgement.

## Principle tradeoff

Kleppmann's distinction between events and derived state matters here: a cycle
completion pulse represents an event, whereas an unload reminder is persisted
state. Keeping both introduces more state and concurrency work than a timer-only
implementation. Existing per-appliance file locks, atomic writes, durable delivery
jobs and regression tests mitigate lost updates and stale reminders. This also
follows Meadows' feedback lens: the reminder remains visible until observed or
explicit feedback resolves the condition, rather than disappearing on a timer.
Longer appliance-specific names trade compact Home tiles for explicit meaning at
the user's request; existing accessory identities are preserved.
