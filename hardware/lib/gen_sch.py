#!/usr/bin/env python3
"""Generate a KiCad schematic (.kicad_sch) for a Screen Hopper variant.

KiCad has no scripting API to author schematics, so this writes the S-expression
file directly: it embeds the real library symbols, places them at angle 0 on a
grid, and expresses connectivity as net labels placed on each pin (a label at a
pin's connection point joins that pin to the net; same-named labels are one net).

The result is verified separately by exporting its netlist and diffing against
the board netlist - run check_sch.py.
"""
import re
import sys
import uuid

SYM_DIR = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"

# The Pico symbol must match the footprint's pad count for tools that cross-check
# the schematic against the board (e.g. Quilter): the SMD footprint exposes 49
# numbered pads (the Extensive symbol), the through-hole header footprint only 40
# (the plain symbol). Pico B always uses the SMD footprint, so always Extensive.
PICO_EXT = ("MCU_Module:RaspberryPi_Pico_Extensive", "MCU_Module", "RaspberryPi_Pico_Extensive")
PICO_BASIC = ("MCU_Module:RaspberryPi_Pico", "MCU_Module", "RaspberryPi_Pico")
# The 16P USB-C symbol exposes 17 numbered pins (A1/A4-A9/A12, B1/B4-B9/B12, SH),
# matching the HRO TYPE-C-31-M-12 footprint's numbered pads for Quilter's cross-check.
USBC = ("Connector:USB_C_Receptacle_USB2.0_16P", "Connector",
        "USB_C_Receptacle_USB2.0_16P", "USB_C_Host")


def components(variant):
    """role -> (lib_id, source lib file, source symbol name for pins/body, value)."""
    # compact uses the SMD Pico footprint for all three boards, so all need the
    # Extensive symbol (49 pads) to match; only tht's header parts use the basic.
    pico = PICO_EXT if variant in ("smd", "compact") else PICO_BASIC
    # Pico B is SMD (Extensive, 49 pads) everywhere except the header variant,
    # where all three Picos are plug-in THT headers (basic, 40 pads).
    pico_b = PICO_BASIC if variant == "header" else PICO_EXT
    # U1 optocoupler. The 6N137 only exists in DIP-8 and a WIDE gull-wing SMD (~10 mm
    # lead span, 2.54 mm pitch) - it is NOT a SOIC-8, so it does not fit the SO-8 land
    # the smd/compact variants use (a fab rejected it on this very board). Those
    # variants therefore use the HCPL-0601, the true SO-8 member of the same Broadcom
    # family with an identical pinout. Only the DIP-socket tht variant uses the 6N137.
    # Both symbols share the HCPL-261A 8-pin body (incl. NC pin 4), so the nets and the
    # schematic/footprint pad cross-check are unchanged.
    opto = (("Isolator:HCPL-0601", "Isolator", "HCPL-261A", "HCPL-0601")
            if variant in ("smd", "compact", "header")
            else ("Isolator:6N137", "Isolator", "HCPL-261A", "6N137"))
    comp = {
        "A1": (*pico, "RP2350_PicoA"),
        "A2": (*pico_b, "RP2350_PicoB"),
        "A3": (*pico, "RP2350_Fwd"),
        "U1": opto,
        "R1": ("Device:R", "Device", "R", "470"),
        "R2": ("Device:R", "Device", "R", "680"),
        "C1": ("Device:C", "Device", "C", "100nF"),
    }
    if variant != "header":            # USB-A host port (the header build drops it)
        comp["J1"] = ("Connector:USB_A", "Connector", "USB_A", "USB_A_Host")
    if variant == "compact":           # USB-C host port + CC pull-ups + ESD
        comp["R3"] = ("Device:R", "Device", "R", "56k")
        comp["R4"] = ("Device:R", "Device", "R", "56k")
        comp["J2"] = USBC
        # USB ESD array. USBLC6-2SC6 extends USBLC6-2P6, so use the base symbol's
        # body/pins (like the 6N137 extends HCPL-261A) and rename to the variant.
        comp["U2"] = ("Power_Protection:USBLC6-2SC6", "Power_Protection",
                      "USBLC6-2P6", "USBLC6-2SC6")
    # The header variant has no on-board host connector at all - the device plugs
    # into Pico B's own micro-USB - so no J1/J2/U2/CC resistors or USB tap.
    return comp

