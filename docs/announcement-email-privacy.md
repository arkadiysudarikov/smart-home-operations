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
Never use private inbox contents for audible testing.
