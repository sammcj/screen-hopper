#!/usr/bin/env python3
"""Merge the firmware cursor trace (sampler.py) with the host position trace
(cursorlog.swift) by wall-clock and show how the firmware's dead-reckoned cursor
diverges from the host through a drag, in BOTH axes.

  merge.py [fw_trace.csv] [host_trace.csv]

THE metric for drag-gain tuning is the model-independent H/F ratio at the bottom:
  warp_x / warp_y / |warp|  = the pending post-release jump (fw position - host),
                              i.e. exactly what jumps when you next move the mouse.
  H/F  > 1  firmware UNDER-advanced (cursor lags host -> jump backward / rebound)
  H/F  < 1  firmware OVER-advanced  (cursor ahead -> jump forward, in drag dir)
  Tune slope (drag_curve_k) for fast/large drags, base (drag_gain) for slow ones.

The SCREENS table maps internal cursor units -> macOS px and MUST match the
active profile's screen geometry (config-tool/config-samm-profiles.json) and the
real display layout. Update it if you switch profiles or rearrange monitors.
"""
import csv, sys, bisect, math, os

DEFAULT_DIR = os.environ.get("DRAG_DIR") or (os.environ.get("TMPDIR", "/tmp").rstrip("/") + "/sh-drag")
FW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DEFAULT_DIR, "fw_trace.csv")
HOST = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DEFAULT_DIR, "host_trace.csv")

# profile 0 local screens: internal -> macOS px (x and y). active_screen -> geom.
SCREENS = {
    1: dict(ix=3103448, iw=6896551, ox=0,     ow=3840, iy=0,       ih=3879310, oy=0,   oh=2160),  # LG (main)
    2: dict(ix=0,       iw=3103448, ox=-1728, ow=1728, iy=1081178, ih=2006106, oy=602, oh=1117),  # Retina
}
def to_px(cx, cy, asc):
    s = SCREENS.get(asc)
    if not s: return None, None
    px = s["ox"] + (cx - s["ix"]) / s["iw"] * s["ow"]
    py = s["oy"] + (cy - s["iy"]) / s["ih"] * s["oh"]
    return px, py

fw = []
with open(FW) as f:
    for r in csv.DictReader(f):
        fw.append((float(r["epoch"]), int(r["cursor_x"]), int(r["cursor_y"]),
                   int(r["active_screen"]), int(r["defer"])))

host = []
with open(HOST) as f:
    for r in csv.DictReader(f):
        host.append((float(r["epoch_ms"]) / 1000.0, float(r["x"]), float(r["y"])))

if not fw or not host:
    print("empty trace(s)"); sys.exit(1)

host.sort()
ht = [h[0] for h in host]
def host_at(t):
    i = bisect.bisect_left(ht, t)
    if i <= 0: return host[0][1], host[0][2]
    if i >= len(host): return host[-1][1], host[-1][2]
    t0, x0, y0 = host[i-1]
    t1, x1, y1 = host[i]
    if t1 == t0: return x0, y0
    f = (t - t0) / (t1 - t0)
    return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f

t_start = fw[0][0]
rows = []
for (t, cx, cy, asc, defer) in fw:
    fpx, fpy = to_px(cx, cy, asc)
    if fpx is None: continue
    hx, hy = host_at(t)
    rows.append((t - t_start, fpx, fpy, hx, hy, fpx - hx, fpy - hy, asc, defer))

print(f"fw {len(fw)} samples @ ~{len(fw)/(fw[-1][0]-fw[0][0]):.0f}Hz; host {len(host)} samples")
print(f"{'t(s)':>7} {'fwx':>8} {'fwy':>8} {'hostx':>8} {'hosty':>8} {'gapx':>7} {'gapy':>7} {'|gap|':>7} {'scr':>3} {'def':>3}")
n = len(rows); step = max(1, n // 40)
for i in range(0, n, step):
    t, fpx, fpy, hx, hy, gx, gy, asc, defer = rows[i]
    print(f"{t:7.3f} {fpx:8.1f} {fpy:8.1f} {hx:8.1f} {hy:8.1f} {gx:7.1f} {gy:7.1f} {math.hypot(gx,gy):7.1f} {asc:3d} {defer:3d}")

fpx0, fpy0, hx0, hy0 = rows[0][1], rows[0][2], rows[0][3], rows[0][4]
fpxN, fpyN, hxN, hyN = rows[-1][1], rows[-1][2], rows[-1][3], rows[-1][4]
Fx, Fy = fpxN - fpx0, fpyN - fpy0
Hx, Hy = hxN - hx0,  hyN - hy0
gxN, gyN = rows[-1][5], rows[-1][6]
print(f"\nnet x: fw F={Fx:+.1f}  host H={Hx:+.1f}  warp_x={gxN:+.1f}px")
print(f"net y: fw F={Fy:+.1f}  host H={Hy:+.1f}  warp_y={gyN:+.1f}px")
print(f"net |warp| at release = {math.hypot(gxN,gyN):.1f}px")
F = math.hypot(Fx, Fy); H = math.hypot(Hx, Hy)
if F > 1:
    print(f"=> path-length gain ratio H/F = {H/F:.3f}  (>1 under-advanced/rebound, <1 over-advanced/forward)")
