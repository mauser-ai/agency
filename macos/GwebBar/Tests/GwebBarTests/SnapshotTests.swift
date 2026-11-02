import AppKit
import SwiftUI
import XCTest
@testable import GwebBar

/// Disegna la finestrella nei suoi stati principali. Con SNAPSHOT_DIR impostato salva i PNG
/// (controllo visivo manuale); senza, verifica solo che il layout si costruisca.
@MainActor
final class SnapshotTests: XCTestCase {
    private func rec(_ state: String, upload: String = "not_uploaded", title: String = "Briefing Cliente Rossi") -> Recording {
        try! JSONDecoder().decode(Recording.self, from: Data("""
        {"id": "\(title)", "title": "\(title)", "state": "\(state)", "duration_seconds": 1834,
         "upload_status": "\(upload)", "notebook_title": "Cliente Rossi - nuovo sito", "upload_on_ready": 0}
        """.utf8))
    }

    private func model(_ phase: AppModel.Phase) -> AppModel {
        let m = AppModel(autoStart: false)
        m.phase = phase
        m.reachable = true
        m.recordingStart = Date().addingTimeInterval(-754)
        m.notebooks = [
            Notebook(id: "a", title: "Cliente Rossi - nuovo sito", sourcesCount: 3, modifiedAt: nil),
            Notebook(id: "b", title: "Agenzia - interno", sourcesCount: 12, modifiedAt: nil),
            Notebook(id: "c", title: "BIG DATA", sourcesCount: 11, modifiedAt: nil),
        ]
        m.recent = [rec("ready", upload: "ready", title: "Kickoff e-commerce"),
                    rec("ready", upload: "processing", title: "Revisione wireframe"),
                    rec("ready", title: "Prova microfono")]
        return m
    }

    private func render(_ m: AppModel, _ name: String) throws {
        try render(PopoverView(model: m), name)
    }

    private func render<V: View>(_ view: V, _ name: String) throws {
        for (suffix, look) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            try render(view, "\(name)-\(suffix)", appearance: NSAppearance(named: look)!)
        }
    }

    private func render<V: View>(_ view: V, _ name: String, appearance: NSAppearance) throws {
        let host = NSHostingView(rootView: view.background(Color(nsColor: .windowBackgroundColor)))
        host.appearance = appearance
        host.frame = NSRect(origin: .zero, size: host.fittingSize)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        XCTAssertGreaterThan(host.fittingSize.height, 100)
        guard let dir = ProcessInfo.processInfo.environment["SNAPSHOT_DIR"],
              let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return }
        host.cacheDisplay(in: host.bounds, to: rep)
        try rep.representation(using: .png, properties: [:])!
            .write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name).png"))
    }

    func testIdle() throws { try render(model(.idle), "1-idle") }
    func testRecording() throws { try render(model(.recording(rec("recording"))), "2-recording") }
    func testChoosing() throws { try render(model(.choosing(rec("finalizing"))), "3-choosing") }

    func testSessionExpiredBanner() throws {
        let m = model(.idle)
        m.session = try JSONDecoder().decode(SessionSummary.self, from: Data(
            #"{"state": "expired", "account": "sacchetto.riccardo@gmail.com", "blocked_jobs": 1}"#.utf8))
        try render(m, "4-session-expired")
    }

    func testSettings() throws {
        let m = model(.idle)
        m.session = try JSONDecoder().decode(SessionSummary.self, from: Data(
            #"{"state": "ok", "account": "sacchetto.riccardo@gmail.com", "checked_at": "2026-11-02T18:51:46.42Z", "blocked_jobs": 0}"#.utf8))
        m.connectionTest = "✓ Raspberry raggiungibile, servizio gweb attivo"
        try render(SettingsView(model: m), "5-settings")
    }
}
