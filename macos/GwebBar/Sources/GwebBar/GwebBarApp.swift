import AppKit
import Combine
import SwiftUI

@main
struct GwebBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    // Nessuna scena SwiftUI visibile: icona e finestre sono gestite in AppKit (vedi StatusItem).
    var body: some Scene {
        Settings { EmptyView() }
    }
}

/// L'icona nella barra può non vedersi (notch con molte icone, permessi della barra di
/// macOS 26): la stessa interfaccia è anche in una finestra normale, aperta all'avvio e a ogni
/// riapertura dell'app (Spotlight, Finder).
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        Windows.shared.installStatusItem()
        Windows.shared.showMain()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        Windows.shared.showMain()
        return false
    }
}

/// Finestre AppKit con contenuto SwiftUI: apribili da qualunque punto (anche dal popover della
/// barra), restano aperte mentre il browser del login passa in primo piano.
@MainActor
final class Windows {
    static let shared = Windows()
    let model = AppModel()
    private var main: NSWindow?
    private var settings: NSWindow?
    private var statusItem: StatusItem?

    func installStatusItem() {
        statusItem = StatusItem(model: model)
    }

    func showMain() {
        main = show(main, title: "Briefing", autosave: "GwebBarMain") {
            PopoverView(model: model)
        }
    }

    func showSettings() {
        statusItem?.closePopover()
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

/// Icona nella barra con popover. Fatta in AppKit invece che con `MenuBarExtra`: sul Mac
/// dell'utente (notch, barra piena) `MenuBarExtra` ridisegnava il pulsante all'infinito
/// (`updateButton` → `setImage` → layout → nuovo aggiornamento), il thread principale restava
/// occupato e i pulsanti non rispondevano. Qui l'icona cambia solo quando cambia lo stato.
@MainActor
final class StatusItem: NSObject {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let popover = NSPopover()
    private let model: AppModel
    private var bag = Set<AnyCancellable>()
    private var ticker: Timer?
    private var shownSymbol: String?

    init(model: AppModel) {
        self.model = model
        super.init()
        popover.behavior = .transient
        popover.contentViewController = NSHostingController(rootView: PopoverView(model: model))
        item.button?.target = self
        item.button?.action = #selector(toggle)
        item.button?.imagePosition = .imageLeading
        model.$phase.combineLatest(model.$reachable)
            .receive(on: RunLoop.main)
            .sink { [weak self] _, _ in self?.render() }
            .store(in: &bag)
        render()
    }

    func closePopover() { popover.performClose(nil) }

    @objc private func toggle() {
        guard let button = item.button else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    private func render() {
        let symbol = model.isRecording ? "record.circle.fill" : (model.reachable == false ? "mic.slash" : "mic")
        if symbol != shownSymbol, let button = item.button {
            shownSymbol = symbol
            let image = NSImage(systemSymbolName: symbol, accessibilityDescription: "Briefing")
            image?.isTemplate = true
            button.image = image
        }
        if model.isRecording {
            if ticker == nil {
                ticker = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                    MainActor.assumeIsolated { self?.updateTitle() }
                }
            }
            updateTitle()
        } else {
            ticker?.invalidate()
            ticker = nil
            if item.button?.title.isEmpty == false { item.button?.title = "" }
        }
    }

    private func updateTitle() {
        let text = " " + Format.duration(model.recordingStart.map { Date().timeIntervalSince($0) })
        if item.button?.title != text { item.button?.title = text }
    }
}
