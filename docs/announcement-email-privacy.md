# Announcement email privacy guard

2026-09-15: The iPhone receiver previously passed incoming email Content directly
to Intercom. A subject-only trigger is not a safe content boundary. The precise
reason an unrelated email reached that action has not been established.

The live receiver now uses Match Text with `announcement_envelope.PATTERN`, then
an If checking the Matches Text output. Only Matches goes to Intercom Indoors.
Otherwise is empty; there is no unconditional body-to-Intercom action. The email
trigger additionally restricts Sender to the From address verified in the Mac
Relay Home Announcement shortcut, retains subject homeannounce, and runs immediately.

The sender wraps normalized, length-limited generated announcement text with the
matching envelope. Invalid payloads fail rather than falling back to raw content.
This is format isolation plus a sender filter, not cryptographic authentication.

Verification: 399 unit tests pass, including ordinary email rejection, malformed
envelopes, payload limits, surrounding content exclusion and relay encoding.
Saved iPhone actions and sender filter were visually inspected. End-to-end
synthetic negative and positive email delivery tests remain to be completed.
Never use private inbox contents for audible testing.
