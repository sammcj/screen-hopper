// Logs the macOS display arrangement on start and after every reconfiguration.
// Catches the transient layouts macOS applies while the virtual display is
// recreated (wake, replug), which the firmware's fixed screen layout can't
// follow and which show up as the cursor wrapping on a crossing.
//
//   swiftc -O -o display_log display_log.swift
//   nohup ./display_log ~/display-arrangement.log &
//
// Each line: ISO timestamp, reason, then one [id x,y WxH flags] per online display.
import CoreGraphics
import Foundation

let logPath = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : NSString(string: "~/display-arrangement.log").expandingTildeInPath

func snapshot(_ reason: String) {
    var count: UInt32 = 0
    var ids = [CGDirectDisplayID](repeating: 0, count: 16)
    CGGetOnlineDisplayList(16, &ids, &count)
    let main = CGMainDisplayID()
    var line = ISO8601DateFormatter().string(from: Date()) + " " + reason
    for i in 0..<Int(count) {
        let id = ids[i]
        let b = CGDisplayBounds(id)
        var flags = ""
        if id == main { flags += " main" }
        if CGDisplayIsActive(id) == 0 { flags += " inactive" }
        let mirrorOf = CGDisplayMirrorsDisplay(id)
        if mirrorOf != 0 { flags += " mirrors:\(mirrorOf)" }
        line += " [\(id) \(Int(b.origin.x)),\(Int(b.origin.y)) \(Int(b.width))x\(Int(b.height))\(flags)]"
    }
    line += "\n"
    if let h = FileHandle(forWritingAtPath: logPath) {
        h.seekToEndOfFile()
        h.write(line.data(using: .utf8)!)
        h.closeFile()
    } else {
        try? line.write(toFile: logPath, atomically: true, encoding: .utf8)
    }
}

snapshot("start")
CGDisplayRegisterReconfigurationCallback({ _, flags, _ in
    // The callback fires once per display at the start of a change and once at
    // the end. The begin notifications still show the old layout, so skip them.
    if flags.contains(.beginConfigurationFlag) { return }
    snapshot("reconfig")
}, nil)
RunLoop.main.run()
