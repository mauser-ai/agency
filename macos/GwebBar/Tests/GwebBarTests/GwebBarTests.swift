import XCTest
@testable import GwebBar

final class GwebBarTests: XCTestCase {
    func testShellQuoteSurvivesQuotesAndSpaces() {
        XCTAssertEqual(RemoteGweb.shellQuote("Cliente l'Aquila; rm -rf"), "'Cliente l'\\''Aquila; rm -rf'")
        let r = RemoteGweb(host: "raspberry", gwebPath: "~/.local/bin/gweb")
        XCTAssertEqual(r.remoteCommand(["record", "start", "--title", "A B"]),
                       "~/.local/bin/gweb 'record' 'start' '--title' 'A B' --json")
    }

    func testShellQuoteRoundTripsThroughSh() throws {
        let title = "Briefing \"Rossi\" l'ultimo $HOME `id` ; echo x"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", "printf %s " + RemoteGweb.shellQuote(title)]
        let pipe = Pipe()
        p.standardOutput = pipe
        try p.run()
        p.waitUntilExit()
        XCTAssertEqual(String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self), title)
    }

    // Output reale di `gweb record status --json` sul Raspberry (campi ridotti).
    func testDecodeIdleStatus() throws {
        let json = """
        {"active": false, "daemon": {"alive": true, "pid": 3483, "heartbeat_age_seconds": 0.5},
         "last": {"id": "r261102-191259-4de5", "title": "Prova microfono", "kind": "mic",
                  "state": "ready", "created_at": "2026-11-02T18:12:59Z", "notebook_id": null,
                  "duration_seconds": 20.88, "upload_status": "not_uploaded", "upload_on_ready": 0,
                  "file_size": 258827}}
        """
        let st = try JSONDecoder().decode(RecordStatus.self, from: Data(json.utf8))
        XCTAssertFalse(st.active)
        XCTAssertEqual(st.daemon?.alive, true)
        XCTAssertEqual(st.last?.statusLabel, "Non caricata")
    }

    func testDecodeActiveStatusAndNotebooks() throws {
        let status = """
        {"active": true, "daemon": {"alive": true}, "elapsed_seconds": 75, "recorded_seconds": 74,
         "recording": {"id": "r1", "title": "Briefing", "kind": "mic", "state": "recording"},
         "segments": 1, "events": []}
        """
        let st = try JSONDecoder().decode(RecordStatus.self, from: Data(status.utf8))
        XCTAssertTrue(st.active)
        XCTAssertEqual(st.recording?.isActive, true)
        XCTAssertEqual(st.elapsedSeconds, 75)

        let nbs = """
        [{"id": "a6ce", "title": "Cliente Rossi", "sources_count": 1, "modified_at": "2026-06-07T19:19:00+00:00", "default": false},
         {"id": "9e16", "title": "(senza titolo)", "sources_count": 0, "modified_at": null, "default": true}]
        """
        let list = try JSONDecoder().decode([Notebook].self, from: Data(nbs.utf8))
        XCTAssertEqual(list.map(\.title), ["Cliente Rossi", "(senza titolo)"])
    }

    func testDecodeErrorBodyAndUploadResult() throws {
        let err = """
        {"ok": false, "error": {"code": "NOT_READY", "message": "La registrazione r1 non è pronta", "hint": "Attendi", "details": {}}}
        """
        let body = try JSONDecoder().decode(RemoteErrorBody.self, from: Data(err.utf8))
        XCTAssertEqual(body.error.message, "La registrazione r1 non è pronta")
        let up = try JSONDecoder().decode(UploadResult.self,
                                          from: Data(#"{"job": null, "scheduled": true, "warnings": []}"#.utf8))
        XCTAssertEqual(up.scheduled, true)
    }

    func testDurationFormat() {
        XCTAssertEqual(Format.duration(65), "1:05")
        XCTAssertEqual(Format.duration(3725), "1:02:05")
        XCTAssertEqual(Format.duration(nil), "0:00")
    }

    // Output reale di `gweb notebook status --offline --json` sul Raspberry (ridotto).
    func testDecodeSessionSummary() throws {
        let json = """
        {"backend": "notebooklm-py", "local": {"exists": true}, "remote": null,
         "keepalive": {"at": "2026-11-02T18:51:46.420715Z", "ok": true, "error": null},
         "default_notebook": null,
         "session": {"state": "ok", "account": "sacchetto.riccardo@gmail.com",
                     "checked_at": "2026-11-02T18:51:46.420715Z", "error": null, "blocked_jobs": 0}}
        """
        let s = try JSONDecoder().decode(NotebookStatus.self, from: Data(json.utf8)).session
        XCTAssertEqual(s.account, "sacchetto.riccardo@gmail.com")
        XCTAssertFalse(s.needsLogin)
        XCTAssertNotNil(Format.localTime(s.checkedAt!))
        for (state, blocked, needs) in [("expired", 0, true), ("missing", 0, true), ("error", 0, false),
                                        ("unchecked", 0, false), ("ok", 2, true)] {
            let x = try JSONDecoder().decode(SessionSummary.self, from: Data(
                #"{"state": "\#(state)", "account": null, "checked_at": null, "error": null, "blocked_jobs": \#(blocked)}"#.utf8))
            XCTAssertEqual(x.needsLogin, needs, state)
        }
    }

    func testDecodeImportResultEvenWhenRejected() throws {
        let ok = #"{"storage_state": {"saved_to": "/x", "mode": "0600", "cookies": 40}, "status": {"ok": true, "notebooks": 15, "account": "a@b.c", "account_changed": true}}"#
        let r = try JSONDecoder().decode(ImportResult.self, from: Data(ok.utf8)).status!
        XCTAssertTrue(r.ok && r.accountChanged == true)
        let ko = #"{"storage_state": {"saved_to": "/x", "mode": "0600"}, "status": {"ok": false, "code": "AUTH_REQUIRED", "message": "Sessione non valida", "hint": null}}"#
        XCTAssertFalse(try JSONDecoder().decode(ImportResult.self, from: Data(ko.utf8)).status!.ok)
    }

    @MainActor
    func testSessionNotificationOnlyOnTransition() throws {
        let m = AppModel(autoStart: false)
        let ok = try JSONDecoder().decode(SessionSummary.self, from: Data(#"{"state": "ok", "blocked_jobs": 0}"#.utf8))
        let expired = try JSONDecoder().decode(SessionSummary.self, from: Data(#"{"state": "expired", "blocked_jobs": 0}"#.utf8))
        m.updateSession(ok)
        m.updateSession(expired)
        XCTAssertEqual(m.session?.state, "expired")
        m.updateSession(expired)  // nessun nuovo avviso: lo stato non è cambiato
        XCTAssertTrue(m.session!.needsLogin)
    }

    /// Regressione: il master di ssh (ControlPersist) ereditava le pipe e l'app restava su
    /// "Connessione…". Qui un figlio in background tiene aperto stdout per 30 s.
    func testExecReturnsWhenProcessExitsEvenIfPipeStaysOpen() async throws {
        let t0 = Date()
        let (code, out, _) = try await RemoteGweb.exec("/bin/sh", ["-c", "sleep 30 & echo ciao"], timeout: 10)
        XCTAssertEqual(code, 0)
        XCTAssertEqual(String(decoding: out, as: UTF8.self), "ciao\n")
        XCTAssertLessThan(Date().timeIntervalSince(t0), 3)
    }

    func testExecTimeoutTerminates() async throws {
        let t0 = Date()
        let (code, _, _) = try await RemoteGweb.exec("/bin/sleep", ["20"], timeout: 1)
        XCTAssertNotEqual(code, 0)
        XCTAssertLessThan(Date().timeIntervalSince(t0), 4)
    }

    func testExecPassesStdinAndLargeOutput() async throws {
        let payload = Data(String(repeating: "x", count: 300_000).utf8)
        let (code, out, _) = try await RemoteGweb.exec("/bin/cat", [], stdin: payload, timeout: 10)
        XCTAssertEqual(code, 0)
        XCTAssertEqual(out.count, payload.count)
    }
}
