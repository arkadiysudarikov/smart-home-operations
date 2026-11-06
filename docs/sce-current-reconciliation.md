# Current SCE reconciliation — November 5, 2026

The authenticated SCE export is downloaded and imported on M4. It contains
35,428 consecutive quarter-hour intervals from November 1, 2025 at midnight
Pacific through November 5, 2026 at midnight Pacific (all of November 4).
All intervals have delivered and received readings; XML quality is 0.
The original exports remain private, outside Git.

## Import repairs and validation

SCE's XML includes many IntervalBlocks in one Atom content. The old importer
read only its first block, silently importing just one day per direction.
It now reads every block. A regression test includes repeated local clock times
with distinct UTC offsets and both delivered and received energy.

The CSV has duplicated local clock labels at unexpected times on autumn DST
transition days. Its aggregate transition-day totals agree with XML, but its
labels cannot safely identify individual intervals. The XML epochs are used
instead; no timestamps were invented. The XML has no gaps or duplicate epoch
intervals. DST days contain 100 or 92 intervals as appropriate.

Interval integration now requires bracketing monitor readings and rejects gaps
longer than 15 minutes. Daily completeness now uses the actual Pacific day length,
so 96 intervals cannot make a 25-hour day complete. Regression tests cover both.

## Independent grid boundary comparison

After filtering missing boundaries and monitor outages, 554 SCE intervals overlap
Envoy between October 29 and November 4. These are matched-window totals, not
complete calendar-day measurements.

| Measure | Result |
| --- | ---: |
| SCE net grid import | 208.710 kWh |
| Envoy estimated net grid import | 208.962 kWh |
| Net total difference | +0.252 kWh (+0.121%) |
| Median absolute quarter-hour difference | 0.00917 kWh |
| Mean absolute quarter-hour difference | 0.01394 kWh |
| 95th-percentile absolute quarter-hour difference | 0.03928 kWh |

Small signed total differences can cancel; the absolute interval statistics are
included for that reason. Envoy values are time-integrated estimates from monitor
samples, not utility billing registers. This supports Envoy's grid measurement
without proving every meter or physical CT installation correct.

On November 4 specifically, 94 valid matched intervals total 49.560 kWh SCE and
49.890 kWh Envoy. SCE's full day is 53.550 kWh net import; do not compare that
full-day value with the filtered Envoy subtotal. Earlier unfiltered estimates
looked closer but included intervals that now fail the coverage checks.

## Remaining solar and load questions

The earlier simultaneous realtime observations support Sense solar seeing PV
plus signed battery power rather than gross PV. They remain evidence of a
measurement-scope difference, not physical wiring verification. See the
[solar investigation](energy-source-investigation.md).

Fresh Sense cloud daily history was captured for the last 14 days. November 4
Sense solar is 11.554 kWh and Sense load is 50.199 kWh. Envoy's filtered estimates
are incomplete daily windows and must not be presented as complete-day parity.
Sense house-load coverage remains unresolved; current grid agreement does not
establish that Sense measures every household circuit.

Do not reverse CTs, apply arbitrary calibration, or suppress solar alerts based
only on these observations. Identify the Sense CT circuit relative to PV,
battery, and subpanel feeds before changing physical configuration.

## Deployment and proof

Both changed analysis modules are deployed on M4, with pre-change byte checks and
backups under runtime `data/backups/sce-import-20261105-124803/`. Import and analysis
ran under the refresh lock. The reports and normalized intervals were reread
from saved runtime outputs. All 596 repository tests passed.

Private proof: `data/latest_all_energy_pairs.json`,
`data/latest_combined_energy_monitor.json`, `data/sce_usage_intervals.csv`, and
`data/sce-downloads/SCE_Usage_GBC_2026-11-05.xml` in SmartHomeMonitor runtime.
The manual SCE source is current; UtilityAPI monitoring remains off/stale and
has not been misrepresented as repaired. Current account plan was verified;
current tariff prices and export-credit schedule still need bill-backed proof.
