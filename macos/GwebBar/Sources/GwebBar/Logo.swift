import AppKit

/// Logo GWEB "{G}" ridisegnato in vettoriale dal PNG originale (misure su tela 512 px):
/// corpo verde arrotondato con le punte a graffa ai lati e la G ritagliata (trasparente).
/// Usato per l'icona dell'app (scripts/MakeIcon.swift) e per quella della barra dei menu.
enum Logo {
    static let green = NSColor(srgbRed: 0, green: 99 / 255, blue: 76 / 255, alpha: 1)  // #00634C
    /// Il verde del marchio su fondo chiaro, una tinta più luminosa su fondo scuro (leggibilità).
    static let adaptiveGreen = NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? NSColor(srgbRed: 0.24, green: 0.66, blue: 0.54, alpha: 1) : green
    }

    /// Ingombro del logo nella tela originale (graffe comprese).
    static let bounds = CGRect(x: 33, y: 64, width: 447, height: 383)

    /// Forma con la G come foro: va riempita con la regola even-odd.
    static func path(fitting rect: CGRect) -> CGPath {
        let scale = min(rect.width / bounds.width, rect.height / bounds.height)
        let w = bounds.width * scale, h = bounds.height * scale
        // Coordinate del disegno con y verso il basso: le capovolgiamo per Core Graphics.
        let t = CGAffineTransform(translationX: rect.midX - w / 2, y: rect.midY + h / 2)
            .scaledBy(x: scale, y: -scale)
            .translatedBy(x: -bounds.minX, y: -bounds.minY)
        let p = CGMutablePath()
        p.addPath(body(), transform: t)
        p.addPath(letterG(), transform: t)
        return p
    }

    private static func body() -> CGPath {
        let (l, r, top, bottom, rad) = (53.0, 461.0, 64.0, 447.0, 38.0)
        let p = CGMutablePath()
        p.move(to: CGPoint(x: l + rad, y: top))
        p.addLine(to: CGPoint(x: r - rad, y: top))
        p.addArc(tangent1End: CGPoint(x: r, y: top), tangent2End: CGPoint(x: r, y: top + rad), radius: rad)
        brace(p, x: r, outward: 19)
        p.addArc(tangent1End: CGPoint(x: r, y: bottom), tangent2End: CGPoint(x: r - rad, y: bottom), radius: rad)
        p.addLine(to: CGPoint(x: l + rad, y: bottom))
        p.addArc(tangent1End: CGPoint(x: l, y: bottom), tangent2End: CGPoint(x: l, y: bottom - rad), radius: rad)
        brace(p, x: l, outward: -20, upward: true)
        p.addArc(tangent1End: CGPoint(x: l, y: top), tangent2End: CGPoint(x: l + rad, y: top), radius: rad)
        p.closeSubpath()
        return p
    }

    /// Punta a graffa sul lato: spalla concava, tratto dritto di 22, spalla concava.
    private static func brace(_ p: CGMutablePath, x: CGFloat, outward d: CGFloat, upward: Bool = false) {
        let ys: [CGFloat] = upward ? [292, 268, 246, 222] : [222, 246, 268, 292]
        p.addLine(to: CGPoint(x: x, y: ys[0]))
        p.addQuadCurve(to: CGPoint(x: x + d, y: ys[1]), control: CGPoint(x: x, y: ys[1]))
        p.addLine(to: CGPoint(x: x + d, y: ys[2]))
        p.addQuadCurve(to: CGPoint(x: x, y: ys[3]), control: CGPoint(x: x, y: ys[2]))
    }

    /// La G: barre di spessore 44, braccio interno, apertura in alto a destra.
    private static func letterG() -> CGPath {
        let rad: CGFloat = 13
        let pts: [(CGFloat, CGFloat, Bool)] = [   // (x, y, angolo arrotondato)
            (121, 142, true), (392, 142, true), (392, 187, false), (166, 187, false),
            (166, 323, false), (347, 323, false), (347, 278, false), (280, 278, false),
            (280, 234, false), (392, 234, false), (392, 367, true), (121, 367, true),
        ]
        let p = CGMutablePath()
        let n = pts.count
        let start = CGPoint(x: (pts[0].0 + pts[1].0) / 2, y: pts[0].1)
        p.move(to: start)
        for i in 1...n {
            let cur = pts[i % n], next = pts[(i + 1) % n]
            let c = CGPoint(x: cur.0, y: cur.1)
            if cur.2 {
                p.addArc(tangent1End: c, tangent2End: CGPoint(x: next.0, y: next.1), radius: rad)
            } else {
                p.addLine(to: c)
            }
        }
        p.closeSubpath()
        return p
    }

    /// Icona per la barra dei menu: sagoma nera (template, macOS la colora per barra
    /// chiara/scura) con la G ritagliata. `dot` aggiunge un pallino (registrazione in corso).
    static func menuBarImage(height: CGFloat = 16, dot: Bool = false) -> NSImage {
        let w = (height * bounds.width / bounds.height).rounded(.up) + (dot ? 6 : 0)
        let image = NSImage(size: NSSize(width: w, height: height), flipped: false) { _ in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            let logoW = height * bounds.width / bounds.height
            ctx.addPath(path(fitting: CGRect(x: 0, y: 0, width: logoW, height: height)))
            ctx.setFillColor(NSColor.black.cgColor)
            ctx.fillPath(using: .evenOdd)
            if dot {
                ctx.fillEllipse(in: CGRect(x: w - 5, y: height - 5, width: 5, height: 5))
            }
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = "Briefing"
        return image
    }
}
