import Foundation
import EventKit
import MapKit
import CoreLocation

// Local, read-only EventKit bridge. Permission requests are explicit, never scheduled.
let store = EKEventStore()
let arguments = CommandLine.arguments
func emit(_ payload: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) { print(text) }
}
func readCalendars() {
    let status = EKEventStore.authorizationStatus(for: .event)
    guard status == .fullAccess else {
        emit(["ok": false, "authorization": status.rawValue, "error": "Calendar full access is required; no events read."])
        return
    }
    let all = store.calendars(for: .event)
    let selected = all.filter { ["Arkadiy", "Maxim", "Jeanne"].contains($0.title) }
    // Duplicate titles are ambiguous: require one calendar per person.
    guard selected.count == 3, Set(selected.map { $0.title }).count == 3 else {
        emit(["ok": false, "error": "Expected exactly one Arkadiy, Maxim and Jeanne calendar.", "calendars": all.map { $0.title }])
        return
    }
    let now = Date()
    let predicate = store.predicateForEvents(withStart: now, end: now.addingTimeInterval(86400), calendars: selected)
    let formatter = ISO8601DateFormatter()
    let events: [[String: Any]] = store.events(matching: predicate).map { event in
        var row: [String: Any] = [
            "id": event.calendarItemIdentifier,
            "calendar": event.calendar.title,
            "title": event.title ?? "Appointment",
            "start": formatter.string(from: event.startDate),
            "end": formatter.string(from: event.endDate),
            "allDay": event.isAllDay,
            "location": event.location ?? "",
            "url": event.url?.absoluteString ?? "",
            "status": event.status.rawValue
        ]
        if let coordinate = event.structuredLocation?.geoLocation?.coordinate {
            row["latitude"] = coordinate.latitude
            row["longitude"] = coordinate.longitude
        }
        return row
    }
    emit(["ok": true, "generatedAt": formatter.string(from: now), "calendars": selected.map { $0.title }, "events": events])
}
if arguments.count == 5 && arguments[1] == "--eta",
   let latitude = Double(arguments[3]), let longitude = Double(arguments[4]) {
    let geocoder = CLGeocoder()
    geocoder.geocodeAddressString(arguments[2]) { places, error in
        guard error == nil, let places = places, places.count == 1,
              let origin = places.first?.location else {
            emit(["ok": false, "error": "Home address did not resolve unambiguously."])
            exit(1)
        }
        let request = MKDirections.Request()
        request.source = MKMapItem(placemark: MKPlacemark(coordinate: origin.coordinate))
        request.destination = MKMapItem(placemark: MKPlacemark(coordinate: CLLocationCoordinate2D(latitude: latitude, longitude: longitude)))
        request.transportType = .automobile
        request.departureDate = Date()
        MKDirections(request: request).calculateETA { response, error in
            guard error == nil, let response = response else {
                emit(["ok": false, "error": "Driving ETA unavailable."])
                exit(1)
            }
            emit(["ok": true, "seconds": response.expectedTravelTime,
                  "generatedAt": ISO8601DateFormatter().string(from: Date())])
            exit(0)
        }
    }
    RunLoop.main.run()
} else if arguments.contains("--request-access") || arguments.count == 1 {
    store.requestFullAccessToEvents { granted, error in
        emit(["granted": granted, "error": error?.localizedDescription ?? ""])
        exit(granted ? 0 : 1)
    }
    RunLoop.main.run()
} else {
    readCalendars()
}
