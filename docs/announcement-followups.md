# Announcement follow-ups

Siri phrases and restricted SSH commands:

- Repeat that: `repeat`
- Remind me again in ten minutes: `snooze`
- Why did you announce that: `why`

These read the bounded local announcement log, never a mailbox. Only known routine
announcement IDs and exact calendar occurrence IDs qualify. Failed, suppressed,
future-dated, malformed, or older-than-ten-minute entries are excluded. House
command acknowledgments cannot become replay targets. Replayed text starts with
“Earlier announcement” and is not represented as a current sensor reading.

Calendar replay requires the exact uncancelled future occurrence still present in
a fresh Calendar snapshot and its owner's mapped iPhone currently home. Unknown
or away presence means no personal replay. Explanations give the recorded trigger
category without personal appointment details; missing sensor provenance is stated.

There is one pending snooze, replaced by a newer request. It is due in 600 seconds,
checked every 30 seconds, and expires after a 120-second catch-up window. Quiet
hours (21:00–08:00 Pacific), routine pauses, and calendar privacy checks still apply.
The request is consumed before relay invocation, including on uncertain delivery,
so it cannot loop or replay after a restart. The acknowledgment notes these limits.

Deployment copies announcement_followup.py, house_briefing.py, dr_house_ssh.py and
action_server.py and smart_home_snapshot.py to the existing runtime, restarts only smart-home-actions, and
installs launchagents/com.arkadiy.smart-home-followup.plist into LaunchAgents.
No new credentials, ports, or SSH permissions are needed. No playback or email
receiver settings change.

Verification: 418 local tests passed, including 12 focused follow-up tests. Saved
Mac shortcut actions use the existing host/key and the commands above. The old
“Why did you announce that” shortcut had only a Text action; it was repaired with
Run Script Over SSH and the orphaned Text input removed. Phone sync, Siri voice
recognition, acoustic response, and a full ten-minute delivery are separate checks.

Live September 16 checks: all three Mac shortcuts invoked their correct deployed
commands and the relay accepted them. Local CM-15 recognition captured the snooze
acknowledgment, the energy replay, and the recorded Energy High explanation. This
does not verify Siri wake-word recognition or iPhone shortcut synchronization.

The deployed-script drift inventory includes the new follow-up module; the full
418-test suite passed again after deployment. The disposable driving event was
accepted once by the automatic calendar scheduler and removed afterward; EventKit
independently confirmed its removal. Relay acceptance alone is not acoustic proof.

The full ten-minute snooze was requested at 12:06:29 Pacific and accepted by the
relay at 12:17:03. The one-shot queue was empty on subsequent scheduler passes.
The 420-second local CM-15 capture beginning 12:14:32 recognized at offset 180
seconds: "Announcement check current energy use", matching the queued energy
replay. Office HomePod reported Playing afterward, with no playback commands sent.
This establishes an audible replay, not exact podcast-position continuity or
proof from every room. The helper completed and exited normally.

The driving reminder was accepted at 12:13:34, before this capture began; its
phrase was not recognized. That acoustic check remains unverified. iPhone
Mirroring still required local authentication, leaving phone synchronization and
Siri activation unverified. GitHub CI passed; the pull request remains draft.

Follow-up at 12:29–12:30: iPhone Mirroring connected, all three shortcuts were
visible on the phone, and each was run there. The deployed relay recorded the
correct repeat/snooze/why command. With no recent qualifying record, each failed
closed with the expected no-recent-announcement response. The local microphone
recognized the repeat and explanation responses. This verifies phone execution,
not spoken Siri name recognition.

A second disposable driving event (Blue lighthouse road test) was automatically
accepted once at 12:34:38, with a 13-minute drive. It was deleted after the test;
EventKit confirmed zero matching events remained. The capture already running
before scheduling recognized a real Energy High cleared message at offset 390
seconds, more than two minutes after that message's relay acceptance, but not the
driving phrase. An extended listening window was started without resending.
Three additional replacement/invalid-timer/deleted-event regression tests bring
the passing local suite to 421 tests.
