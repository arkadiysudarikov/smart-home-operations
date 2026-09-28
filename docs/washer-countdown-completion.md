# Washer completion repair — September 28

The stored event history showed Cycle Status/MotionDetected becoming false at
10:00 while RemainingDuration was 2002 seconds. The real countdown continued to
29 seconds at 10:38 and zero at about 10:40. The notifier never armed from the
fresh post-recovery cycle signal and therefore never dispatched a wash announcement.
Fresh heartbeat diagnostics did not prove correct completion detection.

Washer finish_signal=countdown_confirmed now uses the already-captured live
RemainingDuration. Positive time with InUse=true establishes a running observation,
independently of the faulty cycle tile. Completion requires observed decreasing
positive time ending at <=180 seconds, followed by two distinct fresh zero captures
at least sixty seconds apart. This is telemetry-based evidence, not a physical
completion receipt; cancellation near the very end can still be indistinguishable.

Missing/invalid values never become zero. Duplicate captures do not advance proof.
Stale observations reset zero confirmation; gaps over five minutes reset countdown
evidence. A large jump from >180 seconds to zero is ambiguous and does not announce.
Starting with an idle machine is silent. Existing quiet hours, venting handling,
indoor Intercom, and playback behavior are unchanged. No historical state is backfilled.
With the current polling interval, confirmation can delay a finish by several minutes.

Tests replay the September 28 failure and cover positive-time protection, confirmation,
single delivery action, startup, gaps, stale/duplicate readings and transient zero.
Audible verification still requires a newly observed real cycle or an authorized test.

## Jev

The dedicated Smart Home API key is in macOS Keychain, not the repository.
scripts/jev_laundry_review.py sends only a fixed synthetic conflicting-signal case.
The real response from jev-1.13.0 selected review, confidence 0.7; probabilities
review 0.8, still_running 0.2, finished 0.0. 417 input and 41 output tokens.
This is an advisory test, not a production reliability evaluation or a model-driven
completion gate. No household identifiers, mail, or calendar content were sent.

The local shared ledger reserves $0.01 per <=2048-byte request, up to $1/month,
before transmission; no retries and no refunds after uncertain failures. This is
a conservative allocation at the currently displayed $0.042/million input tokens
and free output, not a provider-side billing limit or protection for unrelated keys.
Billing and auto-recharge remain unchanged. Recurring Jev energy integration remains
unfinished and is not silently enabled by this diagnostic command.
