#!/usr/bin/env python3
"""Geometric checks that stand in for the (macOS-crashing) kicad-cli DRC:
 - connectivity (unconnected ratsnest count)
 - copper clearance between different-net items, via SHAPE.Collide()
 - isolation: no copper bridges the domain gap

Usage: check_board.py <board.kicad_pcb> [gap_l] [gap_r] [clearance_mm]
"""
import sys
import pcbnew

DEFAULT_RULE_MM = 0.2     # design-rule clearance (smd/tht); compact passes 0.15
ROUNDING_MARGIN = 0.02    # tolerate KiCad's nm-rounding so a track sitting at
                          # exactly the rule isn't flagged: check at rule - margin


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
    rule_mm = float(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_RULE_MM
    check_mm = max(rule_mm - ROUNDING_MARGIN, 0.0)
    b = pcbnew.LoadBoard(path)
    b.BuildConnectivity()
    rats = b.GetConnectivity().GetUnconnectedCount(True)

    items = copper_items(b)
    clr = mm(check_mm)
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
    print(f"clearance violations : {len(viol)}  (< {check_mm:.3f}mm = rule {rule_mm} "
          f"- {ROUNDING_MARGIN} margin, different nets)")
    for v in viol[:25]:
        print("   ", v)
    print(f"copper in iso-gap    : {len(gap)}")
    ok = rats == 0 and not viol and not gap
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
