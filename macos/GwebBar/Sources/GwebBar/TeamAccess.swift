import Foundation

/// Accesso al Raspberry senza configurazione per tutto il team: l'app incorpora una chiave
/// SSH condivisa (messa nel pacchetto da build-app.sh, presa da ~/.config/gweb-team/ del Mac
/// di chi lo prepara, mai nel repository). Sul Raspberry quella chiave è autorizzata con
/// `restrict` + `gweb-ssh-guard`: solo i comandi gweb dell'app, nessuna shell.
enum TeamAccess {
    /// ssh rifiuta chiavi private leggibili da altri: la copiamo dal pacchetto con permessi 0600.
    static var installedKey: URL? {
        guard let bundled = Bundle.main.url(forResource: "team_ed25519", withExtension: nil) else { return nil }
        let fm = FileManager.default
        let dir = fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Briefing")
        let target = dir.appendingPathComponent("team_ed25519")
        if !fm.contentsEqual(atPath: bundled.path, andPath: target.path) {
            try? fm.createDirectory(at: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try? fm.removeItem(at: target)
            guard (try? fm.copyItem(at: bundled, to: target)) != nil else { return nil }
        }
        try? fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
        return target
    }

    /// Opzioni da aggiungere alla riga di ssh.
    static var sshOptions: [String] {
        // accept-new: al primo collegamento registra l'impronta del Raspberry invece di
        // fermarsi (con BatchMode non potrebbe chiedere conferma); se poi cambiasse, rifiuta.
        var opts = ["-o", "StrictHostKeyChecking=accept-new"]
        if let key = installedKey { opts += ["-i", key.path] }
        return opts
    }
}
