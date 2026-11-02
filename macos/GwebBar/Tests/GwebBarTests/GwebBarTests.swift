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
}
