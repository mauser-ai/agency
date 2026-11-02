// Genera AppIcon.iconset (tutte le dimensioni macOS) dal logo vettoriale di Logo.swift.
// Uso (da build-app.sh): swiftc -parse-as-library Sources/GwebBar/Logo.swift scripts/MakeIcon.swift -o mkicon && ./mkicon DIR
//
// Griglia delle icone macOS: tela 1024, forma arrotondata 824×824 centrata (margine 100) con
// angoli "continui" (raggio ≈ 22,5% del lato) e ombra morbida verso il basso. Il logo, che ha
// le graffe sporgenti, sta su un fondo chiaro dentro la forma: così resta riconoscibile nel
// Dock accanto alle icone di sistema.
import AppKit

func render(size px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let ctx = NSGraphicsContext.current!.cgContext
    let k = CGFloat(px) / 1024
    ctx.scaleBy(x: k, y: k)

    let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
    let shape = CGPath(roundedRect: tile, cornerWidth: 185, cornerHeight: 185, transform: nil)

    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 28,
                  color: NSColor.black.withAlphaComponent(0.30).cgColor)
    ctx.addPath(shape)
    ctx.setFillColor(NSColor.white.cgColor)
    ctx.fillPath()
    ctx.restoreGState()

    ctx.saveGState()
    ctx.addPath(shape)
    ctx.clip()
    let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: [
        NSColor(srgbRed: 1, green: 1, blue: 1, alpha: 1).cgColor,
        NSColor(srgbRed: 0.925, green: 0.953, blue: 0.941, alpha: 1).cgColor,
    ] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(gradient, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: [])
    ctx.restoreGState()

    // Bordo interno sottilissimo, come le icone di sistema.
    ctx.addPath(CGPath(roundedRect: tile.insetBy(dx: 0.75, dy: 0.75), cornerWidth: 184, cornerHeight: 184, transform: nil))
    ctx.setStrokeColor(NSColor.black.withAlphaComponent(0.06).cgColor)
    ctx.setLineWidth(1.5)
    ctx.strokePath()

    ctx.addPath(Logo.path(fitting: tile.insetBy(dx: 110, dy: 110)))
    ctx.setFillColor(Logo.green.cgColor)
    ctx.fillPath(using: .evenOdd)

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

@main
enum MakeIcon {
    static func main() throws {
        let dir = URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? "AppIcon.iconset")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        for base in [16, 32, 128, 256, 512] {
            try render(size: base).write(to: dir.appendingPathComponent("icon_\(base)x\(base).png"))
            try render(size: base * 2).write(to: dir.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
        }
        print("iconset in \(dir.path)")
    }
}
