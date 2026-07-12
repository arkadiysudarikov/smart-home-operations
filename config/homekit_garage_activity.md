# HomeKit Garage Activity Setup

This file records the intended Home/Homebridge wiring for the Garage Light
last-activity hold without storing raw HomeKit or Homebridge runtime files.

## Bridges

- `Alarm.com` is the Homebridge Alarm.com child bridge. It owns the physical
  garage devices, including `Garage Light`, `Garage Door Contact`, `Garage Door
  Lock`, and the garage door opener accessories.
- `Smart Home Actions` is the local action-switch child bridge. In the Home app
  it may appear as `Default Room Http Switch` because HomeKit retained an older
  bridge/accessory display name. It must stay paired.
- Do not remove the Home app bridge named `Default Room Http Switch` unless the
  Smart Home action switches have first been migrated and re-paired.

## Required Action Switch

The `SmartHomeActions` Homebridge platform must expose this switch:

```json
{
  "id": "garage-activity",
  "name": "Garage Activity",
  "path": "/action/garage-activity",
  "timeoutMs": 120000
}
```

The plugin also includes `Garage Activity` in its default action list, so it
will be added even if an older Homebridge config omits it from the explicit
`actions` array.

## Home App Automations

These garage-only automations should trigger `Garage Activity`; they should not
directly set `Garage Light`:

- `When Motion Detected in Garage`
- `Garage Door Contact Opens`
- `Garage Door Lock Unlocks`
- `Garage Door Opener 2207 Opens`
- `Garage Door Opener 2210 Opens`

This arrival automation also currently targets `Garage Activity`, along with
`🛡️ Off`:

- `When The First Person Arrives Home`

The controller endpoint then turns `Garage Light` on to 100%, holds it until at
least five minutes after the latest activity, and restores the pre-hold state
only if the light is still in the controller-owned 100% state.

Each endpoint activation and hold expiry is tracked in
`data/garage_activity_events.jsonl` and summarized in `/status` under
`actions.garageActivity.activityReport`. HomeKit does not pass the upstream
automation name to a shared action switch, so source-specific attribution is
limited to the intended trigger list above unless a caller includes
`?trigger=...&source=...` on the action URL.

## Files Not Stored In Git

Do not commit these raw local files:

- `~/Library/HomeKit/core.sqlite`
- `~/.homebridge/config.json`
- runtime state under `~/Library/Application Support/SmartHomeMonitor/`

Use timestamped local backups for those files and keep this sanitized note as
the source-control checkpoint for the intended setup.