FOOTPRINTS = {
    "smd": {"A1": "Module:RaspberryPi_Pico_SMD", "A2": "Module:RaspberryPi_Pico_SMD",
            "A3": "Module:RaspberryPi_Pico_SMD", "U1": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
            "R1": "Resistor_SMD:R_0805_2012Metric", "R2": "Resistor_SMD:R_0805_2012Metric",
            "C1": "Capacitor_SMD:C_0805_2012Metric", "J1": "Connector_USB:USB_A_Connfly_DS1095"},
    "tht": {"A1": "Module:RaspberryPi_Pico_Common_THT", "A2": "Module:RaspberryPi_Pico_SMD",
            "A3": "Module:RaspberryPi_Pico_Common_THT", "U1": "Package_DIP:DIP-8_W7.62mm_Socket",
            "R1": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
            "R2": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
            "C1": "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm",
            "J1": "Connector_USB:USB_A_Connfly_DS1095"},
    "compact": {"A1": "Module:RaspberryPi_Pico_SMD", "A2": "Module:RaspberryPi_Pico_SMD",
                "A3": "Module:RaspberryPi_Pico_SMD", "U1": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                "R1": "Resistor_SMD:R_0805_2012Metric", "R2": "Resistor_SMD:R_0805_2012Metric",
                "R3": "Resistor_SMD:R_0805_2012Metric", "R4": "Resistor_SMD:R_0805_2012Metric",
                "C1": "Capacitor_SMD:C_0805_2012Metric",
                "J1": "Connector_USB:USB_A_TE_292303-7_Horizontal",
                "J2": "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
                "U2": "Package_TO_SOT_SMD:SOT-23-6"},
    # All three Picos are plug-in 0.1" THT headers; only the opto + its passives are
    # SMD (fab-assembled). No on-board host connector - the device plugs into Pico
    # B's own micro-USB - so no USB-A, USB-C, ESD, CC resistors or USB tap.
    "header": {"A1": "Module:RaspberryPi_Pico_Common_THT",
               "A2": "Module:RaspberryPi_Pico_Common_THT",
               "A3": "Module:RaspberryPi_Pico_Common_THT",
               "U1": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
               "R1": "Resistor_SMD:R_0805_2012Metric", "R2": "Resistor_SMD:R_0805_2012Metric",
               "C1": "Capacitor_SMD:C_0805_2012Metric"},
}

# Part identity for the BOM, embedded as schematic properties so KiCad and
# BOM-<variant>.csv stay in sync. Only the compact (turnkey) variant is pinned to
# specific NextPCB in-stock MPNs; the Picos carry the Pico 2 module SKU (intent;
# NextPCB does not stock the module - see BOM note). ref -> (MPN, manufacturer).
# C1 is deliberately unset: any 100nF 0805 X7R from the fab's basic library.
PART_MPN = {
    "compact": {
        "A1": ("SC1631", "Raspberry Pi"), "A2": ("SC1631", "Raspberry Pi"),
        "A3": ("SC1631", "Raspberry Pi"),
        "U1": ("HCPL-0601", "Broadcom"),  # SO-8 opto; 6N137 has no SO-8 package (HCPL-0611 = higher-CMR drop-in)
        "R1": ("RC0805JR-07470RL", "Yageo"), "R2": ("RC0805FR-07680RL", "Yageo"),
        "R3": ("AC0805FR-0756KL", "Yageo"), "R4": ("AC0805FR-0756KL", "Yageo"),
        "J1": ("292303-7", "TE Connectivity"), "J2": ("TYPE-C-31-M-12", "HRO"),
        "U2": ("USBLC6-2SC6", "STMicroelectronics"),
    },
    # Header variant: opto + 3 passives are the only fab-assembled SMD parts (no
    # host connector - the device plugs into Pico B's own micro-USB). The Picos are
    # user-fitted, so they carry the Pico 2 module SKU. MPNs chosen to match
    # NextPCB's Rev0 "HQ Online" PCBA matcher (the no-review auto-assembly catalogue).
    # IMPORTANT: that web matcher is STRICTER than the eda.cn / smtapi inquiry search
    # used by nextpcb_search.py - a part can self-return with stock in that search yet
    # still be rejected by the Rev0 matcher (proven 2026-06-14: TE CRGH0805F680R had
    # 19.6k inquiry stock but the BOM Review marked it "not matched"). The only solid
    # signal is the brand the matcher actually accepted: a 2026-06-14 upload matched
    # Walsin (C1 0805B104K500CT) and Broadcom (U1), so the two resistors now use Walsin
    # WR08X parts (same brand, HuaQiu basic-part line) rather than Yageo/TE codes.
    "header": {
        "A1": ("SC1631", "Raspberry Pi"), "A2": ("SC1631", "Raspberry Pi"),
        "A3": ("SC1631", "Raspberry Pi"),
        "U1": ("HCPL-0601-500E", "Broadcom"),           # SO-8 opto; HQ# IS0009935, 1.48k stock
        "R1": ("WR08X4700FTL", "Walsin"),               # 470R 1% 0805; HQ# RE0195837, 8.97k stock
        "R2": ("WR08X6800FTL", "Walsin"),               # 680R 1% 0805 (HQ# TBC)
    },
}

