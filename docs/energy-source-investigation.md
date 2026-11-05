# Energy source investigation — November 5, 2026

PR #102 was merged into `codex/m4-announcement-verification` at 20:10:51 UTC,
merge commit `535a92e6e14c34f7c4f0bd5f43b1769e355a48ea`. It was not a merge into main.

## Solar measurement scope

Collected 70 Sense realtime packets alongside 12 direct Envoy meter readings.
Matched 12 distinct Envoy observations to Sense sensor epochs with at most five
seconds skew. Private numerical proof is in M4 runtime data:
`solar_sense_aligned.json`, `solar_envoy_aligned.json`, and
`solar_pair_diagnostic.json`. No credentials are in those proof records.

| Comparator for Sense solar | Median absolute difference |
| --- | ---: |
| Envoy gross PV production | 855 W |
| Envoy PV + signed storage power | 28 W |

Nine of twelve combined-feed comparisons were within 150 W. The others occurred
while storage power was changing. Those transients are not treated as evidence
of a wiring fault. Independent Envoy inverter and production-meter readings
were both approximately 2 kW. Enabled production, grid and storage meter identities
were read directly; no meter identity was inferred from list order.

This supports the hypothesis that Sense's solar CTs see a combined solar/battery
feed. During charging, battery power is negative and can nearly cancel PV; during
discharge, it can appear as generation after sunset. This is not physical wiring
verification. Do not reverse CTs, apply calibration multipliers, or relabel the
installation as confirmed from software observations alone.

The Sense account has one monitor, with solar connected/configured and the aux
port assigned to solar. Root `solar_w` agreed with the displayed solar device
reading, so choosing another JSON solar field does not solve the discrepancy.
Per-phase aux watts and sensor epochs are now preserved to make future comparisons
possible without another diagnostic collector.

## Storage sign bug

Some older reports used `total - abs(storage)`. That removes charging load but
also subtracts discharging energy instead of adding its contribution to the house.
The consistent convention used by the Envoy plugin and meter data is:

```
storage < 0: charging
storage > 0: discharging
site load = Envoy Consumption Total + signed Storage
net generation = Envoy Production + signed Storage
```

The reports now use signed storage in both charging and discharging cases. Missing
values remain unknown and zero production remains zero; an inverter fallback
cannot replace a valid zero measurement. Solar-warning freshness uses actual
meter timestamps rather than a fresh download of old readings.

Sense house load still differed from storage-adjusted Envoy load in this sample
window (median absolute difference 730 W). Whole-house CT coverage remains an
additional question; the solar comparison does not resolve it. Current SCE import
and export intervals are needed to cross-check the grid boundary independently.

## SCE progress

Authenticated in the built-in browser using the user's verification code. The
live dashboard confirms TOU-D-PRIME, Solar Billing Plan and Clean Power Alliance,
with current usage through November 3. Prepared the Green Button CSV export for
November 1, 2025 through November 5, 2026. Download/import awaits the site's
CAPTCHA; no CAPTCHA was bypassed and no paid UtilityAPI collection was enabled.

UtilityAPI's meter metadata separately shows monitoring off and a June 16 last
update. This is why repeated API downloads cannot produce current intervals.
Do not mark that API healthy merely because a newer manual export is imported.
