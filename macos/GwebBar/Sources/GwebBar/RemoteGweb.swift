import Foundation

/// Esegue `gweb … --json` sul Raspberry tramite `/usr/bin/ssh`.
///
/// Nessuna porta di rete né API HTTP: si usa la stessa connessione SSH del terminale
/// (chiave in ~/.ssh, alias e multiplexing da ~/.ssh/config). BatchMode impedisce che
/// ssh resti appeso a chiedere una password che l'app non potrebbe mostrare.
struct RemoteGweb {
    var host: String
    var gwebPath: String

    struct Failure: Error, Equatable {
        let message: String
        let hint: String?
        let exitCode: Int32

        var isUnreachable: Bool { exitCode == 255 }
        var needsLogin: Bool { exitCode == 5 }
    }

    /// Quoting per la shell remota: ssh concatena gli argomenti in un'unica riga di comando.
    static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    func remoteCommand(_ args: [String]) -> String {
        // gwebPath può iniziare con ~/ : lasciato senza virgolette perché lo espanda la shell.
        ([gwebPath] + args.map(Self.shellQuote) + ["--json"]).joined(separator: " ")
    }

    func run<T: Decodable>(_ args: [String], as type: T.Type, timeout: TimeInterval = 60) async throws -> T {
        let (code, out, err) = try await Self.exec(
            "/usr/bin/ssh",
            ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15",
             host, remoteCommand(args)],
            timeout: timeout)
        if code == 0 {
            do {
                return try JSONDecoder().decode(T.self, from: out)
            } catch {
                throw Failure(message: "Risposta inattesa da gweb", hint: String(describing: error), exitCode: -1)
            }
        }
        if let body = try? JSONDecoder().decode(RemoteErrorBody.self, from: out) {
            throw Failure(message: body.error.message, hint: body.error.hint, exitCode: code)
        }
        let stderr = String(decoding: err, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        if code == 255 {
            throw Failure(message: "Raspberry non raggiungibile",
                          hint: stderr.isEmpty ? "Controlla che sia acceso e in rete (ssh \(host))." : stderr,
                          exitCode: code)
        }
        throw Failure(message: "Comando non riuscito (codice \(code))",
                      hint: stderr.isEmpty ? nil : String(stderr.suffix(300)), exitCode: code)
    }

    static func exec(_ path: String, _ args: [String], timeout: TimeInterval) async throws -> (Int32, Data, Data) {
        try await withCheckedThrowingContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: path)
                p.arguments = args
                let out = Pipe(), err = Pipe()
                p.standardOutput = out
                p.standardError = err
                p.standardInput = FileHandle.nullDevice
                do {
                    try p.run()
                } catch {
                    cont.resume(throwing: Failure(message: "ssh non avviabile", hint: error.localizedDescription, exitCode: -1))
                    return
                }
                let killer = DispatchWorkItem { if p.isRunning { p.terminate() } }
                DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)
                // Lettura prima di waitUntilExit: evita blocchi con output grandi.
                let outData = out.fileHandleForReading.readDataToEndOfFile()
                let errData = err.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                killer.cancel()
                cont.resume(returning: (p.terminationStatus, outData, errData))
            }
        }
    }

    // MARK: comandi usati dall'app

    func status() async throws -> RecordStatus {
        try await run(["record", "status"], as: RecordStatus.self, timeout: 20)
    }

    func start(title: String) async throws -> Recording {
        try await run(["record", "start", "--title", title], as: RecordingEnvelope.self, timeout: 40).recording
    }

    /// Arresto senza attendere la codifica: il notebook si sceglie mentre il Pi finalizza.
    func stop() async throws -> Recording {
        try await run(["record", "stop"], as: RecordingEnvelope.self, timeout: 60).recording
    }

    func notebooks() async throws -> [Notebook] {
        try await run(["notebook", "list"], as: [Notebook].self, timeout: 60)
    }

    func createNotebook(title: String) async throws -> Notebook {
        try await run(["notebook", "create", title], as: Notebook.self, timeout: 60)
    }

    func upload(recordingId: String, notebookId: String) async throws -> UploadResult {
        try await run(["recordings", "upload", recordingId, "--notebook", notebookId],
                      as: UploadResult.self, timeout: 90)
    }

    func recent(limit: Int = 5) async throws -> [Recording] {
        try await run(["recordings", "list", "--limit", String(limit)], as: [Recording].self, timeout: 20)
    }
}