# placement centre (mm) on the sheet, all at angle 0. Connectivity comes from the
# net labels (same-named labels join regardless of distance), so these positions
# only need to keep symbols from overlapping. J2/R3/R4 are compact-only.
PLACE = {"A2": (90, 160), "A1": (190, 160), "A3": (290, 160),
         "U1": (185, 70), "R1": (130, 70), "R2": (240, 60), "C1": (290, 70),
         "J1": (90, 250), "J2": (90, 340), "R3": (30, 300), "R4": (30, 340),
         "U2": (190, 320), "J3": (30, 250)}

GND = ["3", "8", "13", "18", "23", "28", "33", "38"]


def nets(variant):
    base = [
        ("SER_A0_B1", [("A1", "1"), ("A2", "2")]),
        ("SER_A1_B0", [("A1", "2"), ("A2", "1")]),
        ("SER_A2_B3", [("A1", "4"), ("A2", "5")]),
        ("SER_A3_B2", [("A1", "5"), ("A2", "4")]),
        ("VBUS1", [("A1", "40"), ("A2", "40")]),
        ("GND1", [("A1", g) for g in GND] + [("A2", g) for g in GND]),
        ("OPTO_3V3", [("A1", "36"), ("R1", "1")]),
        ("OPTO_ANODE", [("R1", "2"), ("U1", "2")]),
        ("OPTO_CATH", [("U1", "3"), ("A1", "26")]),
        ("VBUS2", [("A3", "40"), ("U1", "8"), ("C1", "1")]),
        ("GND2", [("A3", g) for g in GND] + [("U1", "5"), ("C1", "2")]),
        ("FWD_3V3", [("A3", "36"), ("R2", "1")]),
        ("OPTO_VO", [("R2", "2"), ("U1", "6"), ("A3", "12")]),
    ]
    d = dict(base)
    if variant == "header":
        # No on-board host connector: the device plugs into Pico B's own micro-USB,
        # so there are no USB_DM/USB_DP/J1 nets at all - just the core KVM nets.
        return list(d.items())
    # smd/tht/compact: USB-A on Pico B's native USB (TP pads).
    d["VBUS1"] += [("J1", "1")]
    d["GND1"] += [("J1", "4"), ("J1", "SH"), ("A2", "TP1")]
    d["USB_DM"] = [("J1", "2"), ("A2", "TP2")]
    d["USB_DP"] = [("J1", "3"), ("A2", "TP3")]
    if variant != "compact":
        return list(d.items())
    # USB-C host (J2) parallels the other host port; 56k Rp pull-ups (R3/R4)
    # advertise default USB power on CC1/CC2; U2 clamps the data lines.
    d["VBUS1"] += [("J2", "A4"), ("J2", "B4"), ("J2", "A9"), ("J2", "B9"),
                   ("R3", "2"), ("R4", "2"), ("U2", "5")]
    d["GND1"] += [("J2", "A1"), ("J2", "B1"), ("J2", "A12"), ("J2", "B12"),
                  ("J2", "SH"), ("U2", "2")]
    d["USB_DP"] += [("J2", "A6"), ("J2", "B6"), ("U2", "1"), ("U2", "6")]
    d["USB_DM"] += [("J2", "A7"), ("J2", "B7"), ("U2", "3"), ("U2", "4")]
    return list(d.items()) + [
        ("CC1", [("J2", "A5"), ("R3", "1")]),
        ("CC2", [("J2", "B5"), ("R4", "1")]),
    ]


