import XCTest
@testable import GwebBar

/// Contro il Raspberry vero (sola lettura): GWEBBAR_LIVE=raspberry swift test --filter LiveTests
final class LiveTests: XCTestCase {
    func testStatusAndSessionOverSSH() async throws {
        guard let host = ProcessInfo.processInfo.environment["GWEBBAR_LIVE"] else {
            throw XCTSkip("GWEBBAR_LIVE non impostata")
        }
        let r = RemoteGweb(host: host, gwebPath: "~/.local/bin/gweb")
        let t0 = Date()
        let st = try await r.status()
        XCTAssertNotNil(st.daemon)
        let s = try await r.session(online: false)
        XCTAssertFalse(s.state.isEmpty)
        let recent = try await r.recent()
        print("LIVE ok in \(Date().timeIntervalSince(t0))s: daemon=\(st.daemon?.alive ?? false) session=\(s.state) recenti=\(recent.count)")
    }

    /// Come all'avvio dell'app: il modello deve collegarsi da solo entro pochi secondi.
    @MainActor
    func testModelPollsOnItsOwn() async throws {
        guard ProcessInfo.processInfo.environment["GWEBBAR_LIVE"] != nil else { throw XCTSkip("GWEBBAR_LIVE non impostata") }
        let m = AppModel(autoStart: true)
        for _ in 0..<40 where m.reachable == nil { try await Task.sleep(nanoseconds: 250_000_000) }
        print("LIVE modello: reachable=\(String(describing: m.reachable)) host=\(m.host) sessione=\(m.session?.state ?? "-")")
        XCTAssertEqual(m.reachable, true)
    }
}
