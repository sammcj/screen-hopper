#!/usr/bin/env python3
"""Single-shot read of the pending post-drag warp while holding still. Use after a
drag-and-hold (don't move the mouse) to see how far the next move will jump.
Spawns openocd once via mdw/mdb (no persistent session). Run UNSANDBOXED.

Addresses come from the environment (symbols.sh); refresh after a rebuild:
    source <(./symbols.sh) && ./snap.py
"""
import subprocess, re, sys, os

ADDR_CURSOR = int(os.environ.get("ADDR_CURSOR", "0x20020370"), 0)  # int64 cursor_x, int64 cursor_y
ADDR_ACTIVE = int(os.environ.get("ADDR_ACTIVE", "0x200235ed"), 0)  # int8 active_screen
ADDR_DEFER  = int(os.environ.get("ADDR_DEFER",  "0x200235f1"), 0)  # uint8 defer_abs_after_drag

# Active-profile output:0 screens + macOS display geometry. Keep in sync with
# config-tool/config-samm-profiles.json and the real layout (see merge.py).
SCREENS = {
    1: dict(ix=3103448, iw=6896551, iy=0,       ih=3879310, ox=0,     ow=3840, oy=0,   oh=2160),  # LG (main)
    2: dict(ix=0,       iw=3103448, iy=1081178, ih=2006106, ox=-1728, ow=1728, oy=602, oh=1117),  # Retina
}

def read_probe():
    out = subprocess.run(
        ["openocd", "-f", "interface/cmsis-dap.cfg", "-f", "target/rp2350.cfg",
         "-c", "init",
         "-c", f"mdw 0x{ADDR_CURSOR:08x} 4",
         "-c", f"mdb 0x{ADDR_ACTIVE:08x} 1",
         "-c", f"mdb 0x{ADDR_DEFER:08x} 1",
         "-c", "exit"],
        capture_output=True, text=True, timeout=20)
    blob = out.stdout + out.stderr
    words = abyte = defer = None
    for ln in blob.splitlines():
        m = re.match(r'0x%08x:\s+([0-9a-fA-F ]+)' % ADDR_CURSOR, ln)
        if m:
            words = [int(w, 16) for w in m.group(1).split()]
        m2 = re.match(r'0x%08x:\s+([0-9a-fA-F]+)' % ADDR_ACTIVE, ln)
        if m2:
            abyte = int(m2.group(1), 16)
        m3 = re.match(r'0x%08x:\s+([0-9a-fA-F]+)' % ADDR_DEFER, ln)
        if m3:
            defer = int(m3.group(1), 16)
    if words is None or len(words) < 4:
        print("PROBE READ FAILED (probe wedged? replug it):\n" + blob, file=sys.stderr); sys.exit(1)
    cx = words[0] | (words[1] << 32)
    cy = words[2] | (words[3] << 32)
    if cx >= (1 << 63): cx -= (1 << 64)
    if cy >= (1 << 63): cy -= (1 << 64)
    asc = abyte if abyte is not None and abyte < 128 else (abyte - 256 if abyte else 0)
    return cx, cy, asc, defer

def read_host():
    out = subprocess.run(
        ["swift", "-e", 'import CoreGraphics; if let l=CGEvent(source:nil)?.location { print("\\(l.x),\\(l.y)") }'],
        capture_output=True, text=True, timeout=15)
    hx, hy = out.stdout.strip().split(",")
    return float(hx), float(hy)

def fw_to_px(cx, cy, asc):
    s = SCREENS.get(asc)
    if not s:
        return None, None
    fx = s["ox"] + (cx - s["ix"]) / s["iw"] * s["ow"]
    fy = s["oy"] + (cy - s["iy"]) / s["ih"] * s["oh"]
    return fx, fy

hx0, hy0 = read_host()
cx, cy, asc, defer = read_probe()
hx1, hy1 = read_host()
fx, fy = fw_to_px(cx, cy, asc)
moved = abs(hx1 - hx0) + abs(hy1 - hy0)
print(f"active_screen={asc}  cursor_x={cx} cursor_y={cy}  defer_abs_after_drag={defer}")
print(f"host before/after probe: ({hx0:.1f},{hy0:.1f}) / ({hx1:.1f},{hy1:.1f})  moved={moved:.1f}px")
if moved > 5:
    print("  ** cursor MOVED during read - hold still and re-run, comparison invalid **")
if defer == 1:
    print("  warp ARMED: the next mouse move will jump the host cursor by the amount below")
elif defer == 0:
    print("  warp NOT armed (defer=0): no pending post-drag warp right now")
if fx is not None and fy is not None:
    print(f"firmware->px : ({fx:.1f}, {fy:.1f})")
    print(f"PENDING WARP (firmware target - host now) : ({fx-hx1:+.1f}, {fy-hy1:+.1f})  px")
else:
    print(f"active_screen {asc} not in SCREENS table")
