#!/usr/bin/env python3
"""Generate HuaQiu Revision Zero (Rev0) upload files for a Screen Hopper variant.

Rev0's web uploader runs a strict cross-check: every designator in the centroid
(pick-and-place) file must have a matching row in the BOM, and vice versa. Two
things trip it up with raw KiCad output:

  - The BOM must group/list designators that exactly cover the centroid set.
  - The centroid must use Rev0's own column header
    "Designator,Mid X,Mid Y,Layer,Rotation" with mm-suffixed coordinates and
    capitalised Top/Bottom - not KiCad's "Ref,Val,Package,PosX,PosY,Rot,Side".

Both files list ALL footprints on the board (matching the gerbers); parts we
don't populate are flagged "DNP" in the BOM rather than dropped, so the
designator sets stay consistent with the board.

The BOM is emitted as CSV with a header row then one data row per designator -
NO guidance/instruction row. Rev0's CSV parser treats the first row after the
header as data, so a template "Required,Required,..." row gets read as a phantom
component and breaks the upload. (Their .xls template carries that row and their
.xls parser skips it; the CSV path does not.)

MPN/manufacturer come from gen_sch.PART_MPN (single source of truth, also
embedded in the schematic). Only the fields PART_MPN doesn't carry - package,
description, DNP flag, and C1's concrete MPN - live in REV0_EXTRA below.

Usage:
    python3 gen_rev0_fab.py [variant] [pos_csv] [out_dir]
Defaults: variant=compact, pos_csv=<repo>/hardware/<variant>/screen-hopper-<variant>-all-pos.csv,
out_dir alongside pos_csv. Pure stdlib - no third-party deps.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_sch import PART_MPN  # noqa: E402

# Per-designator fields the schematic MPN table doesn't carry. mpn/manufacturer
# fall back to PART_MPN[variant]; set mpn here only to fill a gap (C1) or override.
# (package, description, proc_type, note, mpn_override, mfr_override)
REV0_EXTRA = {
    "compact": {
        "A1": ("RaspberryPi_Pico_SMD", "Raspberry Pi Pico 2 (RP2350) - USB device to computer 1", "",
               "Raspberry Pi Pico 2 MODULE (SC1631), NOT a bare RP2350 chip. May not be in the "
               "HQ/Rev0 catalogue and cannot be consigned - if unmatched it is left unpopulated."),
        "A2": ("RaspberryPi_Pico_SMD", "Raspberry Pi Pico 2 (RP2350) - USB host for mouse/keyboard", "",
               "As A1. Mounts on the BACK of the PCB, directly under A1."),
        "A3": ("RaspberryPi_Pico_SMD", "Raspberry Pi Pico 2 (RP2350) - forwarder to computer 2", "",
               "As A1. Isolated domain 2."),
        "U1": ("SOIC-8", "High-speed 10 Mbit optocoupler, SO-8 (galvanic isolation)", "",
               "True SO-8 part. The 6N137 is NOT available in SO-8 (DIP-8 or wide gull-wing "
               "only) - do NOT substitute it. HCPL-0611 is a pin-compatible higher-CMR "
               "alternative in this same SO-8 footprint."),
        "U2": ("SOT-23-6", "USB 2.0 ESD/TVS diode array (host port D+/D- protection)", "", ""),
        "R1": ("0805", "Resistor 470R 5% (opto LED current limit)", "", ""),
        "R2": ("0805", "Resistor 680R 1% (opto VO pull-up)", "", "Any 680R 0805 acceptable."),
        "R3": ("0805", "Resistor 56k 1% (USB-C CC1 pull-up Rp)", "",
               "Value non-critical (+/-20% per USB spec); any 56k 0805 OK."),
        "R4": ("0805", "Resistor 56k 1% (USB-C CC2 pull-up Rp)", "",
               "Value non-critical (+/-20% per USB spec); any 56k 0805 OK."),
        "C1": ("0805", "Capacitor 100nF X7R 50V (opto VCC decoupling)", "",
               "Non-critical decoupling; any 100nF 0805 X7R OK.",
               "CC0805KRX7R9BB104", "Yageo"),  # PART_MPN leaves C1 open; pin one in-stock MLCC here
        "J2": ("USB-C 2.0 16-pin SMD", "USB Type-C receptacle (mouse/keyboard host input)", "",
               "POPULATE this connector. Shares data lines with J1 - only one is fitted."),
        "J1": ("USB-A horizontal edge-mount", "USB Type-A receptacle (alternative host input)", "DNP",
               "DO NOT POPULATE. Footprint exists on the board but J2 (USB-C) is the fitted connector."),
    },
    # Two-row plug-in variant: the three Picos and the J3 USB tap are THT 0.1"
    # headers the user fits by hand (Picos plug in on pin-headers), so they are DNP
    # for fab assembly. There is NO on-board host connector - the device plugs into
    # Pico B's own micro-USB - so the only fab-populated parts are the opto and its
    # three passives.
    "header": {
        "A1": ("RaspberryPi_Pico_Common_THT", "Raspberry Pi Pico 2 (RP2350) - USB device to computer 1", "DNP",
               "DO NOT POPULATE. User plugs a Pico 2 module into a 0.1\" pin-header here. Top row, front."),
        "A2": ("RaspberryPi_Pico_Common_THT", "Raspberry Pi Pico 2 (RP2350) - USB host for mouse/keyboard", "DNP",
               "DO NOT POPULATE. User-fitted Pico 2 on the BACK of the board, hole grid offset 8.89mm from A1. "
               "The mouse/keyboard plugs into this Pico's own micro-USB (OTG adapter)."),
        "A3": ("RaspberryPi_Pico_Common_THT", "Raspberry Pi Pico 2 (RP2350) - forwarder to computer 2", "DNP",
               "DO NOT POPULATE. User-fitted Pico 2, bottom row. Isolated domain 2."),
        "U1": ("SOIC-8", "High-speed 10 Mbit optocoupler, SO-8 (galvanic isolation)", "",
               "True SO-8 part. The 6N137 is NOT available in SO-8 (DIP-8 or wide gull-wing "
               "only) - do NOT substitute it. HCPL-0611 is a pin-compatible higher-CMR "
               "alternative in this same SO-8 footprint."),
        "R1": ("0805", "Resistor 470R 1% (opto LED current limit)", "",
               "Walsin WR08X4700FTL. Any 470R 0805 acceptable if the matcher prefers another."),
        "R2": ("0805", "Resistor 680R 1% (opto VO pull-up)", "",
               "Walsin WR08X6800FTL. Any 680R 0805 acceptable if the matcher prefers another."),
        "C1": ("0805", "Capacitor 100nF X7R 50V (opto VCC decoupling)", "",
               "Non-critical decoupling; any 100nF 0805 X7R OK.",
               "0805B104K500CT", "Walsin"),
    },
}

# NextPCB "HQ Part #" - HuaQiu's internal catalogue ID. The Rev0 auto-matcher is
# unreliable at linking an MPN to its HQ entry even when the MPN+manufacturer are
# exact and the part is in stock (U1 proved this). Pinning the HQ# is the reliable
# key: in BOM Review, click Search on the row and select this part. Appended to the
# Customer Note so it travels with the BOM. Fill these in as you confirm them via
# the HQ store / Search. ref -> HQ part number.
HQ_PARTNO = {
    "header": {
        "R1": "RE0195837",   # WR08X4700FTL Walsin 470R, ship immediately
        "U1": "IS0009935",   # HCPL-0601-500E Broadcom, ship immediately
    },
}

HEADERS = ["Designator*", "Quantity*", "Manufacturer Part Number*", "Manufacturer",
           "Package/Footprint", "Description", "Procurement Type", "Customer Note"]


def read_centroid(pos_csv):
    """KiCad all-pos.csv -> list of (ref, x_mm, y_mm, layer, rotation)."""
    with open(pos_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        layer = "Top" if r["Side"].lower() == "top" else "Bottom"
        rot = round(float(r["Rot"])) % 360
        out.append((r["Ref"], float(r["PosX"]), float(r["PosY"]), layer, rot))
    return out


def write_centroid(refs, dst):
    with open(dst, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")  # LF, matching Rev0's sample centroid
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for ref, x, y, layer, rot in refs:
            w.writerow([ref, f"{x:.4f}mm", f"{y:.4f}mm", layer, rot])


def bom_rows(refs, variant):
    """One row per designator (qty 1) so the centroid <-> BOM designator sets
    match 1:1. Returns list of [ref, qty, mpn, mfr, pkg, desc, proc, note]."""
    mpn_map = PART_MPN.get(variant, {})
    extra = REV0_EXTRA.get(variant, {})
    hq_map = HQ_PARTNO.get(variant, {})
    out = []
    for row in refs:
        ref = row[0]
        e = extra.get(ref)
        if e is None:
            raise SystemExit(f"{ref} placed on board but missing from REV0_EXTRA[{variant!r}]")
        pkg, desc, proc, note = e[0], e[1], e[2], e[3]
        mpn, mfr = mpn_map.get(ref, ("", ""))
        if len(e) > 4 and e[4]:
            mpn = e[4]
        if len(e) > 5 and e[5]:
            mfr = e[5]
        if not mpn:
            raise SystemExit(f"{ref} has no MPN (not in PART_MPN[{variant!r}] and no override)")
        hq = hq_map.get(ref)
        if hq:
            note = (f"HQ# {hq}. " + note).strip()
        out.append([ref, 1, mpn, mfr, pkg, desc, proc, note])
    return out


def write_bom_csv(refs, variant, dst):
    rows = bom_rows(refs, variant)
    with open(dst, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADERS)
        w.writerows(rows)


def main():
    variant = sys.argv[1] if len(sys.argv) > 1 else "compact"
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_pos = os.path.join(repo, variant, f"screen-hopper-{variant}-all-pos.csv")
    pos_csv = sys.argv[2] if len(sys.argv) > 2 else default_pos
    out_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(pos_csv)

    refs = read_centroid(pos_csv)
    bom_csv = os.path.join(out_dir, f"Rev0_BOM-screen-hopper-{variant}.csv")
    pnp_dst = os.path.join(out_dir, f"Rev0_PnP-screen-hopper-{variant}.csv")
    write_centroid(refs, pnp_dst)
    write_bom_csv(refs, variant, bom_csv)
    print(f"wrote {bom_csv}")
    print(f"wrote {pnp_dst}")
    print(f"designators ({len(refs)}): {', '.join(r[0] for r in refs)}")


if __name__ == "__main__":
    main()
