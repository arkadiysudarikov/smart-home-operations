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

## Siri execution repair and proof — November 5

The apparent execution stall was a per-shortcut SSH permission prompt in Apple's
`com.apple.WorkflowKit.ShortcutsViewService` process, not the main Shortcuts
editor. Resolved the requested M4 connection prompts for dryer and combo through
the native UI. Both completed read-only `diagnostics` round trips successfully;
their commands were restored to `dryer-unloaded` and `combo-unloaded` afterward.
Their real pending-unload state was preserved, since the user has not confirmed
that those loads have been emptied.

All three canonical shortcuts now have two actions: their fixed SSH command,
then Stop and Output with both the output and Respond fallback bound to the
actual SSH result. Do Nothing was insufficient for an explicit Siri reply.
The numbered washer variants (2 and 3) were deleted in Shortcuts. Verified the
saved, synced M4 library contains exactly three names, correct commands, and
Respond outputs linked to their respective SSH action UUIDs.

A real washer acceptance test at about 4:26 PM Pacific set only its virtual
Unload Reminder tile on, invoked the installed shortcut, then read back all
three appliances. The command completed in 0.516 seconds, the washer tile was
off, dryer and combo states were unchanged, and the washer's original unloadedAt
timestamp was unchanged. Private proof is in M4 runtime
`data/siri_unload_acceptance.json` and `data/siri_unload_installed_proof.json`.

That test also exposed an early-return bug: already-clear state previously
skipped Home tile updates. An explicit acknowledgement now repairs the tile even
when there is no pending unload, without inventing an unload event. Failure to
update Home remains an error; the SSH reply preserves the actionable backend
message instead of reducing it to an HTTP 503 or generic success.

603 repository tests passed. Pending-unload clearing is tested for each of the
three appliance IDs with the other two states unchanged. An unsigned workflow
builder in `scripts/build_laundry_shortcuts.py` makes the action definitions
reproducible. Close editors and replace in place when importing; keeping both or
saving an old open editor can recreate duplicate shortcuts.

The iPhone Mirroring app is behind its existing Mac Touch ID lock. iPhone Siri
voice execution has not been observed; the user must unlock that existing
session before that separate proof surface can be checked. No authentication
protection was bypassed. Mac execution, the Home tile acceptance test, and the
synced M4 workflow definitions were verified independently.

## Additional principle tradeoff

Humble and Farley's reproducibility and fast-feedback principles favor keeping
workflow definitions and contract tests in source rather than relying solely on
GUI edits. Native imports still require per-device consent and careful handling
of existing editors, so the builder deliberately does not import automatically.
This trades one-click installation for preserving existing identities and
permissions. Kleppmann's event/state distinction also motivates repairing a
stale projection without recording a fictional new unload timestamp.
