import Foundation

/// Accesso al Raspberry per chi non ha già una chiave SSH configurata (i colleghi).
/// L'app crea una chiave dedicata in ~/.ssh/gwebbar_ed25519; il "codice di accesso" da
/// mandare a chi gestisce il Raspberry è la sua parte pubblica (add-team-key.sh la autorizza
/// solo per i comandi dell'app).
enum TeamAccess {
    static var keyURL: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".ssh/gwebbar_ed25519") }
    static var hasKey: Bool { FileManager.default.fileExists(atPath: keyURL.path) }

    static var publicKey: String? {
        (try? String(contentsOf: keyURL.appendingPathExtension("pub"), encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Opzioni da aggiungere alla riga di ssh.
    static var sshOptions: [String] {
        // accept-new: al primo collegamento registra l'impronta del Raspberry invece di
        // fermarsi (con BatchMode non potrebbe chiedere conferma); se poi cambiasse, rifiuta.
        var opts = ["-o", "StrictHostKeyChecking=accept-new"]
        if hasKey { opts += ["-i", keyURL.path] }
        return opts
    }

    static func createKey() async throws -> String {
        let ssh = keyURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: ssh, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let who = "\(NSUserName())@\(Host.current().localizedName ?? "Mac")".replacingOccurrences(of: " ", with: "-")
        let (code, _, err) = try await RemoteGweb.exec(
            "/usr/bin/ssh-keygen", ["-q", "-t", "ed25519", "-N", "", "-C", "briefing-\(who)", "-f", keyURL.path],
            timeout: 30)
        guard code == 0, let key = publicKey else {
            throw RemoteGweb.Failure(message: "Codice di accesso non creato",
                                     hint: String(decoding: err, as: UTF8.self), exitCode: code)
        }
        return key
    }
}