def uid():
    return str(uuid.uuid4())


def sym_block(lib, name):
    txt = open(f"{SYM_DIR}/{lib}.kicad_sym").read()
    i = txt.find(f'(symbol "{name}"')
    depth, j = 0, i
    while j < len(txt):
        if txt[j] == "(":
            depth += 1
        elif txt[j] == ")":
            depth -= 1
            if depth == 0:
                return txt[i:j + 1]
        j += 1
    raise RuntimeError(f"symbol {name} not found in {lib}")


def parse_pins(block):
    """number -> (x, y, angle) of the pin connection point (the pin's 'at')."""
    pins = {}
    for m in re.finditer(r"\(pin\b", block):
        seg = block[m.start():m.start() + 320]
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+) (\d+)\)", seg)
        num = re.search(r'\(number "([^"]+)"', seg)
        if at and num:
            pins[num.group(1)] = (float(at.group(1)), float(at.group(2)), int(at.group(3)))
    return pins


def relocate_pin(block, number, nx, ny):
    """Move the pin carrying (number "<number>") to (nx, ny). The Extensive Pico
    symbol stacks a hidden GND pin "D2" on top of the visible GND pins; any GND
    label there would sweep D2 onto GND, but the board leaves D2 netless. Moving
    D2 off the stack keeps it unconnected so schematic and board agree."""
    idx = block.find(f'(number "{number}"')
    if idx < 0:
        return block
    pin_start = block.rfind("(pin", 0, idx)
    head = block[pin_start:idx]
    at = re.search(r"\(at [-\d.]+ [-\d.]+ \d+\)", head)
    if at is None:
        return block
    head = head.replace(at.group(0), f"(at {nx} {ny} 90)", 1)
    return block[:pin_start] + head + block[idx:]


def lib_symbol_entry(lib_id, lib, src):
    """Embedded lib_symbols entry: rename the source symbol's top-level name to
    lib_id and (for extends bases) rename the body to match."""
    block = sym_block(lib, src)
    bare = lib_id.split(":")[1]
    if src != bare:                 # opto: HCPL-261A body used for 6N137
        block = block.replace(f'"{src}', f'"{bare}')
    if src == "RaspberryPi_Pico_Extensive":
        block = relocate_pin(block, "D2", 40.64, -35.56)
    # top-level name -> "Lib:Name"
    block = block.replace(f'(symbol "{bare}"', f'(symbol "{lib_id}"', 1)
    return block, parse_pins(block)


def label(net, x, y, angle):
    return (f'\t(label "{net}"\n\t\t(at {x:.2f} {y:.2f} {angle})\n'
            f'\t\t(effects (font (size 1.27 1.27)) (justify left bottom))\n'
            f'\t\t(uuid "{uid()}")\n\t)\n')


