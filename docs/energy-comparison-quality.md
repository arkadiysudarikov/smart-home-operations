# Energy comparison quality

The previous ChargePoint report divided all visible sessions since a fixed June
2026 date by an Alarm.com seven-day total. The operational recommendation also
contained a fixed 30% EV share. Neither calculation established a current,
matching observation window or complete current session history.

The history total and its date range remain available. The seven-day EV share is
now null until interval boundaries and source completeness are verified; this is
not a claim of zero EV use. The combined report rejects legacy cached shares
without matching-window verification and matching Alarm.com capture identity.
Charging recommendations require current session coverage, tariff, and departure
requirements rather than an unsupported attribution percentage.

Fast refreshes now rebuild ChargePoint pairing, meter reconciliation, bill/home
pairing, and energy cost estimates before the combined report. This prevents a
fresh wrapper report from silently depending on weeks-old derived comparisons.
It does not make old utility readings, bills, or CSV session history current.

The deployed read-only regeneration removed the unsupported high-EV alert and
refreshed the monitor coverage. Utility interval coverage still ends in June
2026 and does not overlap the current monitor window. No charging, battery,
thermostat, or appliance-control setting was changed by this repair.

Validation: 527 unit tests, including historical versus rolling windows,
unknown source completeness, cached legacy shares, and removal of fixed EV
percentage claims. A live solar comparison warns when contemporaneous Envoy
and Sense readings differ materially. Stale, missing, future, skewed, night, and
non-finite samples are excluded. This warning requests meter-coverage/calibration
verification and does not establish a hardware fault. Runtime reports were
regenerated from the existing sources.
