import Foundation
import os
import ServiceManagement
import SwiftUI

/// Diagnostica: ~/Library/Logs/GwebBar.log (ultime righe) e il log di sistema
/// (`log stream --predicate 'subsystem == "local.agency-briefing.gwebbar"'`).
enum Diag {
    private static let logger = Logger(subsystem: "local.agency-briefing.gwebbar", category: "app")
    private static let queue = DispatchQueue(label: "gwebbar.diag")
    static let file = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/GwebBar.log")

    static func log(_ message: String) {
        logger.notice("\(message, privacy: .public)")
        let line = "\(ISO8601DateFormatter().string(from: Date())) \(message)\n"
        queue.async {
            if let h = try? FileHandle(forWritingTo: file) {
                h.seekToEndOfFile()
                h.write(Data(line.utf8))
                try? h.close()
            } else {
                try? Data(line.utf8).write(to: file)
            }
        }
    }
}

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
    @Published var session: SessionSummary?
    /// Avanzamento del login NotebookLM (nil = nessun login in corso).
    @Published var loginProgress: String?
    @Published var connectionTest: String?

    @AppStorage("host") var host = "riccardo@dartsvision.local"
    @AppStorage("gwebPath") var gwebPath = "~/.local/bin/gweb"

    private var pollTask: Task<Void, Never>?

    var remote: RemoteGweb { RemoteGweb(host: host, gwebPath: gwebPath) }

    var isRecording: Bool {
        if case .recording = phase { return true }
        return false
    }

    init(autoStart: Bool = true) {
        if autoStart {
            Diag.log("avvio \(Bundle.main.bundlePath)")
            Notifier.requestAuthorization()
            startPolling()
        }
    }

    // MARK: stato

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            Diag.log("controllo periodico avviato")
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
        Diag.log("refresh verso \(host)")
        do {
            let st = try await remote.status()
            Diag.log("stato ricevuto: attiva=\(st.active)")
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
            if loginProgress == nil, let s = try? await remote.session(online: false) { updateSession(s) }
            if error?.isUnreachable == true { error = nil }
        } catch let f as RemoteGweb.Failure {
            Diag.log("refresh fallito: \(f.message) \(f.hint ?? "") (codice \(f.exitCode))")
            reachable = !f.isUnreachable
            if f.isUnreachable { error = f }
        } catch {
            Diag.log("refresh: \(error.localizedDescription)")
        }
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

    // MARK: sessione NotebookLM

    /// Aggiorna lo stato e avvisa (notifica di sistema) solo quando passa da buono a da rifare.
    func updateSession(_ s: SessionSummary) {
        let was = session
        session = s
        if s.needsLogin && !(was?.needsLogin ?? false) {
            Notifier.post(title: "NotebookLM: serve un nuovo login",
                          body: s.state == "missing" ? "Nessun account collegato al Raspberry."
                              : "La sessione è scaduta: le registrazioni restano in coda finché non accedi di nuovo.")
        }
    }

    func checkSessionNow() {
        perform("Verifico la sessione…") { [self] in
            updateSession(try await remote.session(online: true))
        }
    }

    /// Login (anche per rinnovare la sessione o cambiare account): browser sul Mac → Raspberry.
    func loginNotebookLM() {
        guard loginProgress == nil else { return }
        loginProgress = "Preparazione…"
        error = nil
        notice = nil
        Task {
            defer { loginProgress = nil }
            do {
                let data = try await NotebookLogin.login { [weak self] in self?.loginProgress = $0 }
                loginProgress = "Invio la sessione al Raspberry…"
                let check = try await remote.importSession(data)
                guard check.ok else {
                    error = RemoteGweb.Failure(message: "Google non ha accettato la sessione",
                                               hint: check.message ?? "Riprova il login.", exitCode: 5)
                    return
                }
                notice = "Collegato a NotebookLM come \(check.account ?? "account Google")"
                    + (check.accountChanged == true ? " (account cambiato)" : "")
                notebooks = []
                Notifier.post(title: "NotebookLM collegato", body: check.account ?? "Sessione attiva")
                if let s = try? await remote.session(online: false) { updateSession(s) }
            } catch let f as NotebookLogin.Failure {
                error = RemoteGweb.Failure(message: f.message, hint: f.hint, exitCode: -1)
            } catch let f as RemoteGweb.Failure {
                error = f
            } catch {
                self.error = RemoteGweb.Failure(message: error.localizedDescription, hint: nil, exitCode: -1)
            }
        }
    }

    func logoutNotebookLM() {
        perform("Scollego l'account…") { [self] in
            try await remote.logout()
            notebooks = []
            if let s = try? await remote.session(online: false) { session = s }
            notice = "Account scollegato dal Raspberry."
        }
    }

    // MARK: connessione

    func testConnection() {
        connectionTest = "Provo \(host)…"
        Task {
            do {
                let st = try await remote.status()
                reachable = true
                daemonAlive = st.daemon?.alive ?? true
                connectionTest = daemonAlive ? "✓ Raspberry raggiungibile, servizio gweb attivo"
                    : "Raspberry raggiungibile, ma il servizio gweb è fermo"
                await refresh()
            } catch let f as RemoteGweb.Failure {
                reachable = !f.isUnreachable
                connectionTest = "✗ \(f.message)" + (f.hint.map { ": \($0)" } ?? "")
            } catch {
                connectionTest = "✗ \(error.localizedDescription)"
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
