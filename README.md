# Smart Home Operations

This repo is the local operations layer for the house. It collects read-only
signals from Homebridge, installed smart-home apps, local services, and logs,
then stores snapshots for pattern analysis.

## Current Shape

- Homebridge is the integration hub.
- Apple Home remains the user-facing control surface.
- UniFi provides network and occupancy context.
- Enphase, Sense, Alarm.com, SmartHQ, TaHoma, Mopar, Dyson, Calendar, and
  Delay Switch signals are treated as telemetry sources.

## Principles

- Preserve existing Homebridge bridge identities, ports, pairing data, rooms,
  and automations.
- Prefer read-only monitoring before action automation.
- Separate core runtime health from integration-specific warnings.
- Store local telemetry in this repo so patterns can be reviewed before any
  automation changes are made.

## Commands

Run one snapshot:

```sh
./scripts/smart_home_snapshot.py
```

Analyze collected history:

```sh
./scripts/analyze_patterns.py
./scripts/analyze_energy_pairing.py
./scripts/fetch_chargepoint_sessions.py
./scripts/analyze_chargepoint_pairing.py
./scripts/extract_sce_bills.py
./scripts/analyze_all_energy_readings.py
./scripts/capture_sense_trends.js
./scripts/analyze_bill_home_pairing.py
./scripts/analyze_energy_costs.py
./scripts/analyze_meter_reconciliation.py
./scripts/analyze_combined_energy_monitor.py
```

Run storage maintenance and alerts:

```sh
./scripts/maintain_storage.py
./scripts/generate_alerts.py
```

Capture a one-shot Sense Now packet and pair it with nearby Envoy readings:

```sh
./scripts/capture_sense_now.js
./scripts/pair_sense_now.py
```

Refresh daily Sense trend rows for the combined energy cross-check:

```sh
./scripts/capture_sense_trends.js
```

Reapply the local SmartHQ HomeKit duration and authentication compatibility fixes after a SmartHQ plugin update:

```sh
./scripts/patch_smarthq_remaining_duration.js
```

Detect and optionally update the Office TaHoma local IP when the gateway moves:

```sh
./scripts/update_office_tahoma_ip.js
./scripts/update_office_tahoma_ip.js --apply
```

Refresh Alarm.com through the Homebridge Alarm.com plugin credentials and MFA
cookie:

```sh
./scripts/capture_alarm_com.js
./scripts/capture_alarm_com.js --crawl
```

Run a passive Sideyard Gate validation window after manually opening/closing
the gate. This records the test start, refreshes Homebridge and Alarm.com state
until Sideyard Gate trip/media evidence appears or the timeout expires, and
does not send any physical gate command:

```sh
./scripts/gate_test_mode.py
./scripts/gate_test_mode.py --timeout 600 --interval 30
```

Refresh ChargePoint sessions before pairing them against home energy sources:

```sh
./scripts/fetch_chargepoint_sessions.py
./scripts/analyze_chargepoint_pairing.py
```

When ChargePoint blocks scripted driver-login refreshes, open
`https://driver.chargepoint.com/charging-activity` in a browser, copy the
`Download CSV` link target, and import it:

```sh
pbpaste | ./scripts/capture_chargepoint_browser_csv.js --import
```

Install HomeKit virtual alert sensors:

```sh
./scripts/install_homekit_virtual_sensors.py
```

HomeKit action switches call the local action service. `Refresh SCE` first
checks the API path, then re-scans local SCE bill PDFs and Green Button
interval exports and regenerates the energy reports and HomeKit alert states.
The API path supports either UtilityAPI JSON API credentials
(`utilityapi_api_token` plus `utilityapi_meter_uids` or
`utilityapi_authorization_uids`) or a direct SCE Green Button Connect
`green_button_connect.resource_url`/`green_button_connect.access_token`.
UtilityAPI imports default to an automatic moving end date so scheduled
refreshes keep asking for the newest interval rows.
`Refresh SCE` does not trigger UtilityAPI historical collection jobs by default;
those can require UtilityAPI balance or collection entitlement. Set
`utilityapi_auto_historical_collection` to `true` only when you explicitly want
stale interval data to trigger a UtilityAPI collection attempt. Each run writes
downloaded file, row count, requested end, returned coverage, and any collection
attempt to `data/latest_sce_api.json`. Until one of those credential sets is
configured, the status is written there as a registration-required fallback with
an SCE third-party vendor registration plan. See
`config/sce_green_button_third_party.md` for the no-paid-UtilityAPI SCE Green
Button Connect setup notes. `Reconcile
Energy` runs a full local energy refresh: current snapshot, storage cleanup,
pattern analysis, SCE/Envoy/Sense/ChargePoint/Alarm.com reconciliation,
combined energy report, alerts, and HomeKit virtual sensor updates. `Gate Test` runs the passive
Sideyard Gate validation helper and writes its status/report without creating a
new bridge. `Alarm Refresh` recaptures Alarm.com portal state, restarts only the
Alarm.com child bridge, then resamples Homebridge characteristics and refreshes
the Alarm Cache tile/report. `Garage Activity` is the local action switch used
by garage-only Home automations to invoke the Garage Light last-activity hold.
Each activation and hold expiry is logged to `data/garage_activity_events.jsonl`
and summarized under `actions.garageActivity.activityReport` in `/status`.
HomeKit action-switch calls do not expose the upstream automation name, so the
report lists the intended trigger set and records exact `trigger`/`source`
values only when callers include them as query parameters. See
`config/homekit_garage_activity.md` for the intended bridge and automation wiring.

