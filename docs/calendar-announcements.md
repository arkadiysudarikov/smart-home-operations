# Calendar departure announcements

The local EventKit helper reads only uniquely named Arkadiy, Maxim and Jeanne calendars.
macOS grants full Calendar access, but the helper contains no event-write operations.
Shared calendars, all-day events, cancelled events and unmapped in-person destinations
are excluded. Recognized video appointments instead get a five-minute reminder;
generic website links are not treated as video meetings. Private events and phone
mappings are not committed.

The private runtime data/calendar_announcement_config.json maps each calendar to
its owner's iPhone MAC and specifies the home address and arrival buffer (15 minutes).
UniFi must report the matching phone active with a last-seen age of at most 90 seconds.
Missing or stale data means silence. An iPad never substitutes for a phone.
Jeanne remains silent until her iPhone has a verified entry in the private mapping.

Each minute, between 08:00 and 21:00 Pacific, the job checks appointments within
three hours and asks Apple Maps for a current driving ETA from home. It announces
only within two minutes after the calculated departure time. Missed reminders
are not replayed later. A persisted occurrence hash prevents duplicate announcements,
including after uncertain delivery; moving an event to a new start creates a new occurrence.
The phone is checked again immediately before invoking the existing indoor Intercom relay.

Run scripts/calendar_announcements.py for a silent check; --deliver additionally
requires the deployed runtime and enabled=true in the private config. Output contains
presence, event counts and delivery status, not event titles or locations.

Build scripts/calendar_reader.swift into Smart Home Calendar Reader.app in the runtime,
using config/calendar-reader-Info.plist as Contents/Info.plist, and codesign the bundle.
Run the executable with --request-access interactively once before scheduling.
Install launchagents/com.arkadiy.smart-home-calendar.plist in the user's LaunchAgents.
If Calendar access is denied under launchd, leave delivery disabled until resolved.

Limitations: Wi-Fi presence tracks the carried phone, not the person directly, and
private Wi-Fi address changes require reenrollment. Calendar titles and appointment
details are spoken to everyone within earshot indoors. End-to-end acceptance still
requires a real due reminder heard on HomePods with music resuming.
