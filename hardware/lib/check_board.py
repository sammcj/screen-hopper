#!/usr/bin/env python3
"""Geometric checks that stand in for the (macOS-crashing) kicad-cli DRC:
 - connectivity (unconnected ratsnest count)
 - copper clearance between different-net items, via SHAPE.Collide()
 - isolation: no copper bridges the domain gap

Usage: check_board.py <board.kicad_pcb> [gap_l] [gap_r] [clearance_mm]
"""
import sys
import pcbnew

DEFAULT_CLEAR_MM = 0.18  # flag pairs closer than this (rule 0.2 + rounding margin)


def mm(v):
    return pcbnew.FromMM(v)


def copper_items(b):
    items = []
    for t in b.GetTracks():               # tracks + vias
        items.append(t)
    for fp in b.GetFootprints():
        for p in fp.Pads():
            items.append(p)
    return items


def layers_of(it):
    return [l for l in (pcbnew.F_Cu, pcbnew.B_Cu) if it.IsOnLayer(l)]


def main():
    path = sys.argv[1]
    gap_l = float(sys.argv[2]) if len(sys.argv) > 2 else 65.0
    gap_r = float(sys.argv[3]) if len(sys.argv) > 3 else 69.0
    clear_mm = float(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_CLEAR_MM
    b = pcbnew.LoadBoard(path)
    b.BuildConnectivity()
    rats = b.GetConnectivity().GetUnconnectedCount(True)

    items = copper_items(b)
    clr = mm(clear_mm)
    viol = []
    seen = set()
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        on = [it for it in items if it.IsOnLayer(layer)]
        shapes = [it.GetEffectiveShape(layer) for it in on]
        for i in range(len(on)):
            na = on[i].GetNetCode()
            for j in range(i + 1, len(on)):
                if na == on[j].GetNetCode():
                    continue
                if shapes[i].Collide(shapes[j], clr):
                    key = (on[i].GetNetname(), on[j].GetNetname())
                    if key in seen:
                        continue
                    seen.add(key)
                    p = on[i].GetPosition()
                    viol.append((key[0], key[1],
                                 round(pcbnew.ToMM(p.x), 1), round(pcbnew.ToMM(p.y), 1)))

    # isolation: any copper inside the gap?
    gap = []
    for t in b.GetTracks():
        x0 = pcbnew.ToMM(t.GetStart().x)
        x1 = pcbnew.ToMM(t.GetEnd().x)
        if max(x0, x1) > gap_l and min(x0, x1) < gap_r:
            gap.append(t.GetNetname())

    print(f"unconnected ratsnest : {rats}")
    print(f"clearance violations : {len(viol)}  (< {clear_mm}mm, different nets)")
    for v in viol[:25]:
        print("   ", v)
    print(f"copper in iso-gap    : {len(gap)}")
    ok = rats == 0 and not viol and not gap
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
