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
**In Codex** asks for the request, encodes it as one unwrapped base64 payload,
and submits `codex-request <payload>` through the existing restricted SSH key to
M4. **Codex Request Status** sends the fixed `codex-status` command. Their
Mac-only shell actions were replaced with native SSH actions so the saved
workflows can also execute on iPhone. The request is decoded and validated as
data (UTF-8, nonempty, no NUL, at most 8,000 characters), then passed to the
existing backend through stdin. No client shell program is evaluated. Encoding
is framing, not encryption; SSH provides transport encryption.

The original SSH stdin approach blocked because the native action kept its
stream open. A single validated payload avoids waiting for EOF. Unsupported
commands, invalid encodings, oversized requests and changed sandbox flags are
rejected. The Mac's older acknowledgment-only wrapper now permits only its
original acknowledgment and these Codex requests; unrelated household commands
remain rejected. Existing keys, SSH settings and server exposure are retained.
The phone's existing restricted Dr. House dispatcher accepts the same commands.
This deliberately adds Codex submission to these existing voice identities;
it does not grant a raw remote shell or auto-approve Codex actions.

On November 6, the actual Mac Shortcuts submission finished a harmless request,
and the repaired status workflow displayed "Codex finished. Cross-device Codex
handoff verified." The private job record has exit code zero. iPhone execution,
Siri recognition and a spoken result remain separate verification gates. The
Mac's Siri settings still report that support is being added and Siri is
unavailable until its update completes. A single utterance containing arbitrary
trailing arguments is not established; invocation asks for text afterward.

Muse 11.0 (`com.meta.endo`) exposes native iPhone actions. **Talk to Muse** uses
Start a call; **In Muse** uses Dictate and send. The action's own description
requires unlocking the phone and holding the Action Button again to send.
Start a call returned an internal error during Mirroring; its cause was not
isolated. Apple documents that iPhone microphone access and calls are unavailable
in Mirroring (https://support.apple.com/en-gb/120421). These saved native actions
must be tested on the physical phone with Mirroring disconnected before voice
support is declared working. No guessed URL, recipient, account or keystroke
script was installed.

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

## November 6 follow-up and concrete tradeoffs

Two initial generated email tests reached the inbox but were not acoustically
verified. The phone's receiver was enabled, Confirm Before Run was off, and the
email body, own-sender filter, strict envelope and Indoors destination were
correct. Its email trigger was disabled and re-enabled, then saved. A subsequent
generated message sent at 15:04:13 Pacific was recorded in the office around
15:05:07. Independent local small.en and base.en recognizers confirmed the test
phrase, including the refreshed receiver sentence and delivery-test sentence.
The full recording is SHA-256-bound in runtime proof; incidental ambient
transcripts are not included in the repository. This is one successful automatic
path after re-registration, not proof of every speaker or a new natural cycle.

- Kleppmann's emphasis on explicit data boundaries trades against adding a
  framing step to a simple voice shortcut. It is justified because EOF was not
  reliable here; bounded decoding and injection tests keep user text out of
  shell evaluation, while result confirmation remains separate from acceptance.
- Skelton and Pais's cognitive-load and boundary lens trades against giving an
  existing household voice identity a second capability. The user explicitly
  requested Codex access; exact commands, the dedicated workspace, normal Codex
  approvals and tests that preserve the legacy wrapper's exclusions limit that
  scope. Separate keys are a follow-up if household voice identities need
  distinct Codex privileges.
- Humble and Farley's repeatable-delivery principle trades against the manual
  iPhone trigger re-registration needed to recover this incident. Configuration
  observations and dated acoustic evidence are retained, targeted files are
  backed up before replacement, and the remaining phone-only checks are stated
  explicitly rather than treated as automated proof.
