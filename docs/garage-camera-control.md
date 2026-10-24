# Garage camera control and quiet doorbell motion

October 23, 2026: Alarm.com Doorbell General settings had Panel Motion Notifications enabled, independently of the recording rule. Disabled that setting, retained Panel Button Press Notifications and the indoor chime, and left People and Deliveries and Button Press recordings enabled.

Garage occupancy is now sourced from the native Home camera motion sensor, mirrored through a persistent Homebridge Dummy switch named Garage Camera Motion (`smart_home_garage_camera_motion_v1`, webhook enabled, resetOnRestart false). Two enabled Home automations are deployed: Motion Detected in Garage turns the mirror on and Garage Light to 100%, with Turn Off Never; No Motion Detected in Garage turns the mirror off. The existing last-person-leaves off action remains.

The local controller reads this signal directly from the existing local webhook server. Active camera motion holds the light without a phone-association time cap. Clear motion starts a fresh five-minute countdown; later activity cancels that expiry. An unavailable bridge preserves the light and retries. The monitor checks live Alarm.com state rather than relying on a cached Home light value to discover new activations, and continues checking active camera holds. Polling can add up to one monitor interval to the clearance countdown. Manual dimming/off is preserved by the existing expiry logic.

All four Alarm.com door-open rules still turn the light on. Their fixed five-minute off actions were removed, because they could cut the light during continued activity. Shutdown now belongs to the camera-aware local controller.

Validation: 532 automated tests pass. Home's Test This Automation successfully drove the motion mirror true and the real light on. Invoking expiry with an old activity time preserved the on light with camera-occupied status. Home's clear automation drove the mirror false and started a 300-second countdown. Advancing the test countdown exercised the real off command; a separate Alarm.com read confirmed off. This accelerated test verifies the control path, not a natural person-detection event. Camera motion is activity evidence, not a stationary-person occupancy detector. No new video processing or third-party image service was introduced.

Screenshots are under reports/camera-light-control-2026-10-23 in the primary workspace. Deployment included the runtime helper/controller/reconcile and garage camera configuration, plus a Homebridge restart to register the new mirror.
