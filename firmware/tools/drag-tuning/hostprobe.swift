import AppKit
import CoreGraphics

// Host side of bench.py: reports the pointer position, the left button state
// and whether the session is locked, one line per line read on stdin. Needs no
// Input Monitoring permission, but must run unsandboxed (WindowServer access).
//
//   swift hostprobe.swift [--pad]
//
// --pad covers the main display with a window that swallows clicks, so bench.py
// can press and drag there without touching anything else.
// It also counts the mouse events it receives, which is the evidence that macOS
// turned the device's reports into a real drag.
//
// Reply: x y left locked downs drags ups   (global points, top-left origin)

final class Pad: NSView {
    static var downs = 0, drags = 0, ups = 0
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { Pad.downs += 1 }
    override func mouseDragged(with event: NSEvent) { Pad.drags += 1 }
    override func mouseUp(with event: NSEvent) { Pad.ups += 1 }
    override func draw(_ dirtyRect: NSRect) {
        NSColor(white: 0.12, alpha: 0.96).setFill()
        dirtyRect.fill()
        let text = "Screen Hopper bench is driving the pointer" as NSString
        text.draw(at: NSPoint(x: 24, y: 24), withAttributes: [
            .foregroundColor: NSColor(white: 0.85, alpha: 1), .font: NSFont.systemFont(ofSize: 18)])
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

var ready = "ready"
var ids = [CGDirectDisplayID](repeating: 0, count: 16)
var count: UInt32 = 0
CGGetActiveDisplayList(16, &ids, &count)
for i in 0..<Int(count) {
    let b = CGDisplayBounds(ids[i])
    ready += " display \(Int(b.origin.x)) \(Int(b.origin.y)) \(Int(b.width)) \(Int(b.height))"
}

var window: NSWindow?
if CommandLine.arguments.contains("--pad"), let screen = NSScreen.screens.first {
    // The whole display, above the menu bar and Dock, so no press can reach anything else on it.
    let size = screen.frame.size
    let origin = screen.frame.origin
    let w = NSWindow(contentRect: NSRect(origin: origin, size: size), styleMask: .borderless, backing: .buffered, defer: false)
    w.level = .popUpMenu
    w.contentView = Pad()
    w.orderFrontRegardless()
    window = w
    // The primary screen is vertically symmetric about its centre, so the same
    // rectangle holds in CoreGraphics' top-left coordinates.
    ready += " pad \(Int(origin.x)) \(Int(origin.y)) \(Int(size.width)) \(Int(size.height))"
}
print(ready)
fflush(stdout)

Thread {
    while readLine() != nil {
        let loc = CGEvent(source: nil)?.location ?? .zero
        let left = CGEventSource.buttonState(.combinedSessionState, button: .left) ? 1 : 0
        let session = CGSessionCopyCurrentDictionary() as? [String: Any]
        let locked = (session?["CGSSessionScreenIsLocked"] as? Bool ?? false) ? 1 : 0
        print(String(format: "%.2f %.2f %d %d %d %d %d", loc.x, loc.y, left, locked, Pad.downs, Pad.drags, Pad.ups))
        fflush(stdout)
    }
    exit(0)
}.start()
app.run()
