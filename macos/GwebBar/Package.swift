// swift-tools-version: 5.10
// App per la barra dei menu di macOS che comanda `gweb` sul Raspberry via SSH.
import PackageDescription

let package = Package(
    name: "GwebBar",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "GwebBar", path: "Sources/GwebBar"),
        .testTarget(name: "GwebBarTests", dependencies: ["GwebBar"], path: "Tests/GwebBarTests"),
    ]
)
