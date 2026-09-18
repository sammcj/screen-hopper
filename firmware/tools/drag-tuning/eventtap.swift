import CoreGraphics
import Foundation

// Counts mouse-moved events at the HID event tap (the earliest point in the
// macOS input pipeline) and reports the inter-event gap from the events' own
// timestamps. Tells you how many pointer updates macOS actually ingests per
// second, independent of how often the cursor is redrawn. Needs Input
// Monitoring permission for the terminal app; run unsandboxed.
//
//   swift eventtap.swift [durationSeconds]

let durationSec = CommandLine.arguments.count > 1 ? (Double(CommandLine.arguments[1]) ?? 5.0) : 5.0
var times: [Double] = []
var locChanges = 0
var lastLoc = CGPoint(x: -1, y: -1)
var sameLocRuns: [Int] = []
var run = 0

var tb = mach_timebase_info_data_t()
mach_timebase_info(&tb)
let ticksToMs = Double(tb.numer) / Double(tb.denom) / 1e6

let mask: CGEventMask = (1 << CGEventType.mouseMoved.rawValue) | (1 << CGEventType.leftMouseDragged.rawValue)
guard let tap = CGEvent.tapCreate(
    tap: .cghidEventTap, place: .headInsertEventTap, options: .listenOnly, eventsOfInterest: mask,
    callback: { _, _, event, _ in
        times.append(Double(event.timestamp) * ticksToMs)
        let loc = event.location
        if loc != lastLoc {
            locChanges += 1
            if run > 0 { sameLocRuns.append(run) }
            run = 0
            lastLoc = loc
        } else {
            run += 1
        }
        return Unmanaged.passUnretained(event)
    }, userInfo: nil)
else {
    print("could not create event tap (grant Input Monitoring to the terminal and retry)")
    exit(1)
}
let src = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
CFRunLoopAddSource(CFRunLoopGetCurrent(), src, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)
CFRunLoopRunInMode(.defaultMode, durationSec, false)

guard times.count > 2 else {
    print("no mouse-moved events seen")
    exit(1)
}
var gaps = zip(times.dropFirst(), times).map { $0 - $1 }.filter { $0 < 50 }
gaps.sort()
print(String(format: "events: %d over %.1fs, with a new location: %d", times.count, durationSec, locChanges))
if !sameLocRuns.isEmpty {
    sameLocRuns.sort()
    print("consecutive same-location events per run: median \(sameLocRuns[sameLocRuns.count / 2]), max \(sameLocRuns.last!)")
}
print(String(format: "inter-event gap while moving: median %.2f ms, p10 %.2f ms, p90 %.2f ms (~%.0f Hz)",
             gaps[gaps.count / 2], gaps[gaps.count / 10], gaps[Int(Double(gaps.count) * 0.9)], 1000.0 / gaps[gaps.count / 2]))
