#!/usr/bin/env python3
"""
Generate Screen Hopper config based on display arrangement.
Auto-detects connected displays on macOS. On other platforms, specify
displays manually with --displays.

Usage:
  Auto-detect (macOS):  python3 generate_config.py
  Manual displays:      python3 generate_config.py --displays 3840x2160 1920x1080@-1920,0
  CLI (non-interactive): python3 generate_config.py --displays 3840x2160 --edge left --remote-res 2056x1329
"""

import argparse
import json
import subprocess
import sys


def get_displays_macos():
    """Detect display arrangement via CoreGraphics (macOS only)."""
    swift_code = """
    import CoreGraphics
    let maxDisplays: UInt32 = 10
    var displayIDs = [CGDirectDisplayID](repeating: 0, count: Int(maxDisplays))
    var count: UInt32 = 0
    CGGetActiveDisplayList(maxDisplays, &displayIDs, &count)
    let mainID = CGMainDisplayID()
    for i in 0..<Int(count) {
        let did = displayIDs[i]
        let b = CGDisplayBounds(did)
        let isMain = did == mainID
        print("\\(did),\\(Int(b.origin.x)),\\(Int(b.origin.y)),\\(Int(b.size.width)),\\(Int(b.size.height)),\\(isMain)")
    }
    """
    result = subprocess.run(
        ["swift", "-e", swift_code], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None

    displays = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) < 6:
            continue
        displays.append({
            "id": int(parts[0]),
            "x": int(parts[1]),
            "y": int(parts[2]),
            "w": int(parts[3]),
            "h": int(parts[4]),
            "main": parts[5] == "true",
        })
    return displays if displays else None


def parse_display_spec(spec):
    """Parse a display spec like '1920x1080' or '1920x1080@-1920,0'.

    Format: WxH[@X,Y] where X,Y defaults to 0,0 for the first display.
    Multiple displays are positioned automatically if @ is omitted.
    """
    if "@" in spec:
        res, pos = spec.split("@", 1)
        w, h = map(int, res.lower().split("x"))
        x, y = map(int, pos.split(","))
    else:
        w, h = map(int, spec.lower().split("x"))
        x, y = None, None
    return {"w": w, "h": h, "x": x, "y": y}


def get_displays(manual_specs=None):
    """Get display list. Auto-detects on macOS, or uses manual specs."""
    if manual_specs:
        displays = []
        next_x = 0
        for i, spec in enumerate(manual_specs):
            d = parse_display_spec(spec)
            if d["x"] is None:
                d["x"] = next_x
                d["y"] = 0
                next_x += d["w"]
            displays.append({
                "id": i,
                "x": d["x"],
                "y": d["y"],
                "w": d["w"],
                "h": d["h"],
                "main": i == 0,
            })
        return displays

    if sys.platform == "darwin":
        displays = get_displays_macos()
        if displays:
            return displays

    print("Could not auto-detect displays (auto-detection requires macOS).", file=sys.stderr)
    print("\nSpecify displays manually with --displays, e.g.:", file=sys.stderr)
    print("  python3 generate_config.py --displays 3840x2160 1920x1080@-1920,0", file=sys.stderr)
    print("\nFormat: WxH[@X,Y] where X,Y is the display position in pixels.", file=sys.stderr)
    print("If @X,Y is omitted, displays are placed left-to-right.", file=sys.stderr)
    sys.exit(1)


def display_name(d):
    label = "(main) " if d["main"] else ""
    return f"{label}{d['w']}x{d['h']} at ({d['x']},{d['y']})"


def bounding_box(displays):
    min_x = min(d["x"] for d in displays)
    min_y = min(d["y"] for d in displays)
    max_x = max(d["x"] + d["w"] for d in displays)
    max_y = max(d["y"] + d["h"] for d in displays)
    return min_x, min_y, max_x, max_y


def find_leftmost(displays):
    return min(displays, key=lambda d: d["x"])


def find_rightmost(displays):
    return max(displays, key=lambda d: d["x"] + d["w"])


def find_topmost(displays):
    return min(displays, key=lambda d: d["y"])


