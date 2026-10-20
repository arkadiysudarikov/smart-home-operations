# Apple Thread credential-sharing proof of concept

This iPhone app asks Apple's ThreadNetwork framework to show a temporary
nine-digit Thread Administration One-Time Passcode. Apple displays the code in
its own system alert; the app never obtains the code or stores Thread secrets.

Requirements: full Xcode with the iOS 27 SDK, an iPhone running iOS 27+, an
eligible nearby Apple TV running tvOS 27+, and the **Manage Thread Network
Credentials (development)** capability on the app target. Apple's current API
specifically names Apple TV, not HomePod, as the eligible border router.
The API selects an eligible Apple TV itself; this prototype cannot force a
named TV such as the Office Apple TV.

Open `ThreadCredentialShare.xcodeproj` in Xcode. Select the iPhone as the run
destination, choose your Apple development team under Signing & Capabilities,
and confirm the **Manage Thread Network Credentials (development)** capability
is present. Run on the iPhone, tap **Find
Apple Thread network**, confirm the displayed name is the intended Apple
network, then tap **Show one-time code**. Enter the code shown by iOS into
SmartThings' **Join another Thread network** prompt before it expires.

This is intentionally not an automated credential extractor. The system asks
for user consent, and the code is never returned to the app. It has not yet
been built or tested on an iPhone here.

Xcode 27 and the iOS 27 SDK are installed on this Mac. An unsigned generic
iPhone build succeeds, and the target iPhone was observed running iOS 27.0.
The signed device build is blocked: Xcode reports that the signed-in Personal
Team cannot provision the Manage Thread Network Credentials (development)
capability. A development team that supports this entitlement is required;
do not remove the entitlement merely to make the build sign, since the API
would then be unavailable. No app has been installed or tested on the phone.
Treat this as an experiment, not a guaranteed way to obtain a code.
