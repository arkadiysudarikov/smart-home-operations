# UniFi occupancy API-key transport

The existing `homebridge-local` password login is rejected, while the household
monitor's saved UniFi API key can read the legacy fingerprint, network-device,
and active-client endpoints. A reviewed transport adapter reuses that credential
without changing occupancy classification, bridge identity, or authentication
permissions. The key stays in macOS Keychain; only its service/account labels and
the pinned HTTPS controller URL are in the private runtime file
`config/unifi_api_key.json`. The installer preserves this local file.

The adapter permits only the three occupancy GET routes and rejects write methods.
The destination must match the pinned controller. It never falls back to password
login. Requests time out after twelve seconds; HTTP 401/403/429 impose a fifteen-
minute cooldown. Errors are replaced with fixed messages because the underlying
request library may attach authorization headers to error objects.

The event socket accepted an API-key handshake but did not remain connected.
The deployed transport uses the plugin's configured sixty-second polling interval
and creates no event socket or password-reconnect loop. This restores occupancy
updates with up to one polling interval of latency. Existing independent presence
monitoring keeps its own interval. A local `data/latest_unifi_plugin_health.json`
records only endpoint success, time, and counts, so continuing polls can be
verified without logging response bodies or credentials.

## Guarded patch and cache preservation

`scripts/patch_unifi_keychain_transport.py --plugin-path /path/to/homebridge-unifi-occupancy`
checks the exact reviewed 1.3.0 `dist/platform.js` source. Add `--apply` to install
it after placing `unifi_keychain_events.cjs` in the deployed runtime scripts and
configuring the local Keychain metadata. Unknown versions/hashes are refused;
repeated application is idempotent. The original source is backed up privately
under `~/.homebridge/codex-backups/`. Plugin updates require renewed verification.

The patch also preserves accessories already cached at startup. Invalid old room
mappings are marked faulty/inactive and skipped during updates, rather than
unregistered. Their occupancy value is not presented as a fresh reading. New
transient guest accessories retain their normal cleanup behavior. New accessory
creation stops at 145 entries to leave room under the HAP bridge limit. Lazy
handlers initialize their service before their first update. Existing UUIDs and
pairing identifiers remain intact; this is not a cache reset.

October 19 verification used the real installed module in an isolated mock
Homebridge API: nine network-device mappings, 49 active clients, 48 simulated new
accessories, all 55 original UUIDs retained, and zero removals. The live restart
then retained the 55 original entries and wrote 103 total cached accessories.
The 55 legacy mappings remain pending an explicit cleanup decision. No security
or garage-door action was executed by the repair.

The recovery probe uses the same configured Keychain source so it does not keep
trying the obsolete password. Missing credentials or a destination mismatch fail
closed. Both live fingerprint and active-client probes returned HTTP 200 after
the repair.

Checks: `python3 -m unittest discover -s tests -p 'test_unifi_keychain_transport.py'`
and `test_recover_unifi_occupancy.py`. These cover destination pinning, bounded
reads, secret redaction, authentication cooldown, polling, version/hash guards,
cache preservation, capacity protection, and no password fallback.

Rollback: restore the privately saved vendor `platform.js`, then restart only the
UniFi occupancy child bridge. The original password authentication issue would
return. Do not clear HomeKit pairing data as a transport troubleshooting step.
