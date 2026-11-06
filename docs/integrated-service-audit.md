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
- Siri consent was repaired on the Mac. iPhone Mirroring still requires the
  user's existing authentication before its separate Siri surface can be tested.

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