Install the periodic local monitor:

```sh
./scripts/install_monitor.sh
```

The monitor writes:

- `data/latest.json`
- `data/latest_events.json`
- `data/latest_characteristics.json`
- `data/latest_alarm_com.json`
- `data/alarm_com_automation_rules.json`
- `data/latest_alarm_homebridge_state.json`
- `data/latest_chargepoint_refresh.json`
- `data/alarm_com_devices.json`
- `data/alarm_com_activity.json`
- `data/alarm_com_gate_validation.json`
- `data/latest_alarm_gate_test.json`
- `data/snapshots/*.json`
- `data/smart_home.sqlite`
- `reports/latest.md`
- `reports/patterns.md`
- `reports/energy_pairing.md`
- `reports/chargepoint_pairing.md`
- `reports/sce_bill_readings.md`
- `reports/all_energy_pairing.md`
- `reports/bill_home_pairing.md`
- `reports/energy_costs.md`
- `reports/meter_reconciliation.md`
- `reports/combined_energy_monitor.md`
- `reports/sense_now_pairing.md`
- `reports/alarm_com.md`
- `reports/alarm_gate_test.md`
- `reports/alarm_homebridge_state.md`
- `reports/alerts.md`
- `reports/homekit_virtual_sensors.md`

`reports/latest.md` includes Homebridge event activity, deduplicated event
counts, and sensor/accessory characteristic changes since the previous
snapshot. The SQLite database stores those rows in `home_events`.

`reports/sce_bill_readings.md` extracts billing-period SCE import/export totals
from local SCE bill PDFs named `ViewBill*.pdf` in `~/Downloads` and
`~/Documents`, then writes `data/sce_bill_readings.csv`.

`reports/all_energy_pairing.md` compares local SCE Green Button interval
exports, SCE bill-level readings, Sense readings, and Enphase/Envoy readings.
The preferred no-cost SCE refresh path is a manual SCE Green Button CSV/XML
export placed in `~/Downloads`, `~/Documents`, iCloud Drive, or
`data/sce-downloads/`; the `Refresh SCE` HomeKit switch opts into scanning
those local locations before rebuilding the energy reports. Fresh SCE interval
files can also be pulled after UtilityAPI or direct SCE Green Button Connect
credentials are configured in `config/sce_green_button_connect.json` or
equivalent environment variables:
`UTILITYAPI_API_TOKEN`, `UTILITYAPI_METER_UIDS`,
`UTILITYAPI_AUTHORIZATION_UIDS`, `UTILITYAPI_INTERVAL_START`,
`UTILITYAPI_INTERVAL_END` (optional, defaults to `auto`),
`UTILITYAPI_AUTO_HISTORICAL_COLLECTION` (optional, defaults to off; do not
enable unless paid UtilityAPI collection is explicitly approved),
`UTILITYAPI_AUTO_COLLECTION_STALE_HOURS` (optional, defaults to `36`),
`UTILITYAPI_HISTORICAL_COLLECTION_TIMEOUT_SECONDS` (optional, defaults to
`600`), `UTILITYAPI_HISTORICAL_COLLECTION_POLL_SECONDS` (optional, defaults to
`30`), `SCE_GBC_RESOURCE_URL`, and `SCE_GBC_ACCESS_TOKEN`. Direct SCE Green
Button Connect vendor onboarding needs a third-party SCE.com user, organization
TIN, terms acceptance, and SCE connectivity testing before SCE issues usable
OAuth/resource values.

