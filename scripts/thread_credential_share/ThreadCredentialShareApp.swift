import SwiftUI
import ThreadNetwork

// iOS 27+ only. Apple displays the one-time code in a system alert; this app
// never receives, records, or transmits the code or the Thread network key.
@available(iOS 27.0, *)
@MainActor
final class CredentialShareModel: ObservableObject {
    @Published var networkName = "Not loaded"
    @Published var status = "Tap Find Apple Thread network."
    @Published var canRequestCode = false

    private let client = THClient()
    private var extendedPANID: Data?

    func findNetwork() {
        status = "Asking iOS for the preferred Thread network…"
        canRequestCode = false
        extendedPANID = nil

        client.retrievePreferredCredentials { [weak self] credentials, error in
            let name = credentials?.networkName
            let panID = credentials?.extendedPANID
            let errorDescription = error?.localizedDescription

            DispatchQueue.main.async {
                guard let self else { return }
                guard let panID, !panID.isEmpty else {
                    self.networkName = "Not available"
                    self.status = errorDescription ?? "No preferred Thread network was returned."
                    return
                }
                self.networkName = name ?? "Unnamed Thread network"
                self.extendedPANID = panID
                self.canRequestCode = true
                self.status = "Confirm this is your Apple network before requesting a code."
            }
        }
    }

    func requestCode() {
        guard let extendedPANID else { return }
        status = "Looking for a nearby eligible Apple TV…"

        client.enableCredentialSharingMode(forExtendedPANID: extendedPANID) { [weak self] error in
            let errorDescription = error?.localizedDescription
            DispatchQueue.main.async {
                self?.status = errorDescription.map { "Could not show code: \($0)" }
                    ?? "Apple's system alert handles the one-time code."
            }
        }
    }
}

@available(iOS 27.0, *)
struct CredentialShareView: View {
    @StateObject private var model = CredentialShareModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Apple Thread code")
                .font(.title)
            Text("Network: \(model.networkName)")
            Text(model.status)
                .foregroundStyle(.secondary)
            Button("Find Apple Thread network") { model.findNetwork() }
            Button("Show one-time code") { model.requestCode() }
                .disabled(!model.canRequestCode)
            Text("Only enter the code in SmartThings if the network above is the Apple Thread network you intend to join. The code is temporary and is not saved by this app.")
                .font(.footnote)
            Spacer()
        }
        .padding()
    }
}

@main
struct ThreadCredentialShareApp: App {
    var body: some Scene {
        WindowGroup {
            if #available(iOS 27.0, *) {
                CredentialShareView()
            } else {
                Text("Requires iOS 27 or later.")
            }
        }
    }
}
