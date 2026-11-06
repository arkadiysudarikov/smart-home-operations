# Adaptive household operations

An independent one-minute LaunchAgent refreshes announcement health, obtains
Alarm.com automation/recording rules at most every fifteen minutes using the
existing login, and writes local consistency and laundry-opportunity reports.
It never changes device, recording, lock, or security settings.

Routine reminder repeats due together are combined. JEV's recent accepted
attention choice can order them; current source checks still decide whether
each condition exists. Resolved tank/high-energy episodes are removed, stale
sources are held silently, and laundry door/new-cycle evidence cancels old
completion repeats. Existing capped backoff and explicit acknowledgment remain.
Safety, help, doorbell and new laundry-completion delivery stay immediate.

The user's reported detergent refill resolves only cloud readings whose
appliance-update timestamp predates that report. It does not invent a fresh
full-tank reading or suppress a genuinely newer low-tank episode.

Laundry opportunities require ten minutes of distinct fresh meter readings,
at least 1 kW of measured export, synchronized PV/consumption/battery readings,
and no material battery discharge. Re-reading one meter sample cannot extend
the window. Automatic advice waits until the laundry appliances report idle.
It is a solar opportunity, not a price forecast or an appliance command. The
on-demand JEV laundry-time answer reads the same evidence gate.

Health incidents must persist for ten minutes of continuous observations.
New actionable incidents share one digest and a local notification, once per
episode. Recovery clears the episode silently; ordinary healthy checks, stale
optional advice and previously known camera outages produce no new speech.
Fresh positive owner presence, daytime hours and routine pauses gate delivery.
Model requests use the existing shared budget/cooldown and fixed categories;
neither private rule text nor voice requests are sent to JEV.

Consistency contracts detect fixed Alarm.com garage-off timers that oppose
camera occupancy, missing/paused doorbell recording, broken Homebridge camera
mirror hooks, opposing Home actions, and an unguarded Away departure action.
Apple Home's reviewed snapshot has a separate age and scope; its absence never
becomes an all-clear. Bridge configuration is not pairing/control proof.

## Siri interfaces

The local `siri_codex.py` entry point accepts dictated text through standard
input and starts a Codex CLI request in `Documents/Codex Voice Tasks`. It keeps
ordinary on-request approval and workspace-write sandboxing, saves private job
records, and offers a separate status command. Dictation is never interpolated
into a shell command. There is no listening server or new remote credential.
Two Mac shortcuts are installed: **In Codex** asks for the request, submits it,
and confirms acceptance; **Codex Request Status** reads the latest result.
Both were exercised through the actual Shortcuts UI on November 5, 2026:
the submitted harmless request finished and the status shortcut displayed
"Codex finished. Siri shortcut handoff verified." This does not prove Siri
recognition. The Mac's Siri settings currently say support is being added and
Siri is unavailable until its update completes. These shell-script shortcuts
are Mac interfaces; iCloud name synchronization does not make them executable
on iPhone. A single utterance containing arbitrary trailing arguments is not
established; the supported interaction asks for text after invocation.

Muse 11.0 (`com.meta.endo`) has a native call action and a connected WhatsApp
channel. No Muse-specific Mac Shortcuts action was found. iPhone-only action
inspection remains blocked by iPhone Mirroring reconnecting to its Mac-password
screen. No guessed URL route, recipient, new account or background keystroke
script has been installed.

## Engineering tradeoffs requested by the owner

These are design interpretations, not quotations or claims that every author
would prescribe this implementation.

| Lens | Tradeoff and mitigation |
| --- | --- |
| Farley, Modern Software Engineering | The independent scheduler adds concurrency and state. Keep the decision functions testable and explicit instead of coupling them to provider collection. |
| Humble and Farley, Continuous Delivery | A live household needs incremental deployment, not a full restart. Use a backed-up, hash-checked deployment of reviewed files, then verify real scheduled execution. |
| Forsgren, Accelerate and Frictionless | More checks can create friction and alert fatigue. Debounce incidents, notify once, and preserve a quiet healthy path. |
| Skelton and Pais, Team Topologies | One controller spanning several products increases cognitive load. Keep provider adapters and ownership boundaries explicit; do not let the advice model become a settings owner. |
| Kleppmann, Designing Data-Intensive Applications | At-most-once notification attempts may lose an uncertain delivery. Persist before dispatch, retain diagnostic evidence, and avoid automatic duplicate speech. |
| Larson, An Elegant Puzzle | Bespoke voice and home integration increases maintenance cost. Reuse the existing runtime/authentication and expose incomplete coverage rather than adding another account or remote server. |
| Meadows, Thinking in Systems | Feedback delays and noisy sensors can cause oscillation. Use sustained observations, separate source and delivery freshness, and resolve only the episode supported by evidence. |
| Cagan, Inspired, Empowered and Transformed | A broad smart-home request risks becoming a feature checklist. Measure outcomes: understandable speech, reminders stopping after resolution, useful solar advice, and fewer interruptions. |

Residual limits: this does not infer stationary-person occupancy from camera
motion, provide per-announcement HomePod playback receipts, establish unverified
meter wiring, or autonomously change security policy. Siri submission remains
a separate user-directed interface, not an expansion of unattended home control.

## Validation and live coverage

The independent scheduler has executed successfully in the deployed runtime.
Alarm.com returned 27 automation rules and 12 recording rules using its exact
JSON API media type; mixed Accept values had returned HTTP 500. The four garage
light rules have no fixed off action, and both doorbell recording rules are
enabled. This verifies saved rules, not natural person detection or stationary
occupancy behavior.

The current Apple Home review lists six automations and directly inspects the
garage motion, garage no-motion and last-person-departure actions. The motion
rule has no fixed off timer; clearance resets the mirror. Departure invokes the
existing dog-aware controller and turns Garage Light off. The remaining actions
are not represented as fully audited. A daily 09:00 Pacific read-only Home UI
review refreshes only observed metadata; the independent monitor debounces and
reports actionable findings. No rule, door, lock or alarm was operated by checks.

Local bridge TCP and household-action HTTP probes establish reachability only.
The monitor does not confuse them with actual appliance control, Home pairing,
or HomePod playback receipts. Sustained natural solar advice and new real fault
notifications remain unobserved until their conditions occur.
