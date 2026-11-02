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

    func ssh(_ args: [String], stdin: Data? = nil, timeout: TimeInterval) async throws -> (Int32, Data, Data) {
        try await Self.exec(
            "/usr/bin/ssh",
            // ControlMaster=no: usa una connessione condivisa se esiste, ma non ne avvia una.
            // Il master in background (ControlPersist) erediterebbe le pipe dell'app e non le
            // chiuderebbe mai: è il blocco su "Connessione…" visto sul Mac dell'utente.
            ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15",
             "-o", "ControlMaster=no"] + TeamAccess.sshOptions + [host, remoteCommand(args)],
            stdin: stdin, timeout: timeout)
    }

    func run<T: Decodable>(_ args: [String], as type: T.Type, timeout: TimeInterval = 60) async throws -> T {
        let (code, out, err) = try await ssh(args, timeout: timeout)
        return try Self.decode(code: code, out: out, err: err, host: host, as: T.self)
    }

    static func decode<T: Decodable>(code: Int32, out: Data, err: Data, host: String, as: T.Type) throws -> T {
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

    /// Esegue un programma e ne raccoglie l'output. Il risultato arriva quando il PROCESSO
    /// termina, non quando le pipe si chiudono: un nipote che le eredita (es. il master di
    /// ssh) non può bloccare l'app. Dopo `timeout` il processo viene terminato.
    static func exec(_ path: String, _ args: [String], stdin: Data? = nil, environment: [String: String]? = nil,
                     timeout: TimeInterval) async throws -> (Int32, Data, Data) {
        try await withCheckedThrowingContinuation { cont in
            let p = Process()
            p.executableURL = URL(fileURLWithPath: path)
            p.arguments = args
            if let environment { p.environment = environment }
            let out = Pipe(), err = Pipe()
            p.standardOutput = out
            p.standardError = err
            let input = stdin.map { _ in Pipe() }
            p.standardInput = input ?? FileHandle.nullDevice
            let outBuf = Buffer(out.fileHandleForReading), errBuf = Buffer(err.fileHandleForReading)
            let once = Once()

            p.terminationHandler = { proc in
                // Lascia arrivare gli ultimi byte già scritti, poi legge quel che resta senza attese.
                DispatchQueue.global().asyncAfter(deadline: .now() + 0.1) {
                    once.run {
                        cont.resume(returning: (proc.terminationStatus, outBuf.finish(), errBuf.finish()))
                    }
                }
            }
            do {
                try p.run()
            } catch {
                once.run {
                    cont.resume(throwing: Failure(message: "\((path as NSString).lastPathComponent) non avviabile",
                                                  hint: error.localizedDescription, exitCode: -1))
                }
                return
            }
            if let input, let stdin {
                // Scrittura in un thread a parte: un file grande riempirebbe la pipe.
                DispatchQueue.global().async {
                    input.fileHandleForWriting.write(stdin)
                    try? input.fileHandleForWriting.close()
                }
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) {
                if p.isRunning { p.terminate() }
            }
        }
    }

    /// Accumula ciò che arriva su una pipe; `finish` legge il resto in modo non bloccante.
    private final class Buffer: @unchecked Sendable {
        private let lock = NSLock()
        private var data = Data()
        private let handle: FileHandle

        init(_ handle: FileHandle) {
            self.handle = handle
            handle.readabilityHandler = { [weak self] h in
                let chunk = h.availableData
                if chunk.isEmpty { h.readabilityHandler = nil } else { self?.append(chunk) }
            }
        }

        private func append(_ d: Data) {
            lock.lock(); data.append(d); lock.unlock()
        }

        func finish() -> Data {
            handle.readabilityHandler = nil
            let fd = handle.fileDescriptor
            _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)
            var tmp = [UInt8](repeating: 0, count: 65536)
            while true {
                let n = read(fd, &tmp, tmp.count)
                if n <= 0 { break }
                append(Data(tmp[0..<n]))
            }
            try? handle.close()
            lock.lock(); defer { lock.unlock() }
            return data
        }
    }

    private final class Once: @unchecked Sendable {
        private let lock = NSLock()
        private var done = false

        func run(_ body: () -> Void) {
            lock.lock()
            let first = !done
            done = true
            lock.unlock()
            if first { body() }
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

    /// `online` interroga Google (sola lettura); offline usa l'ultimo controllo del demone.
    func session(online: Bool) async throws -> SessionSummary {
        do {
            return try await run(["notebook", "status"] + (online ? [] : ["--offline"]),
                                 as: NotebookStatus.self, timeout: online ? 60 : 20).session
        } catch let f as Failure where online && f.needsLogin {
            // Il controllo fallito è stato registrato sul Pi: il riepilogo offline lo riflette.
            return try await session(online: false)
        }
    }

    /// Passa la sessione al Pi su stdin (mai su disco lì se non nel file 0600 finale).
    func importSession(_ storageState: Data) async throws -> ImportResult.Check {
        let (code, out, err) = try await ssh(["notebook", "login", "--import", "-"], stdin: storageState, timeout: 90)
        if code == 0 || code == 5, let res = try? JSONDecoder().decode(ImportResult.self, from: out),
           let status = res.status {
            return status
        }
        return try Self.decode(code: code, out: out, err: err, host: host, as: ImportResult.Check.self)
    }

    func logout() async throws {
        struct Ok: Decodable {}
        _ = try await run(["notebook", "logout"], as: Ok.self, timeout: 20)
    }
}
