import SwiftUI

struct PopoverView: View {
    @ObservedObject var model: AppModel
    @State private var showSettings = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if let err = model.error { ErrorBanner(failure: err) { model.error = nil } }
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
        .sheet(isPresented: $showSettings) { SettingsView(model: model) }
    }

    private var header: some View {
        HStack {
            Image(systemName: "waveform.circle.fill").font(.title2).foregroundStyle(.tint)
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
            Button { showSettings = true } label: { Image(systemName: "gearshape") }
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

struct SettingsView: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Form {
            TextField("Host SSH", text: $model.host)
            TextField("Percorso di gweb sul Raspberry", text: $model.gwebPath)
            Toggle("Avvia al login", isOn: Binding(get: { model.launchAtLogin },
                                                   set: { model.launchAtLogin = $0 }))
            Text("L'host è un alias di ~/.ssh/config (o utente@indirizzo) con accesso a chiave.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Fatto") {
                    dismiss()
                    Task { await model.refresh() }
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding()
        .frame(width: 360)
    }
}
