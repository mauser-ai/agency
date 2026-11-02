import SwiftUI

@main
struct GwebBarApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        MenuBarExtra {
            PopoverView(model: model)
        } label: {
            MenuBarLabel(model: model)
        }
        .menuBarExtraStyle(.window)

        // Finestra vera (non un foglio nella finestrella): resta aperta mentre il browser del
        // login prende il primo piano.
        Window("Impostazioni Briefing", id: SettingsView.windowID) {
            SettingsView(model: model)
        }
        .windowResizability(.contentSize)
    }
}

/// Icona nella barra: microfono a riposo; pallino pieno e tempo trascorso mentre registra.
struct MenuBarLabel: View {
    @ObservedObject var model: AppModel

    var body: some View {
        if model.isRecording {
            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                HStack(spacing: 3) {
                    Image(systemName: "record.circle.fill")
                    Text(Format.duration(model.recordingStart.map { ctx.date.timeIntervalSince($0) }))
                        .monospacedDigit()
                }
            }
        } else {
            Image(systemName: model.reachable == false ? "mic.slash" : "mic")
        }
    }
}