def find_bottommost(displays):
    return max(displays, key=lambda d: d["y"] + d["h"])


def generate_config(displays, remote_w, remote_h, edge, sensitivity, constraint_mode):
    """Generate Screen Hopper JSON config.

    Screen 0 = remote computer (output=1, sent via serial forwarder)
    Screens 1..N = local displays (output=0, sent via USB)

    Each local display gets its own screen so the firmware can use
    absolute positioning within each display and relative movement
    to cross between them.
    """
    bb_min_x, bb_min_y, bb_max_x, bb_max_y = bounding_box(displays)
    bb_w = bb_max_x - bb_min_x
    bb_h = bb_max_y - bb_min_y

    # Scale factor: map pixel dimensions to internal coordinate space.
    # Use a scale that gives reasonable values (max ~10M).
    coord_scale = 10000000 / max(bb_w, bb_h)

    # Remote screen: scale to match the transition edge
    if edge in ("left", "right"):
        remote_h_scaled = int(bb_h * coord_scale)
        remote_w_scaled = int(remote_h_scaled * remote_w / remote_h)
    else:
        remote_w_scaled = int(bb_w * coord_scale)
        remote_h_scaled = int(remote_w_scaled * remote_h / remote_w)

    # Calculate offset for remote screen and local displays
    if edge == "left":
        remote_x = 0
        remote_y = 0
        local_offset_x = remote_w_scaled
        local_offset_y = 0
    elif edge == "right":
        local_offset_x = 0
        local_offset_y = 0
        remote_x = int(bb_w * coord_scale)
        remote_y = 0
    elif edge == "top":
        remote_x = 0
        remote_y = 0
        local_offset_x = 0
        local_offset_y = remote_h_scaled
    else:  # bottom
        local_offset_x = 0
        local_offset_y = 0
        remote_x = 0
        remote_y = int(bb_h * coord_scale)

    NSCREENS = 6  # must match firmware NSCREENS in types.h
    if len(displays) + 1 > NSCREENS:
        print(f"Warning: {len(displays)} displays + 1 remote = {len(displays) + 1} screens, "
              f"but firmware supports max {NSCREENS}. Truncating.", file=sys.stderr)
        displays = displays[:NSCREENS - 1]

    screens = [
        {
            "x": remote_x,
            "y": remote_y,
            "w": remote_w_scaled,
            "h": remote_h_scaled,
            "sensitivity": sensitivity,
            "output": 1,
        }
    ]

    # One screen per local display, positioned in internal coord space
    for d in displays:
        sx = int((d["x"] - bb_min_x) * coord_scale) + local_offset_x
        sy = int((d["y"] - bb_min_y) * coord_scale) + local_offset_y
        sw = int(d["w"] * coord_scale)
        sh = int(d["h"] * coord_scale)
        screens.append({
            "x": sx,
            "y": sy,
            "w": sw,
            "h": sh,
            "sensitivity": sensitivity,
            "output": 0,
        })

    return {
        "version": 4,
        "unmapped_passthrough": True,
        "partial_scroll_timeout": 1000000,
        "interval_override": 0,
        "constraint_mode": constraint_mode,
        "offscreen_sensitivity": sensitivity,
        "screens": screens,
        "mappings": [
            {
                "source_usage": "0x00070039",
                "target_usage": "0xfff20001",
                "layer": 0,
                "sticky": False,
                "scaling": 1000,
            }
        ],
    }


