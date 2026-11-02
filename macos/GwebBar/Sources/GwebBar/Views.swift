import SwiftUI

struct PopoverView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if let err = model.error { ErrorBanner(failure: err) { model.error = nil } }
            SessionBanner(model: model)
            if let notice = model.notice {
                Label(notice, systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .font(.callout)
            }
            switch model.phase {
            case .idle: IdleView(model: model)
            case .recording(let rec): RecordingView(model: model, recording: rec)
            case .choosing(let rec): NotebookPicker(model: model, recording: rec)
            }
            if case .choosing = model.phase {} else {
                RecentList(model: model)
            }
            Divider()
            footer
        }
        .padding(14)
        .frame(width: 340)
    }

    private var header: some View {
        HStack {
            Image(nsImage: Logo.menuBarImage(height: 18))
                .renderingMode(.template)
                .foregroundStyle(Color(nsColor: Logo.adaptiveGreen))
            Text("Briefing").font(.headline)
            Spacer()
            if let busy = model.busy {
                ProgressView().controlSize(.small)
                Text(busy).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(model.reachable == true ? (model.daemonAlive ? Color.green : Color.orange)
                      : model.reachable == false ? Color.red : Color.gray)
                .frame(width: 8, height: 8)
            Text(model.reachable == true
                 ? (model.daemonAlive ? "Raspberry collegato" : "Servizio gweb fermo sul Raspberry")
                 : model.reachable == false ? "Raspberry non raggiungibile" : "Connessione…")
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
            Button { Windows.shared.showSettings() } label: { Image(systemName: "gearshape") }
                .buttonStyle(.borderless).help("Impostazioni")
            Button { NSApp.terminate(nil) } label: { Image(systemName: "power") }
                .buttonStyle(.borderless).help("Esci")
        }
    }
}

struct IdleView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField(Format.defaultTitle(), text: $model.titleDraft)
                .textFieldStyle(.roundedBorder)
                .onSubmit { model.startRecording() }
            Button {
                model.startRecording()
            } label: {
                Label("Nuova registrazione", systemImage: "record.circle")
            }
            .buttonStyle(PrimaryButtonStyle(color: .red))
            .disabled(model.busy != nil || model.reachable == false || !model.daemonAlive)
            Text("Il titolo è facoltativo: diventa il nome della fonte su NotebookLM.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct RecordingView: View {
    @ObservedObject var model: AppModel
    let recording: Recording

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Circle().fill(.red).frame(width: 10, height: 10)
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    Text(Format.duration(model.recordingStart.map { ctx.date.timeIntervalSince($0) }))
                        .font(.system(size: 34, weight: .semibold, design: .rounded).monospacedDigit())
                }
            }
            Text(recording.title).font(.callout).lineLimit(2)
            Text("Puoi chiudere questa finestra o spegnere il Mac: registra il Raspberry.")
                .font(.caption).foregroundStyle(.secondary)
            Button {
                model.stopRecording()
            } label: {
                Label("Stop", systemImage: "stop.fill")
            }
            .buttonStyle(PrimaryButtonStyle(color: Color(white: 0.28)))
            .disabled(model.busy != nil)
        }
    }
}

struct NotebookPicker: View {
    @ObservedObject var model: AppModel
    let recording: Recording
    @State private var search = ""
    @State private var newTitle = ""
    @State private var selection: Notebook?

