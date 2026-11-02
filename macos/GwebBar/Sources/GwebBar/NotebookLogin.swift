import Foundation

/// Login Google per NotebookLM eseguito sul Mac (serve un browser), con notebooklm-py in un
/// ambiente Python dedicato. La sessione prodotta va subito al Raspberry e la copia locale
/// viene cancellata: un solo client usa (e ruota) quei cookie.
enum NotebookLogin {
    /// Stessa versione bloccata da gweb sul Pi (src/agency_briefing/notebook/notebooklm_py.py).
    static let libraryVersion = "0.8.6"
    /// Profilo usa-e-getta: `--fresh` + cancellazione a fine login = scelta dell'account ogni
    /// volta, senza toccare il profilo `default` di chi usa notebooklm anche da terminale.
    static let profile = "gwebbar"

    static var home: URL { FileManager.default.homeDirectoryForCurrentUser }
    static var venv: URL { home.appendingPathComponent(".venvs/notebooklm") }
    static var cli: URL { venv.appendingPathComponent("bin/notebooklm") }
    static var profileDir: URL { home.appendingPathComponent(".notebooklm/profiles/\(profile)") }
    static var hasChrome: Bool { FileManager.default.fileExists(atPath: "/Applications/Google Chrome.app") }

    struct Failure: Error {
        let message: String
        let hint: String?
    }

    /// Prepara notebooklm-py se manca (una volta sola, circa un minuto).
    static func ensureTool(progress: @escaping @MainActor (String) -> Void) async throws {
        if FileManager.default.isExecutableFile(atPath: cli.path) { return }
        guard let python = findPython() else {
            throw Failure(message: "Serve Python 3.10 o successivo sul Mac",
                          hint: "Installa Python da python.org (o con Homebrew), poi riprova.")
        }
        await progress("Preparo lo strumento di login (una volta sola)…")
        try await step(python, ["-m", "venv", venv.path], timeout: 120)
        await progress("Scarico notebooklm-py \(libraryVersion)…")
        try await step(venv.appendingPathComponent("bin/pip").path,
                       ["install", "--quiet", "notebooklm-py[browser]==\(libraryVersion)"], timeout: 600)
        if !hasChrome {
            await progress("Scarico il browser per il login…")
            try await step(venv.appendingPathComponent("bin/playwright").path, ["install", "chromium"], timeout: 900)
        }
    }

    /// Apre il browser per il login e restituisce il contenuto di storage_state.json.
    static func login(progress: @escaping @MainActor (String) -> Void) async throws -> Data {
        try await ensureTool(progress: progress)
        let fm = FileManager.default
        let tmp = fm.temporaryDirectory.appendingPathComponent("gwebbar-login-\(UUID().uuidString)")
        try fm.createDirectory(at: tmp, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        defer {
            try? fm.removeItem(at: tmp)
            try? fm.removeItem(at: profileDir)
        }
        try? fm.removeItem(at: profileDir)
        let storage = tmp.appendingPathComponent("storage_state.json")
        await progress("Accedi a Google nella finestra del browser…")
        var args = ["-p", profile, "login", "--fresh", "--browser-timeout", "600", "--storage", storage.path]
        if hasChrome { args += ["--browser", "chrome"] }
        let (code, _, err) = try await RemoteGweb.exec(cli.path, args, environment: toolEnvironment(), timeout: 660)
        guard code == 0, let data = try? Data(contentsOf: storage) else {
            let detail = String(decoding: err, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            throw Failure(message: "Login non completato",
                          hint: detail.isEmpty ? "La finestra è stata chiusa prima della fine?" : String(detail.suffix(300)))
        }
        return data
    }

    // MARK: supporto

    private static func step(_ exe: String, _ args: [String], timeout: TimeInterval) async throws {
        let (code, _, err) = try await RemoteGweb.exec(exe, args, environment: toolEnvironment(), timeout: timeout)
        if code != 0 {
            throw Failure(message: "Preparazione non riuscita (\((exe as NSString).lastPathComponent))",
                          hint: String(String(decoding: err, as: UTF8.self).suffix(300)))
        }
    }

    /// Le app avviate dal Finder hanno un PATH minimo: aggiungiamo i percorsi usuali.
    private static func toolEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
                       env["PATH"] ?? ""].joined(separator: ":")
        return env
    }

    static func findPython() -> String? {
        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                          "/Library/Frameworks/Python.framework/Versions/Current/bin/python3", "/usr/bin/python3"]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: path)
            p.arguments = ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"]
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
            if (try? p.run()) != nil {
                p.waitUntilExit()
                if p.terminationStatus == 0 { return path }
            }
        }
        return nil
    }
}
