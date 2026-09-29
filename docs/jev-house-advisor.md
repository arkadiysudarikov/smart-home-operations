# Jev household decision support

Eleven bounded Choice questions share a request: measured energy contributor,
attention priority, laundry telemetry conflict, routine-summary advice,
supporting detail, energy-saving opportunity, changes, deferral, solar timing,
unusual combinations, and evening recap. Scheduled energy reviews
include these questions alongside the existing low-energy advisory. On-demand
reviews have a five-minute persistent attempt cooldown. All calls share the
existing Keychain credential and $1 monthly local allocation; each attempt
reserves $0.01 before transmission. Requests are limited to 8 KiB, responses to
64 KiB, with a 20-second network timeout and no automatic retries.

## User entry points

| Phrase | Restricted command | Behavior |
| --- | --- | --- |
| Why is energy high | energy | Select measured contributors, or say unknown |
| Anything need attention | attention | Rank verified household issues |
| Any complications | complications | Same ranked household review |
| How can I save energy | savings | Recommend a supported check; no controls |
| Check the laundry | diagnostics | Flag positive-countdown/idle conflicts |
| Review routine announcements | routine | Recommend announce/combine/quiet; advisory only |
| Tell me more | more | Expand the latest supported announcement using current evidence |
| What changed | changes | Rank comparable energy/opening changes since the last accepted check |
| What can wait | wait | Only defer energy optimization; never safety or appliance faults |
| Best time to run laundry | laundrytime | Require ten minutes of fresh measured solar surplus; no invented TOU price |
| Anything unusual | unusual | Rank verified conflicting laundry, cooling/opening, or away/high-use combinations |
| Evening recap | recap | Short current-issues report |

The automatic recap is enabled for 19:00–20:00 America/Los_Angeles, at most
one attempt per local day, only with fresh positive home presence, an accepted
Jev issues choice, daytime permission, and no announcement pause. No catch-up
after the window and no suppression of existing safety/laundry announcements.
It uses the existing scheduled energy process, with one bounded evening review.
No appointment contents, names, IDs, or raw messages are sent to Jev.

Energy reports distinguish `lowEnergyStatus`, `missingContext`, and `reviewStatus`.
Unknown EV context blocks low-energy detection, not a scheduled advisory request;
the normal twelve-hour review cooldown remains. Missing context is never filled
with assumed false values. Bill-average pricing is not a time-of-use schedule,
so future cheapest-hour recommendations remain unavailable until verified rates exist.

These are backend commands; saved Shortcuts and their host must be verified
separately. Phone sync and Siri speech recognition are not implied by a backend
test. The existing indoor Intercom relay is unchanged.

## Boundaries

- Only fixed categories, numeric readings, counts, and booleans/unknown values go
  to TypeSafe. Device labels, IDs, names, locations, email, calendar contents,
  and raw announcement text are excluded.
- Jev returns choices, never spoken prose. Local templates supply every word.
- Each accepted choice requires the pinned model, complete probability schema,
  confidence and selected probability at least 0.9, plus matching local evidence.
- Cached advice expires after ten minutes and is invalidated when facts change.
- Temperature and laundry conflicts cannot be hidden by a ranking decision.
  No safety, laundry, doorbell, or existing routine delivery is suppressed by
  model advice. Routine-summary advice is intentionally not an automatic mute.
- Fresh, explicitly identified Sense loads can support contributions, not proof
  of the complete cause. Inconsistent totals reject attribution. Solar/unknown
  buckets and unsupported names cannot become appliance causes.
- Positive time remaining conflicting with idle is a diagnostic, never completion.
  The deterministic countdown completion monitor remains authoritative.
- An away owner does not establish an empty house. Away-high recommendations
  explicitly say other people may be home. Nothing is automatically switched off.
- Low-energy baselines still require seven matching days and explicit EV context.
  Missing Sense EV readings cannot be repaired by assuming charging is off.
- Calendar follow-up delegates to the existing exact-occurrence/presence-checked
  replay. No appointment details are sent to Jev.

## Operations and checks

`jev_house_advisor.py --review` is runtime-only and performs a bounded review
without audio. Without `--review`, `--mode attention|energy|savings|diagnostics|routine|detail`
renders a read-only result with no network call. State is in
`data/jev_house_advice.json` and `data/jev_house_attempt.json`.
Each scheduled pass also writes `data/latest_jev_house_diagnostics.json`, so
fresh laundry conflicts remain visible during the API cooldown. This record
cannot alter the completion detector or send an announcement.

Focused verification: `python3 -m unittest discover -s tests -p 'test_jev*py'`.
Tests cover fresh source adapters, model schemas, evidence gates, bounded requests,
privacy, cooldown persistence, no-mail follow-ups, and no implicit controls.

September 28 deployment check: a real six-question Jev call completed using
1,077 input and 263 output tokens. The locally accepted priority was `laundry`,
matching positive time remaining with an idle flag. An immediate second review
was cached and made zero API calls. No audio was played by that review.

Mac shortcut setup exposed two migration issues: `m2-office-mini.local` was no
longer reachable, and the current Mac Studio Shortcuts public key did not match
the existing restricted Mac/iPhone keys. The user authorized the current Mac
key with the same forced-command/no-forwarding restrictions; existing keys
were retained. `Anything need attention` then successfully invoked the deployed
service and the iPhone Intercom relay accepted its reply at 12:38:32 Pacific.
After administrator approval, the local hostname was changed to `home-hub.local`.
Name resolution and SSH connectivity were verified. Twenty-one SSH-backed
household shortcuts were updated to that stable hostname, including all four new
phrases and the existing energy/follow-up commands. The local display-awake target
uses the stable name too; its existing ID was preserved so state mappings do not
reset.

September 29 correction: macOS detected a Bonjour conflict and renamed the host
to `home-hub-2.local`. The old name advertised an unreachable IPv6 address.
Reserved this Mac's existing Ethernet address, `192.168.0.190`, in UniFi and
verified its SSH host key matches the local Mac. Updated all 21 SSH-backed house
shortcuts and the local display target to that reserved address. This avoids
Bonjour-name churn; do not transfer the reservation to other hardware without
verifying the SSH identity and updating the reservation. The competing advertiser
is not identified, and iPhone sync remains a separate verification step.

The audit also found four older Text-only placeholders (House, good morning;
Quiet until morning; When should I leave; Anything unusual). They have no SSH
host to migrate and were not represented as working commands by this change.
Phone sync, acoustic delivery, and Siri voice recognition remain separate checks,
not established by relay acceptance.