def instance(ref, lib_id, value, footprint, x, y, pin_nums, root, mpn="", manufacturer=""):
    out = [f'\t(symbol\n\t\t(lib_id "{lib_id}")\n\t\t(at {x:.2f} {y:.2f} 0)\n'
           '\t\t(unit 1)\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n'
           '\t\t(on_board yes)\n\t\t(dnp no)\n'
           f'\t\t(uuid "{uid()}")\n'
           f'\t\t(property "Reference" "{ref}"\n\t\t\t(at {x:.2f} {y - 25:.2f} 0)\n'
           '\t\t\t(effects (font (size 1.27 1.27)))\n\t\t)\n'
           f'\t\t(property "Value" "{value}"\n\t\t\t(at {x:.2f} {y + 25:.2f} 0)\n'
           '\t\t\t(effects (font (size 1.27 1.27)))\n\t\t)\n'
           f'\t\t(property "Footprint" "{footprint}"\n\t\t\t(at {x:.2f} {y:.2f} 0)\n'
           '\t\t\t(effects (font (size 1.27 1.27)) (hide yes))\n\t\t)\n']
    # BOM part identity (hidden) - keeps the schematic in sync with BOM-<variant>.csv
    for field, val in (("MPN", mpn), ("Manufacturer", manufacturer)):
        if val:
            out.append(
                f'\t\t(property "{field}" "{val}"\n\t\t\t(at {x:.2f} {y:.2f} 0)\n'
                '\t\t\t(effects (font (size 1.27 1.27)) (hide yes))\n\t\t)\n')
    for n in pin_nums:
        out.append(f'\t\t(pin "{n}"\n\t\t\t(uuid "{uid()}")\n\t\t)\n')
    out.append(f'\t\t(instances\n\t\t\t(project "screen-hopper"\n'
               f'\t\t\t\t(path "/{root}"\n\t\t\t\t\t(reference "{ref}")\n'
               '\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n')
    return "".join(out)


def check_opto_package(variant, comp):
    """Fail loudly on the exact mistake this board shipped once: a 6N137 on an SO-8
    land. The 6N137 has no true SO-8 package (DIP-8 or wide gull-wing only), so it
    cannot be assembled on a SOIC-8 footprint - use the HCPL-0601/0611 there. The
    DIP-socket variant is the only one that takes a real 6N137."""
    fp = FOOTPRINTS[variant]["U1"]
    value = comp["U1"][3]
    if "SOIC" in fp or "SO-8" in fp:
        assert value != "6N137", (
            f"{variant}: U1 is SO-8 ({fp}) but part is 6N137 - the 6N137 has no SO-8 "
            "package. Use HCPL-0601 (or HCPL-0611).")
    if "DIP" in fp:
        assert value == "6N137", (
            f"{variant}: U1 is DIP ({fp}) but part is {value} - HCPL-0601 is SO-8 only; "
            "the DIP-socket build takes a 6N137.")


def build(variant, out_path):
    comp = components(variant)
    check_opto_package(variant, comp)
    root = uid()
    lib_entries, pins = {}, {}
    for ref, (lib_id, lib, src, _v) in comp.items():
        if lib_id not in lib_entries:
            entry, pset = lib_symbol_entry(lib_id, lib, src)
            lib_entries[lib_id] = entry
            pins[lib_id] = pset

    parts = ['(kicad_sch\n\t(version 20250114)\n\t(generator "screen-hopper")\n'
             '\t(generator_version "9.0")\n'
             f'\t(uuid "{root}")\n\t(paper "A2")\n\t(lib_symbols\n']
    for e in lib_entries.values():
        parts.append("\t\t" + e + "\n")
    parts.append("\t)\n")

    # symbol instances
    mpn_map = PART_MPN.get(variant, {})
    for ref, (lib_id, _lib, _src, value) in comp.items():
        x, y = PLACE[ref]
        fp = FOOTPRINTS[variant][ref]
        mpn, mfr = mpn_map.get(ref, ("", ""))
        parts.append(instance(ref, lib_id, value, fp, x, y, sorted(pins[lib_id]),
                              root, mpn=mpn, manufacturer=mfr))

    # net labels on each pin connection point (angle-0 transform: x+lx, y-ly)
    for net, members in nets(variant):
        for ref, pad in members:
            lib_id = comp[ref][0]
            lx, ly, pa = pins[lib_id][pad]
            px, py = PLACE[ref]
            parts.append(label(net, px + lx, py - ly, pa))

    parts.append('\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n)\n')
    open(out_path, "w").write("".join(parts))
    print("wrote", out_path)


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