`reports/bill_home_pairing.md` compares SCE bill-period import/export totals
with the currently available home-side readings from Envoy, Sense, and
Alarm.com. It reports strict overlap separately from rough scale checks so
closed bills are not confused with live monitor windows.

`reports/energy_costs.md` turns SCE bills into cost rates, including latest
import cost, export-credit value, net-bill equivalent cost, ChargePoint actual
rates, separate solar and battery self-consumption values, and rough cost
equivalents for Envoy, Alarm.com, and SCE interval coverage.

`reports/alarm_com.md` is the Alarm.com portal layer. It logs in through the
same Homebridge Alarm.com plugin credentials and MFA cookie, refreshes
`config/alarm_energy_readings.json`, captures read-only device state from the
Alarm.com JSON API, captures sanitized activity history, checks websocket-token
health without storing the token, and can run a safe GET-only portal crawl with
`./scripts/capture_alarm_com.js --crawl`. The scheduled monitor runs the energy,
device-state, activity, and websocket-health refresh path only. It also writes
normalized device and activity files, including device-state changes and newly
seen activity since the previous capture. When the primary
`/web/api/activity/historyEvents` endpoint is degraded, the capture can reuse
the page-backed media/activity audit as a stale-but-usable activity source.
`reports/alarm_homebridge_state.md` compares the fresh Alarm.com portal device
state against cached Homebridge Alarm.com characteristics; Alarm.com portal
state is treated as the current source of truth when the cache disagrees.
`reports/homekit_virtual_sensors.md` shows Alarm.com portal-capture and
Homebridge-cache comparison age so the Home-facing tile state can be judged
against source freshness. Activity-history capture degradation is surfaced
separately from physical Alarm state through the `Alarm Activity` virtual
sensor. The Alarm.com report also validates the recorded Flex IO / gate-control
hardware against Sideyard Gate state, the Sideyard Gate Video rule, and recent
activity/media evidence when activity history is available.

`scripts/fetch_chargepoint_sessions.py` refreshes
`data/chargepoint_sessions.json` before the ChargePoint pairing report runs.
It supports the driver portal charging-activity path with a password stored in
macOS Keychain and referenced from `config/chargepoint.json` by
`password_keychain_service` and `password_keychain_account`. The portal page's
CSV button is generated in the browser from loaded table data; the script uses
the underlying `charging_activity_monthly` POST to ChargePoint's map-cache API
instead. Because ChargePoint may trigger DataDome/CAPTCHA on repeated password
logins, driver-portal mode has a freshness gate and retry backoff. When auth is
blocked, it can fall back to the newest browser-exported CSV in
`data/chargepoint-downloads` or a configured `csv_path`, then keeps the last good
local `chargepoint_sessions.json` file if no CSV is available.

The same script can also use ChargePoint's station-owner Web Services API
(`getChargingSessionData`) with credentials from `config/chargepoint.json` or
`CHARGEPOINT_WS_USERNAME`, `CHARGEPOINT_WS_PASSWORD`,
`CHARGEPOINT_STATION_ID`, and `CHARGEPOINT_LOOKBACK_DAYS`, or a generic JSON
endpoint via `mode=json`, or a browser CSV export via `mode=browser_csv`. It writes status to
`data/latest_chargepoint_refresh.json`. If credentials are missing, stale, or
the API returns no sessions, the script keeps the last good local
`chargepoint_sessions.json` file so the rest of the monitor can continue.
`scripts/capture_chargepoint_browser_csv.js` turns a ChargePoint browser
`Download CSV` data URL, clipboard value, or CSV file into a dated file under
`data/chargepoint-downloads`, then optionally imports it immediately with
`--import`.

`reports/meter_reconciliation.md` adds Alarm.com energy readings to the
Envoy/Sense/SCE view. Alarm.com readings live in
`config/alarm_energy_readings.json`.

`reports/combined_energy_monitor.md` rolls Envoy, Sense, SCE, ChargePoint, and
Alarm.com into one operational energy view. Its alerts and state titles feed the
HomeKit virtual energy sensors. Daily Sense trend data is cached in
`data/sense_trends_latest.json`.

Retention is configured in `config/sources.json`. By default, raw snapshot files
are kept for 2 days, database snapshot rows are kept for 14 days, Home event
rows are kept for 7 days, and older heavy snapshot payloads are compacted.

HomeKit virtual alert sensors are backed by the existing `Homebridge Dummy`
platform. The monitor updates those accessories through the dummy plugin's
local webhook after each alert pass.

When installed as a LaunchAgent, the runtime copy lives at
`~/Library/Application Support/SmartHomeMonitor` because macOS privacy controls
can block background agents from opening files under `Documents`.