    private var filtered: [Notebook] {
        let q = search.trimmingCharacters(in: .whitespaces)
        return q.isEmpty ? model.notebooks
            : model.notebooks.filter { $0.title.localizedCaseInsensitiveContains(q) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Dove la carico?").font(.headline)
            Text("«\(recording.title)»\(recording.durationSeconds.map { " · " + Format.duration($0) } ?? "")")
                .font(.caption).foregroundStyle(.secondary).lineLimit(1)

            HStack {
                TextField("Nuovo notebook", text: $newTitle)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { model.createNotebookAndUpload(recording, title: newTitle) }
                Button("Crea e carica") { model.createNotebookAndUpload(recording, title: newTitle) }
                    .disabled(newTitle.trimmingCharacters(in: .whitespaces).isEmpty || model.busy != nil)
            }

            TextField("Cerca tra i notebook", text: $search).textFieldStyle(.roundedBorder)
            Group {
                if model.notebooksLoading && model.notebooks.isEmpty {
                    HStack { ProgressView().controlSize(.small); Text("Carico i notebook…").font(.caption) }
                        .frame(maxWidth: .infinity, minHeight: 120)
                } else {
                    List(filtered, selection: $selection) { nb in
                        HStack {
                            Text(nb.title).lineLimit(1)
                            Spacer()
                            if let n = nb.sourcesCount {
                                Text("\(n)").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .tag(nb)
                        .contentShape(Rectangle())
                        .onTapGesture(count: 2) { model.upload(recording, to: nb) }
                    }
                    .listStyle(.bordered(alternatesRowBackgrounds: true))
                    .frame(height: 180)
                }
            }

            HStack {
                Button("Non caricare") { model.skipUpload() }
                Spacer()
                Button("Carica") { if let nb = selection { model.upload(recording, to: nb) } }
                    .buttonStyle(PrimaryButtonStyle(color: .accentColor, fullWidth: false))
                    .keyboardShortcut(.defaultAction)
                    .disabled(selection == nil || model.busy != nil)
            }
        }
    }
}

struct RecentList: View {
    @ObservedObject var model: AppModel

    var body: some View {
        let items = model.recent.filter { !$0.isActive }.prefix(4)
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("Ultime registrazioni").font(.caption).foregroundStyle(.secondary)
                ForEach(Array(items)) { rec in
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(rec.title).font(.callout).lineLimit(1)
                            Text([rec.statusLabel, rec.notebookTitle].compactMap { $0 }.joined(separator: " · "))
                                .font(.caption2).foregroundStyle(color(rec)).lineLimit(1)
                        }
                        Spacer()
                        if rec.state == "ready" && ["not_uploaded", "failed", "cancelled"].contains(rec.uploadStatus ?? "")
                            && rec.uploadOnReady != 1 {
                            Button("Carica…") { model.choose(rec) }
                                .controlSize(.small)
                                .disabled(model.busy != nil || model.isRecording)
                        }
                    }
                }
            }
        }
    }

    private func color(_ rec: Recording) -> Color {
        switch rec.uploadStatus {
        case "ready": return .green
        case "failed", "blocked_auth": return .red
        case "uncertain": return .orange
        default: return .secondary
        }
    }
}

