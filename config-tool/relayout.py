#!/usr/bin/env python3
"""Recompute the screen geometry in a multi-profile config from a macOS display
arrangement, keeping every other setting (mappings, drag gain, jiggle, etc).

The firmware's screen rectangles must match macOS's arrangement exactly: the
same-output crossing burst only works where macOS actually has the neighbour
display, so a stale layout (laptop scaling or offset changed) makes crossings
in the mismatched band wrap instead of landing. Same maths as generate_config.py.

Usage:
  relayout.py config.json --displays 3840x2160@0,0 2056x1329@-2056,0 --remote-res 2056x1329
"""
import argparse
import json
import sys


def parse_display(s):
    res, _, pos = s.partition("@")
    w, h = (int(v) for v in res.split("x"))
    x, y = (int(v) for v in pos.split(",")) if pos else (0, 0)
    return x, y, w, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--displays", nargs="+", required=True, help="WxH@X,Y per local display, main first")
    ap.add_argument("--remote-res", default="2056x1329")
    ap.add_argument("--profile", type=int, default=0)
    a = ap.parse_args()

    displays = [parse_display(d) for d in a.displays]
    rw, rh = (int(v) for v in a.remote_res.split("x"))

    with open(a.config) as f:
        cfg = json.load(f)
    prof = cfg["profiles"][a.profile] if "profiles" in cfg else cfg
    screens = prof["screens"]
    if len(screens) != len(displays) + 1:
        sys.exit(f"config has {len(screens)} screens, expected {len(displays) + 1} (remote + locals)")

    bbx = min(d[0] for d in displays)
    bby = min(d[1] for d in displays)
    bbw = max(d[0] + d[2] for d in displays) - bbx
    bbh = max(d[1] + d[3] for d in displays) - bby
    cs = 10000000 / max(bbw, bbh)
    ratio = cs / prof["coord_scale"]  # keep pixel-space feel for sensitivity and edge resistance

    # Remote sits below the local bounding box, scaled to its width.
    remote_w = int(bbw * cs)
    screens[0].update(x=0, y=int(bbh * cs), w=remote_w, h=int(remote_w * rh / rw), scale=round(remote_w / rw))
    for scr, d in zip(screens[1:], displays):
        scr.update(
            x=int((d[0] - bbx) * cs), y=int((d[1] - bby) * cs),
            w=int(d[2] * cs), h=int(d[3] * cs),
            scale=int(round(cs)),
            sensitivity=int(round(scr["sensitivity"] * ratio)),
        )
    prof["coord_scale"] = int(cs)
    prof["offscreen_sensitivity"] = int(round(prof["offscreen_sensitivity"] * ratio))
    prof["edge_resistance"] = int(round(prof["edge_resistance"] * ratio))

    with open(a.config, "w") as f:
        json.dump(cfg, f, indent=4)
        f.write("\n")

    print(f"coord_scale {int(cs)}  edge_resistance {prof['edge_resistance'] / cs:.0f}px")
    for s in screens:
        print({k: s[k] for k in ("x", "y", "w", "h", "scale", "sensitivity", "output")})


if __name__ == "__main__":
    main()
