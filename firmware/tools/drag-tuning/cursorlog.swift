import CoreGraphics
import Foundation

// Permission-free cursor-position trace. Reads CGEvent(source: nil).location,
// which does NOT need Input Monitoring, but DOES need WindowServer IPC, so it
// must run unsandboxed (a sandboxed run logs all-zero positions).
//
//   swift cursorlog.swift <durationSeconds> [outPath]
//
// Output CSV: epoch_ms,x,y at ~5 ms cadence (global display points, top-left
// origin). epoch_ms is wall-clock so merge.py can align it with the sampler.

let durationSec = CommandLine.arguments.count > 1 ? (Double(CommandLine.arguments[1]) ?? 90.0) : 90.0
let outPath = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "/tmp/sh-drag/host_trace.csv"
let sampleUs: UInt32 = 5000  // 5 ms

let start = Date()
var rows: [String] = ["epoch_ms,x,y"]
rows.reserveCapacity(Int(durationSec * 1000.0 / 5.0) + 16)

while Date().timeIntervalSince(start) < durationSec {
    if let loc = CGEvent(source: nil)?.location {
        let epochMs = Date().timeIntervalSince1970 * 1000.0
        rows.append(String(format: "%.1f,%.2f,%.2f", epochMs, loc.x, loc.y))
    }
    usleep(sampleUs)
}

let csv = rows.joined(separator: "\n") + "\n"
do {
    try csv.write(toFile: outPath, atomically: true, encoding: .utf8)
    print("trace done: \(rows.count - 1) samples -> \(outPath)")
} catch {
    print("write failed: \(error)")
}