/// Pulsante pieno disegnato da SwiftUI: a differenza di `.borderedProminent` resta visibile
/// anche quando la finestrella della barra dei menu non è la finestra attiva.
struct PrimaryButtonStyle: ButtonStyle {
    var color: Color
    var fullWidth = true
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.vertical, fullWidth ? 9 : 5)
            .padding(.horizontal, 14)
            .frame(maxWidth: fullWidth ? .infinity : nil)
            .background(color.opacity(isEnabled ? (configuration.isPressed ? 0.75 : 1) : 0.35),
                        in: RoundedRectangle(cornerRadius: 8))
            .contentShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ErrorBanner: View {
    let failure: RemoteGweb.Failure
    let dismiss: () -> Void

    var body: some View {
        HStack(alignment: .top) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text(failure.message).font(.callout)
                if let hint = failure.hint { Text(hint).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer()
            Button(action: dismiss) { Image(systemName: "xmark") }.buttonStyle(.borderless)
        }
        .padding(8)
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// Login in corso o sessione da rifare: visibile subito aprendo la finestrella.
struct SessionBanner: View {
    @ObservedObject var model: AppModel

    var body: some View {
        if let progress = model.loginProgress {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(progress).font(.callout)
            }
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.blue.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
        } else if let s = model.session, s.needsLogin {
            VStack(alignment: .leading, spacing: 6) {
                Label("NotebookLM: \(s.label.lowercased())", systemImage: "person.crop.circle.badge.exclamationmark")
                    .font(.callout.weight(.semibold))
                if let n = s.blockedJobs, n > 0 {
                    Text("\(n) caricament\(n == 1 ? "o" : "i") in attesa del login.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Button("Accedi con Google…") { model.loginNotebookLM() }
                    .buttonStyle(PrimaryButtonStyle(color: .orange, fullWidth: false))
            }
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @State private var hostDraft = ""
    @State private var pathDraft = ""

    var body: some View {
        Form {
            Section("Raspberry") {
                TextField("Indirizzo (alias SSH o utente@host)", text: $hostDraft)
                TextField("Percorso di gweb", text: $pathDraft)
                HStack {
                    Button("Salva e prova la connessione") {
                        model.host = hostDraft.trimmingCharacters(in: .whitespaces)
                        model.gwebPath = pathDraft.trimmingCharacters(in: .whitespaces)
                        model.testConnection()
                    }
                    .disabled(hostDraft.trimmingCharacters(in: .whitespaces).isEmpty)
                    if let t = model.connectionTest {
                        Text(t).font(.caption)
                            .foregroundStyle(t.hasPrefix("✓") ? .green : t.hasPrefix("✗") ? .red : .secondary)
                            .lineLimit(2)
                    }
                }
                Text(verbatim: "Es. \"raspberry\" (da ~/.ssh/config), \"riccardo@dartsvision.local\" o l'indirizzo Tailscale. Serve l'accesso SSH a chiave, senza password.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("NotebookLM") {
                LabeledContent("Account") {
                    Text(model.session?.account ?? "—").textSelection(.enabled)
                }
                LabeledContent("Stato") {
                    HStack(spacing: 6) {
                        Circle().fill(color).frame(width: 8, height: 8)
                        Text(model.session?.label ?? "Sconosciuto")
                    }
                }
                if let checked = model.session?.checkedAt.flatMap(Format.localTime) {
                    LabeledContent("Ultimo controllo", value: checked)
                }
                if let progress = model.loginProgress {
                    HStack { ProgressView().controlSize(.small); Text(progress) }
                }
                HStack {
                    Button(model.session?.state == "missing" ? "Accedi con Google…" : "Rinnova sessione…") {
                        model.loginNotebookLM()
                    }
                    Button("Cambia account…") { model.loginNotebookLM() }
                        .disabled(model.session?.state == "missing")
                    Spacer()
                    Button("Verifica ora") { model.checkSessionNow() }
                    Button("Scollega", role: .destructive) { model.logoutNotebookLM() }
                        .disabled(model.session?.state == "missing")
                }
                .disabled(model.loginProgress != nil || model.busy != nil)
                Text("L'account NotebookLM è uno solo per tutto il team: è quello salvato sul Raspberry. Il login si fa qui sul Mac (si apre il browser), la sessione passa al Raspberry e la copia sul Mac viene cancellata. Cambiare account lo cambia per tutti.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("Generale") {
                Toggle("Avvia al login del Mac", isOn: Binding(get: { model.launchAtLogin },
                                                               set: { model.launchAtLogin = $0 }))
            }

            if let err = model.error {
                ErrorBanner(failure: err) { model.error = nil }
            } else if let notice = model.notice {
                Label(notice, systemImage: "checkmark.circle.fill").foregroundStyle(.green)
            }
        }
        .formStyle(.grouped)
        .frame(width: 520)
        .fixedSize(horizontal: false, vertical: true)
        .onAppear {
            hostDraft = model.host
            pathDraft = model.gwebPath
            model.connectionTest = nil
            Task { await model.refresh() }
        }
    }

    private var color: Color {
        switch model.session?.state {
        case "ok": return .green
        case "expired", "missing": return .red
        case "error": return .orange
        default: return .gray
        }
    }
}