def print_diagnostics(displays, config):
    bb = bounding_box(displays)
    bb_w = bb[2] - bb[0]
    bb_h = bb[3] - bb[1]

    print(f"\nDiagnostics:")
    print(f"  Desktop bounding box: {bb_w}x{bb_h} pixels")
    print(f"  {len(displays)} local display(s) + 1 remote = {len(config['screens'])} screens total")
    print(f"  Boundary crossing: relative movement between local displays")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate Screen Hopper config from display arrangement"
    )
    parser.add_argument(
        "--displays", nargs="+", metavar="WxH[@X,Y]",
        help="Manual display specs (auto-detected on macOS). "
             "e.g. --displays 3840x2160 1920x1080@-1920,0"
    )
    parser.add_argument(
        "--edge", choices=["left", "right", "top", "bottom"],
        help="Where the remote screen is relative to this desktop"
    )
    parser.add_argument(
        "--remote-res",
        help="Remote screen logical resolution, e.g. 2056x1329"
    )
    parser.add_argument(
        "--sensitivity", type=int, default=4000,
        help="Cursor sensitivity (default: 4000)"
    )
    parser.add_argument(
        "--constraint-mode", type=int, default=2, choices=[0, 1, 2],
        help="0=no restrict, 1=bounding box, 2=visible screens (default: 2)"
    )
    parser.add_argument(
        "--output", default="config-generated.json",
        help="Output file (default: config-generated.json)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("Detecting displays...\n")
    displays = get_displays(manual_specs=args.displays)

    if not displays:
        print("No displays found.", file=sys.stderr)
        sys.exit(1)

    print("Your displays (logical/scaled resolution):")
    for i, d in enumerate(displays):
        print(f"  [{i}] {display_name(d)}")

    bb = bounding_box(displays)
    bb_w = bb[2] - bb[0]
    bb_h = bb[3] - bb[1]
    print(f"\nDesktop bounding box: {bb_w}x{bb_h}")

    left = find_leftmost(displays)
    right = find_rightmost(displays)
    print(f"\nLeftmost display:  {display_name(left)}")
    print(f"Rightmost display: {display_name(right)}")

    # Edge selection
    if args.edge:
        edge = args.edge
    else:
        print("\nWhere is the remote computer's screen?")
        print("  [l] Left of leftmost display")
        print("  [r] Right of rightmost display")
        print("  [t] Above topmost display")
        print("  [b] Below bottommost display")
        edge_choice = input("\nEdge [l/r/t/b] (default: l): ").strip().lower() or "l"
        edge_map = {"l": "left", "r": "right", "t": "top", "b": "bottom"}
        edge = edge_map.get(edge_choice, "left")

    interactive = args.edge is None

    # Remote resolution
    if args.remote_res:
        remote_w, remote_h = map(int, args.remote_res.lower().split("x"))
    elif interactive:
        print("\nRemote computer's screen resolution (logical/scaled, not native)?")
        remote_res = input("WxH (default: 2056x1329 for 16\" MBP): ").strip()
        if not remote_res:
            remote_w, remote_h = 2056, 1329
        else:
            remote_w, remote_h = map(int, remote_res.lower().split("x"))
    else:
        remote_w, remote_h = 2056, 1329

    # Sensitivity
    if interactive:
        sens_input = input(f"\nSensitivity (default: {args.sensitivity}): ").strip()
        sensitivity = int(sens_input) if sens_input else args.sensitivity
    else:
        sensitivity = args.sensitivity

    # Constraint mode
    if interactive:
        print("\nRestrict cursor?")
        print("  [0] No restriction")
        print("  [1] To bounding box")
        print("  [2] To visible screens")
        constraint_input = input(f"Mode [0/1/2] (default: {args.constraint_mode}): ").strip()
        constraint_mode = int(constraint_input) if constraint_input else args.constraint_mode
    else:
        constraint_mode = args.constraint_mode

    config = generate_config(displays, remote_w, remote_h, edge, sensitivity, constraint_mode)

    output_file = args.output
    with open(output_file, "w") as f:
        json.dump(config, f, indent=4)
        f.write("\n")
    print(f"\nConfig written to {output_file}")

    print("\nGenerated screen layout:")
    for i, s in enumerate(config["screens"]):
        output_label = "serial/forwarder" if s.get("output", 0) == 1 else "USB/local"
        label = "Remote" if i == 0 else f"Display {i}"
        print(f"  Screen {i} ({label}, {output_label}): x={s['x']} y={s['y']} w={s['w']} h={s['h']} sens={s['sensitivity']}")

    print_diagnostics(displays, config)

    print(f"\nTo apply: import {output_file} via the web config tool, or run:")
    print(f"  python set_config.py < {output_file}")


if __name__ == "__main__":
    main()
