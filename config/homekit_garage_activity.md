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

Current camera automations (saved October 23):

- `Garage camera activity — keep light on`: camera motion sets `Garage Camera Motion`
  on and `Garage Light` to 100%, with no fixed off timer.
- `When No Motion Detected in Garage`: clears `Garage Camera Motion`.

The four Alarm.com garage-door rules turn the light on without a fixed off timer.
The monitor calls `/action/garage-reconcile` to adopt a direct full-brightness
activation into the controller. While the camera signal is active, the controller
holds the light on; clearing the signal starts a full five-minute countdown.
Camera-read failure preserves the light and retries. Phone Wi-Fi is not used as
garage occupancy when the camera bridge is enabled.

Earlier wiring used `Garage Activity` for these triggers:

- `When Motion Detected in Garage`
- `Garage Door Contact Opens`
- `Garage Door Lock Unlocks`
- `Garage Door Opener 2207 Opens`
- `Garage Door Opener 2210 Opens`

Once activity has cleared, the controller turns the light off if it is still in
the controller-owned 100% state. A manual off or brightness change is preserved.
Camera motion is not proof of stationary-person detection; that remains a separate
limitation and must not be described as verified occupancy sensing.

November 3: a source deployment had overwritten the runtime-only camera handler
and reconcile route. Both camera modules, the controller integration, and the
refresh hooks are now tracked, with route and camera-priority regression tests.

The explicit off policy avoids restoring an already-on starting state when an
Alarm.com direct garage-door rule reaches the light just before the HomeKit
`Garage Activity` automation.

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
