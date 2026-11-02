import AppKit
import Carbon.HIToolbox
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

/// Finestre dell'app: il pannello a tendina sotto la barra (stile Fathom) e le Impostazioni.
@MainActor
final class Windows {
    static let shared = Windows()
    let model = AppModel()
    private lazy var panel = DropdownPanel(model: model)
    private var settings: NSWindow?
    private var statusItem: StatusItem?
    private var hotKey: HotKey?

    func installStatusItem() {
        statusItem = StatusItem(model: model) { [weak self] anchor in self?.panel.toggle(below: anchor) }
        // ⌥⌘B apre il pannello anche quando l'icona è nascosta dal notch.
        hotKey = HotKey(keyCode: 0x0B, modifiers: [.command, .option]) { [weak self] in
            MainActor.assumeIsolated { self?.panel.toggle(below: self?.statusItem?.visibleButton) }
        }
    }

    func showMain() {
        panel.show(below: statusItem?.visibleButton)
    }

    func showSettings() {
        panel.close()
        let window = settings ?? {
            let w = NSWindow(contentViewController: NSHostingController(rootView: SettingsView(model: model)))
            w.title = "Impostazioni Briefing"
            w.styleMask = [.titled, .closable, .miniaturizable]
            w.isReleasedWhenClosed = false
            w.setFrameAutosaveName("GwebBarSettings")
            if !w.setFrameUsingName("GwebBarSettings") { w.center() }
            return w
        }()
        settings = window
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }
}

/// Pannello senza bordi agganciato sotto la barra dei menu: sotto l'icona se è visibile,
/// altrimenti in alto a destra. Si chiude cliccando fuori o con Esc, come un menu.
@MainActor
final class DropdownPanel: NSPanel {
    private let hosting: NSHostingView<PanelChrome<PopoverView>>
    private var anchorButton: NSStatusBarButton?
    private var outsideClick: Any?
    private var bag = Set<AnyCancellable>()

    init(model: AppModel) {
        hosting = NSHostingView(rootView: PanelChrome { PopoverView(model: model) })
        super.init(contentRect: NSRect(x: 0, y: 0, width: 340, height: 300),
                   styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        isFloatingPanel = true
        level = .popUpMenu
        backgroundColor = .clear
        isOpaque = false
        hasShadow = true
        hidesOnDeactivate = false
        isReleasedWhenClosed = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        contentView = hosting
        // Il contenuto cambia altezza (registrazione, scelta del notebook…): il pannello segue.
        model.objectWillChange
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in DispatchQueue.main.async { self?.fitIfVisible() } }
            .store(in: &bag)
    }

    override var canBecomeKey: Bool { true }
    override func cancelOperation(_ sender: Any?) { close() }

    func toggle(below button: NSStatusBarButton?) {
        isVisible ? close() : show(below: button)
    }

    func show(below button: NSStatusBarButton?) {
        anchorButton = button
        fit()
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if outsideClick == nil {
            outsideClick = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
                MainActor.assumeIsolated { self?.close() }
            }
        }
    }

    override func close() {
        if let m = outsideClick { NSEvent.removeMonitor(m); outsideClick = nil }
        super.close()
    }

    private func fitIfVisible() {
        if isVisible { fit() }
    }

    private func fit() {
        let size = hosting.fittingSize
        guard let screen = anchorButton?.window?.screen ?? NSScreen.main else { return }
        let top = screen.visibleFrame.maxY - 6
        var x: CGFloat
        if let button = anchorButton, let win = button.window {
            x = win.convertToScreen(button.convert(button.bounds, to: nil)).midX - size.width / 2
        } else {
            x = screen.visibleFrame.maxX - size.width - 12
        }
        x = min(max(x, screen.visibleFrame.minX + 8), screen.visibleFrame.maxX - size.width - 8)
        let frame = NSRect(x: x, y: top - size.height, width: size.width, height: size.height)
        if frame != self.frame { setFrame(frame, display: true) }
    }
}

/// Fondo del pannello: materiale dei menu di macOS, angoli arrotondati e bordo sottile.
struct PanelChrome<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
            .background(VisualEffect().clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous)))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.12), lineWidth: 1))
    }
}

struct VisualEffect: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = .menu
        v.blendingMode = .behindWindow
        v.state = .active
        return v
    }

    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {}
}

/// Scorciatoia globale con l'API Carbon (non richiede il permesso Accessibilità).
final class HotKey {
    private var ref: EventHotKeyRef?
    private var handler: EventHandlerRef?
    fileprivate let action: () -> Void

    init(keyCode: UInt32, modifiers: NSEvent.ModifierFlags, action: @escaping () -> Void) {
        self.action = action
        var carbon: UInt32 = 0
        if modifiers.contains(.command) { carbon |= UInt32(cmdKey) }
        if modifiers.contains(.option) { carbon |= UInt32(optionKey) }
        if modifiers.contains(.control) { carbon |= UInt32(controlKey) }
        if modifiers.contains(.shift) { carbon |= UInt32(shiftKey) }
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let me = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            guard let userData else { return noErr }
            let hk = Unmanaged<HotKey>.fromOpaque(userData).takeUnretainedValue()
            DispatchQueue.main.async { hk.action() }
            return noErr
        }, 1, &spec, me, &handler)
        RegisterEventHotKey(keyCode, carbon, EventHotKeyID(signature: OSType(0x4757_4542), id: 1),
                            GetApplicationEventTarget(), 0, &ref)
    }

    deinit {
        if let ref { UnregisterEventHotKey(ref) }
        if let handler { RemoveEventHandler(handler) }
    }
}

/// Icona nella barra. Fatta in AppKit invece che con `MenuBarExtra`: sul Mac dell'utente
/// (notch, barra piena) `MenuBarExtra` ridisegnava il pulsante all'infinito
/// (`updateButton` → `setImage` → layout → nuovo aggiornamento), il thread principale restava
/// occupato e i pulsanti non rispondevano. Qui l'icona cambia solo quando cambia lo stato.
@MainActor
final class StatusItem: NSObject {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let model: AppModel
    private let onClick: (NSStatusBarButton?) -> Void
    private var bag = Set<AnyCancellable>()
    private var ticker: Timer?
    private var shownSymbol: String?

    init(model: AppModel, onClick: @escaping (NSStatusBarButton?) -> Void) {
        self.model = model
        self.onClick = onClick
        super.init()
        item.button?.target = self
        item.button?.action = #selector(clicked)
        item.button?.imagePosition = .imageLeading
        model.$phase.combineLatest(model.$reachable)
            .receive(on: RunLoop.main)
            .sink { [weak self] _, _ in self?.render() }
            .store(in: &bag)
        render()
    }

    /// Il pulsante, solo se davvero visibile (non nascosto dal notch o dalla barra piena).
    var visibleButton: NSStatusBarButton? {
        guard let b = item.button, let w = b.window, w.occlusionState.contains(.visible),
              let screen = w.screen, screen.frame.intersects(w.frame) else { return nil }
        return b
    }

    @objc private func clicked() { onClick(item.button) }

    private func render() {
        // Logo GWEB monocromatico (template): pallino in registrazione, attenuato se il
        // Raspberry non risponde.
        let state = model.isRecording ? "rec" : (model.reachable == false ? "off" : "on")
        if state != shownSymbol, let button = item.button {
            shownSymbol = state
            button.image = Logo.menuBarImage(dot: state == "rec")
            button.appearsDisabled = state == "off"
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
