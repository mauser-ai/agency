import AppKit
import SwiftUI

@main
struct GwebBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        MenuBarExtra {
            PopoverView(model: Windows.shared.model)
        } label: {
            MenuBarLabel(model: Windows.shared.model)
        }
        .menuBarExtraStyle(.window)
    }
}

/// L'icona nella barra può non vedersi (notch con molte icone, permessi della barra di
/// macOS 26): la stessa interfaccia è anche in una finestra normale, aperta all'avvio e a ogni
/// riapertura dell'app (Spotlight, Finder).
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        Windows.shared.showMain()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        Windows.shared.showMain()
        return false
    }
}

/// Finestre AppKit con contenuto SwiftUI: apribili da qualunque punto (anche dalla finestrella
/// della barra), restano aperte mentre il browser del login passa in primo piano.
@MainActor
final class Windows {
    static let shared = Windows()
    let model = AppModel()
    private var main: NSWindow?
    private var settings: NSWindow?

    func showMain() {
        main = show(main, title: "Briefing", autosave: "GwebBarMain") {
            PopoverView(model: model)
        }
    }

    func showSettings() {
        settings = show(settings, title: "Impostazioni Briefing", autosave: "GwebBarSettings") {
            SettingsView(model: model)
        }
    }

    private func show<V: View>(_ existing: NSWindow?, title: String, autosave: String,
                               @ViewBuilder content: () -> V) -> NSWindow {
        let window = existing ?? {
            let w = NSWindow(contentViewController: NSHostingController(rootView: content()))
            w.title = title
            w.styleMask = [.titled, .closable, .miniaturizable]
            w.isReleasedWhenClosed = false
            w.setFrameAutosaveName(autosave)
            if !w.setFrameUsingName(autosave) { w.center() }
            return w
        }()
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        return window
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
