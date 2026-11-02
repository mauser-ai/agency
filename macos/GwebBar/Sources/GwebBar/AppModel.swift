import Foundation
import ServiceManagement
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    enum Phase: Equatable {
        case idle
        case recording(Recording)
        /// Registrazione ferma (o vecchia non caricata): si sceglie il notebook.
        case choosing(Recording)
    }

    @Published var phase: Phase = .idle
    @Published var titleDraft = ""
    @Published var recordingStart: Date?
    @Published var busy: String?
    @Published var error: RemoteGweb.Failure?
    @Published var notice: String?
    @Published var notebooks: [Notebook] = []
    @Published var notebooksLoading = false
    @Published var recent: [Recording] = []
    @Published var reachable: Bool?
    @Published var daemonAlive = true

    @AppStorage("host") var host = "raspberry"
    @AppStorage("gwebPath") var gwebPath = "~/.local/bin/gweb"

    private var pollTask: Task<Void, Never>?

    var remote: RemoteGweb { RemoteGweb(host: host, gwebPath: gwebPath) }

    var isRecording: Bool {
        if case .recording = phase { return true }
        return false
    }

    init(autoStart: Bool = true) {
        if autoStart { startPolling() }
    }

    // MARK: stato

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                // Più frequente mentre qualcosa cambia (registrazione, codifica, caricamento).
                let fast = self?.needsFastPolling ?? false
                try? await Task.sleep(nanoseconds: (fast ? 4 : 30) * 1_000_000_000)
            }
        }
    }

    private var needsFastPolling: Bool {
        if isRecording { return true }
        return recent.contains { ["finalizing", "stopping"].contains($0.state)
            || ["queued", "uploading", "processing", "uncertain"].contains($0.uploadStatus ?? "") }
    }

    func refresh() async {
        do {
            let st = try await remote.status()
            reachable = true
            daemonAlive = st.daemon?.alive ?? true
            if st.active, let rec = st.recording {
                recordingStart = Date().addingTimeInterval(-(st.elapsedSeconds ?? 0))
                if case .choosing = phase {} else { phase = .recording(rec) }
            } else if isRecording {
                // Fermata da un'altra parte (terminale, arresto di sicurezza): si sceglie il notebook.
                if let last = st.last, last.uploadStatus == "not_uploaded", last.uploadOnReady != 1 {
                    phase = .choosing(last)
                    loadNotebooks()
                } else {
                    phase = .idle
                }
            }
            recent = (try? await remote.recent()) ?? recent
            if error?.isUnreachable == true { error = nil }
        } catch let f as RemoteGweb.Failure {
            reachable = !f.isUnreachable
            if f.isUnreachable { error = f }
        } catch {}
    }

    // MARK: azioni

    func startRecording() {
        let title = titleDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        perform("Avvio della registrazione…") { [self] in
            let rec = try await remote.start(title: title.isEmpty ? Format.defaultTitle() : title)
            titleDraft = ""
            recordingStart = Date()
            phase = .recording(rec)
            loadNotebooks()  // pronti per quando si ferma
        }
    }

    func stopRecording() {
        perform("Arresto…") { [self] in
            let rec = try await remote.stop()
            phase = .choosing(rec)
            if notebooks.isEmpty { loadNotebooks() }
            await refresh()
        }
    }

    func choose(_ rec: Recording) {
        phase = .choosing(rec)
        loadNotebooks()
    }

    func upload(_ rec: Recording, to nb: Notebook) {
        perform("Invio a «\(nb.title)»…") { [self] in
            let res = try await remote.upload(recordingId: rec.id, notebookId: nb.id)
            phase = .idle
            notice = res.scheduled == true
                ? "Verrà caricata in «\(nb.title)» appena il file è pronto."
                : "In caricamento su «\(nb.title)»."
            await refresh()
        }
    }

    func createNotebookAndUpload(_ rec: Recording, title: String) {
        let name = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        perform("Creazione di «\(name)»…") { [self] in
            let nb = try await remote.createNotebook(title: name)
            notebooks.insert(nb, at: 0)
            let res = try await remote.upload(recordingId: rec.id, notebookId: nb.id)
            phase = .idle
            notice = res.scheduled == true
                ? "Notebook creato: la registrazione verrà caricata appena pronta."
                : "Notebook creato, caricamento in corso."
            await refresh()
        }
    }

    func skipUpload() {
        phase = .idle
        notice = "Registrazione salvata sul Raspberry, non caricata."
    }

    func loadNotebooks() {
        guard !notebooksLoading else { return }
        notebooksLoading = true
        Task {
            defer { notebooksLoading = false }
            do {
                notebooks = try await remote.notebooks()
            } catch let f as RemoteGweb.Failure {
                error = f
            } catch {}
        }
    }

    private func perform(_ label: String, _ body: @escaping () async throws -> Void) {
        guard busy == nil else { return }
        busy = label
        error = nil
        notice = nil
        Task {
            defer { busy = nil }
            do {
                try await body()
            } catch let f as RemoteGweb.Failure {
                error = f
            } catch {
                self.error = RemoteGweb.Failure(message: error.localizedDescription, hint: nil, exitCode: -1)
            }
        }
    }

    // MARK: avvio al login

    var launchAtLogin: Bool {
        get { SMAppService.mainApp.status == .enabled }
        set {
            do {
                if newValue { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            } catch {
                self.error = RemoteGweb.Failure(message: "Avvio al login non modificabile",
                                                hint: error.localizedDescription, exitCode: -1)
            }
            objectWillChange.send()
        }
    }
}
