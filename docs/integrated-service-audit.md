# Integrated service audit — November 6, 2026

Combined the adaptive-operations branch with current SCE imports, signed battery
accounting, persistent laundry reminders, and Siri acknowledgement repairs.
619 tests pass together. No merge conflict required discarding either behavior.

## Current evidence

- Homebridge and the ten configured integration groups have reachable bridges;
  this does not establish physical device operation.
- SCE manual intervals cover through midnight November 5. UtilityAPI still ends
  June 16. Refreshing the API does not renew that coverage.
- Solar scope remains unresolved physically; current alerts still report a gross
  PV/Sense gap. The earlier signed-storage comparison is a hypothesis to verify.
- Alarm.com reports three trouble conditions. Retirement identities are not yet
  confirmed, so no device or alarm coverage was removed.
- Mopar is disabled. ChargePoint's fetcher reports its last refresh inside its
  three-hour portal interval; that does not establish current charging state.
- JEV's low-use detector has fresh power but unknown EV context. The conservative
  rejection is correct. Do not turn missing EV evidence into false/not charging.
- Fixed a separate JEV bug: prefer Sense sampleAt over capturedAt when present,
  so freshly downloaded old measurements cannot authorize energy advice.
- Siri consent was repaired on the Mac. The iPhone typed Siri invocation and
  reply were verified on November 6; microphone recognition remains separate.

## Principle tradeoff

The shared integration branch improves the fast feedback and deployability
emphasized by Humble/Farley and Accelerate, but retaining a stack of unmerged
branches adds coordination cost (Larson/Team Topologies). Validate the combined
head before merging the parent stack; preserve targeted runtime backups and byte
checks during deployment. Kleppmann's event-time distinction motivates using
measurement time instead of retrieval time. Legacy captures without sampleAt
retain their older timestamp fallback for compatibility; explicit old or invalid
sampleAt values cannot use that fallback. Retiring that fallback requires a
separate producer-coverage check.

## ChargePoint and source freshness follow-up

Found that a failed driver login could fall back to the same historical CSV and
stamp it as captured now, postponing the next real portal fetch. CSV import now
retains source file time and content hash, while importedAt records processing.
Reimporting identical content cannot renew its capture time. Login-blocked
backoff is preserved even after a successful historical fallback. Service health
checks the underlying ChargePoint capture and Sense measurement timestamps rather
than the latest wrapper/import times. 622 tests pass, including these regressions.

This trades potentially conservative CSV freshness for avoiding false live
readiness (Kleppmann's event-time/data-provenance distinction). A newly exported
identical CSV may still look old without authoritative export metadata; use a
verified portal fetch to establish a new observation. Backoff prevents the stricter
freshness check from turning a blocked provider into repeated login traffic.

## Current pairing pipeline repair

The saved Sense Now pairing artifact was dated July 22 because neither refresh
mode ran its producer. Both refresh modes now regenerate the pairing after the
post-capture snapshot. Pairing uses Sense measurement time, requires fresh verified
Sense readings, and rejects Envoy log samples more than 90 seconds apart or more
than 180 seconds old. Nonfinite values cannot make comparisons. The report labels
log alignment separately from physical sensor/CT verification. Reconciliation
also refuses old pairing files, including the observed July artifact.

626 tests pass. The added freshness gates trade sparse availability for truthful
comparisons (Kleppmann's event-time distinction); missing evidence produces an
explicit partial/unavailable result rather than a plausible-looking old delta.
Scheduled regeneration adds bounded local work to the refresh cycle, justified
by keeping the shared report usable and mitigated by its existing runtime lock
and a 60-second job timeout (Continuous Delivery feedback).
