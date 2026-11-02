import Foundation

// Sottoinsieme dell'output `--json` di gweb (contratto in docs/commands.md: i campi
// possono aumentare, quindi qui sono quasi tutti opzionali).

struct Recording: Decodable, Identifiable, Equatable {
    let id: String
    let title: String
    let state: String
    let createdAt: String?
    let startedAt: String?
    let durationSeconds: Double?
    let notebookId: String?
    let notebookTitle: String?
    let uploadStatus: String?
    let error: String?
    let uploadOnReady: Int?

    enum CodingKeys: String, CodingKey {
        case id, title, state, error
        case createdAt = "created_at"
        case startedAt = "started_at"
        case durationSeconds = "duration_seconds"
        case notebookId = "notebook_id"
        case notebookTitle = "notebook_title"
        case uploadStatus = "upload_status"
        case uploadOnReady = "upload_on_ready"
    }

    var isActive: Bool { ["requested", "recording", "stopping"].contains(state) }

    /// Stato leggibile, dal punto di vista di chi aspetta il notebook.
    var statusLabel: String {
        switch state {
        case "requested": return "In avvio…"
        case "recording": return "In registrazione"
        case "stopping": return "In arresto…"
        case "finalizing":
            return uploadOnReady == 1 ? "Preparazione file, poi caricamento…" : "Preparazione file…"
        case "failed": return "Registrazione non riuscita"
        default: break
        }
        switch uploadStatus {
        case "not_uploaded", nil: return "Non caricata"
        case "queued": return "In coda per il caricamento"
        case "uploading": return "Caricamento…"
        case "processing": return "Caricata, NotebookLM sta trascrivendo…"
        case "ready": return "Pronta su NotebookLM"
        case "uncertain": return "Esito caricamento incerto (verifica in corso)"
        case "blocked_auth": return "Serve un nuovo login NotebookLM"
        case "failed": return "Caricamento non riuscito"
        case "cancelled": return "Caricamento annullato"
        default: return uploadStatus ?? ""
        }
    }
}

struct Daemon: Decodable, Equatable {
    let alive: Bool
}

struct RecordStatus: Decodable, Equatable {
    let active: Bool
    let daemon: Daemon?
    let recording: Recording?
    let last: Recording?
    let elapsedSeconds: Double?
    let recordedSeconds: Double?

    enum CodingKeys: String, CodingKey {
        case active, daemon, recording, last
        case elapsedSeconds = "elapsed_seconds"
        case recordedSeconds = "recorded_seconds"
    }
}

struct RecordingEnvelope: Decodable {
    let recording: Recording
    let warnings: [String]?
}

struct Notebook: Decodable, Identifiable, Equatable, Hashable {
    let id: String
    let title: String
    let sourcesCount: Int?
    let modifiedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title
        case sourcesCount = "sources_count"
        case modifiedAt = "modified_at"
    }
}

struct UploadResult: Decodable {
    let scheduled: Bool?
    let warnings: [String]?
}

struct RemoteErrorBody: Decodable {
    struct Inner: Decodable {
        let code: String?
        let message: String
        let hint: String?
    }
    let ok: Bool?
    let error: Inner
}

enum Format {
    static func duration(_ seconds: Double?) -> String {
        let s = max(0, Int(seconds ?? 0))
        let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, sec) : String(format: "%d:%02d", m, sec)
    }

    static func defaultTitle(now: Date = Date()) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "it_IT")
        f.dateFormat = "d MMM yyyy HH:mm"
        return "Briefing \(f.string(from: now))"
    }
}
