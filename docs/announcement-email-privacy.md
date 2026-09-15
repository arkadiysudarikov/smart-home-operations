# Announcement email privacy guard

2026-09-15: The iPhone receiver previously passed incoming email Content directly
to Intercom. A subject-only trigger is not a safe content boundary. The precise
reason an unrelated email reached that action has not been established.

The live receiver now uses Match Text with `announcement_envelope.PATTERN`, then
an If checking the Matches Text output. Only Matches goes to Intercom Indoors.
Otherwise is empty; there is no unconditional body-to-Intercom action. The email
trigger additionally restricts Sender to the actual sender verified in delivered
test-email headers, retains subject homeannounce, and runs immediately. The Mac
shortcut's displayed From account differed from the delivered sender; using that
displayed account initially blocked positive tests. The iPhone filter was corrected
to the delivered iCloud custom-domain sender, with the payload guard unchanged.

Live iPhone inspection also found the opening regex characters transposed during
UI entry. The pattern was re-entered with a UI acknowledgement after every
character; the opening `(?<=` and closing lookahead were visually verified before
saving. Unit tests of the source regex do not prove the separately edited iPhone
expression is identical; always inspect and test the actual receiver.

The sender wraps normalized, length-limited generated announcement text with the
matching envelope. Invalid payloads fail rather than falling back to raw content.
This is format isolation plus a sender filter, not cryptographic authentication.

Verification: 399 unit tests pass, including ordinary email rejection, malformed
envelopes, payload limits, surrounding content exclusion and relay encoding.
Saved iPhone actions and sender filter were visually inspected. After the regex
repair, the user reported hearing the test. The mic captured 45 seconds but did
not recognize the phrase; repeated tests had very low input peaks. Independent
audible payload-isolation and playback-resume verification is still incomplete.
The local temporary mic helper was updated with a disconnected-input guard and
capture-only gain. Permissions were granted and recording was confirmed before
the next test. In a fresh 45-second window it recognized "Only this sentence
should be spoken", which is within the marked synthetic payload, with 2160000
frames and raw peak 0.0052084457. This independently confirms audible payload
delivery, not the full sentence or playback resumption. macOS CM-15 input was
already at 100%; no output/HomePod volume was changed.
An isolated unmarked-email test then captured 2160000 frames and recognized only
"Should be", not the unmarked "red balloon" phrase. This is not conclusive proof
of silence: ambient/late audio and incomplete speech recognition remain possible.
No private email contents were used as test payloads. Playback resumption remains
unverified in this test session.

Later live verification: Office HomePod reported Playing before testing and after
the dummy relay. After the energy/more sequence, the Home controller reported
Paused. This observation alone did not establish a playback regression; the
precise transition/cause was not established. The iPhone Tell me more shortcut contains
only Run Script over SSH with command more, and produced a fresh dr_house_more
accepted event at 11:35:13 Pacific. Do not add unconditional Play as a workaround:
that would start audio when the user had intentionally paused it.

11:50 Pacific retest: no playback settings or controls changed. CM-15 capture
started at Unix 1789498237.945; the synthetic marked email was sent at 1789498265.
The 90-second recording had 4320000 frames. Local offline recognition of its
60–90 second segment returned: "Announcement test blue lighthouse only this
sentence should be spoken". Neither surrounding dummy-text sentence was in
that transcript. The delayed arrival explains why a 45-second capture could
miss delivery; this is not evidence of a broken microphone or failed relay.
Office HomePod reported Playing before and after the confirmed audible test,
without a Play command. No playback failure was reproduced. This confirms the
announced payload acoustically and playback state via the controller, not
individual acoustic verification of every speaker or podcast position recovery.
The recording remains local in /tmp, not in the repository.
Never use private inbox contents for audible testing.
