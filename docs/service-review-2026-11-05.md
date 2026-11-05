# Household service review — November 5, 2026

Read-only audit of the M4 coordinator and regression fixes. Bridge identity,
pairing, security rules, physical controls, and household credentials are preserved.

## Fixes

- Validate the entire JEV energy Choice response, including probability keys,
  distribution, confidence, pinned model, and locally supported classification.
  Missing EV/HVAC/presence/history cannot become a typical-use claim.
- Measure solar opportunities over distinct advancing meter timestamps. Polls,
  old schema state, backward time, stale samples, and observation gaps cannot
  manufacture ten minutes of surplus.
- Attribute Mopar login success only to a Mopar log line.

## JEV improvements

- Service faults and security-maintenance counts are derived from fresh sources.
  Only fixed categories, counts, and boolean/unknown values enter the request.
- JEV can select a service to investigate and a locally written next check.
  Attention summaries keep all verified faults regardless of model ranking.
- Savings responses explain when stale utility data or conflicting meters limit
  advice. Bill-average prices do not establish a cheapest future hour.
- Service changes are comparable only when both old and new values are known.
- Automatic evening recaps do not repeat unchanged accepted issue sets on later
  evenings. This affects only the additional recap; existing alert paths remain.
- Interactive and scheduled reviews share a lock and a five-minute attempt gate.
  The 8 KiB request limit, one-cent reservation, and $1 monthly allocation remain.
- `service_health.py --write` publishes runtime JSON and a Markdown report.
  The loopback action server exposes fresh read-only `/status/services` JSON.
- Refresh `status=complete` describes execution; `operationalStatus=degraded`
  identifies optional failures. `checks_passed` is not complete household coverage.

## Service inventory and current evidence

| Service | Audit result / remaining check |
| --- | --- |
| M4 runtime / Homebridge | Running; action health passes; no advertisement mismatch. |
| Apple Home / HomeKit | Bridge reachability checked; room assignments, pairing and each control require functional checks. |
| Siri / Shortcuts | Existing direct reply path preserved; no fresh voice invocation in this audit. |
| HomePods / Intercom | Dated acoustic proof remains separate from current relay status and every-room playback. |
| UniFi | Occupancy bridge and presence controller configured/running; 145 cached occupancy accessories. Full-family presence is not established. |
| AT&T | Both local gateway addresses answered ICMP; this is not proof of WAN reliability. |
| Enphase / Envoy | Direct meter capture live; production, total and net consumption available. |
| Sense | Live capture; EV category absent. Sense solar and Envoy production disagree; verify CT coverage/calibration physically. |
| SCE / Green Button | API coverage ends June 16; imported interval coverage ends September 16. Both stale. Current tariff/solar settlement terms need verification. |
| ChargePoint | Recent successful refresh status; charging-session history is not live charging proof. |
| Mopar / Jeep | Plugin explicitly disabled. No automatic re-enable or invented EV-off state. |
| SmartHQ | Three appliances configured; active washer heartbeat went stale; watchdog restarted the reviewed child. Observe recovery and next real completion. |
| TaHoma | Seven bridges configured, with cached blind characteristics. Each blind's motion and gateway health require direct functional checks. |
| Dyson | Purifier, fan, temperature/humidity and filter cache present. Values lack per-reading timestamps; do not use cache as fresh maintenance proof. |
| Sony Bravia | All three read-only TV APIs pass. Family and Bar expose active PIN-entry pages and still need TV registration. Office has a persisted registered external accessory. External TVs need not appear in the ordinary bridge cache; Apple Home pairing remains a separate check. |
| AirPlay | Network reachability does not prove playback, Intercom reception, or media resumption. |
| Matter / Thread | Pairing, commissioned fabrics and real accessory operation remain a separate functional check. |
| Calendar | Fresh scheduled log at 11:50 Pacific: two events, zero due, no announcement attempts. SSH dry-run cannot read Calendar; this does not establish a failure of the authorized scheduled GUI context. |
| Mac displays / presence | Controller enforcing policy; each Mac's assertions, lid/power gates and presence mapping remain separate checks. |
| Bubbler / Delay Switch | Virtual accessory configured; physical pump output not tested. |
| Home Status dashboard | Cached characteristics exist; an active platform/fresh telemetry is not established. |
| HomeKit virtual sensors | Configured; source freshness and physical outcomes remain separate. |
| Household actions | Running; new read-only service endpoint tested separately from control actions. |
| JEV / TypeSafe | Existing model and spending bounds retained; provider verification recorded separately from fixtures. |
| Alarm.com | Websocket live. Gate reports malfunction; Sideyard and Backyard cameras plus gate sensor have not-responding trouble. Portal malfunction versus Homebridge open is not a trustworthy open/closed reading. |

## Operational repair

Restricted `~/.homebridge/codex-backups` directory from 0755 to 0700 after checking
that it is an owned directory, not a symlink. No backup contents were removed.

## Remaining external evidence

- Confirm whether the nonresponding cameras/gate sensor are still expected devices.
- Obtain current utility intervals and the actual SCE tariff/solar billing terms.
- Validate solar CT measurement coverage; do not fix disagreement by changing units or offsets without evidence.
- Test TV pairing, blind motion, household voice entry points and every-room audio
  when the household is ready. Do not equate network responses with those outcomes.
