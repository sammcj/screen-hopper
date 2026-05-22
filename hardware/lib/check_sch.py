#!/usr/bin/env python3
"""Verify a generated schematic's connectivity by exporting its netlist with
kicad-cli and diffing the pin grouping against the expected nets in gen_sch.

Usage: check_sch.py <schematic.kicad_sch>
"""
import os
import re
import subprocess
import sys
import tempfile

import gen_sch

CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"


def export_netlist(sch):
    out = os.path.join(tempfile.gettempdir(), "sch_check.net")
    subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadsexpr",
                    "-o", out, sch], capture_output=True)
    return open(out).read()


def parse_nets(txt):
    """net name -> set of (ref, pin) from a kicadsexpr netlist."""
    nets = {}
    matches = list(re.finditer(r'\(net\b\s+\(code "?\d+"?\)\s*\(name "([^"]*)"\)', txt))
    for i, nm in enumerate(matches):
        start = nm.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(txt)
        body = txt[start:end]
        nodes = set(re.findall(r'\(node\s+\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', body))
        nets[nm.group(1)] = nodes
    return nets


def variant_of(sch):
    """smd | tht | compact, from the filename (or the 2nd CLI arg)."""
    if len(sys.argv) > 2:
        return sys.argv[2]
    for v in ("compact", "smd", "tht"):
        if v in os.path.basename(sch):
            return v
    raise SystemExit(f"cannot infer variant from {sch}; pass it as arg 2")


def main():
    sch = sys.argv[1]
    expected = gen_sch.nets(variant_of(sch))
    actual = parse_nets(export_netlist(sch))
    # map each (ref,pin) to the actual net it lands in
    where = {}
    for name, nodes in actual.items():
        for n in nodes:
            where[n] = name

    ok = True
    for net, members in expected:
        want = set((r, p) for r, p in members)
        landed = {where.get((r, p)) for r, p in want}
        if None in landed:
            missing = [m for m in want if (m) not in where]
            print(f"FAIL {net}: pins not in any net: {missing}")
            ok = False
            continue
        if len(landed) != 1:
            print(f"FAIL {net}: split across actual nets {landed}")
            ok = False
            continue
        actual_net = landed.pop()
        extra = actual[actual_net] - want
        if extra:
            print(f"FAIL {net}: actual net {actual_net} has extra pins {extra}")
            ok = False
    print("schematic netlist:", "PASS" if ok else "FAIL",
          f"({len(expected)} nets checked)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
