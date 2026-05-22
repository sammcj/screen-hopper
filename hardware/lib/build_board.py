#!/usr/bin/env python3
"""Generate the Screen Hopper triple-Pico carrier PCB with pcbnew.

Three variants:
  smd      - single-sided, fab-assembled (SOIC opto, 0805 passives, all Picos reflowed flat)
  tht      - through-hole, self-assembled (DIP opto socket, axial resistors, Picos A/F socketed)
  compact  - double-sided SMD: Pico B reflows on the back under Pico A; adds a USB-C host port
             (J2) in parallel with the USB-A, plus CC pull-ups. Finer JLCPCB 6/6 geometry.

Pico B always uses the SMD (flat) footprint because only it exposes the USB
test-point pads (TP1/TP2/TP3 = USB_GND/DM/DP) needed for the onboard USB-A host port.

Critical: the 6N137 galvanically isolates the two host computers. Domain 1
(Pico A + Pico B + USB-A) and domain 2 (Forwarder) have SEPARATE ground/VBUS
nets and separate copper pours with an isolation gap under the optocoupler.
"""
import sys
import pcbnew

SS = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
CLEAR = 0.2        # mm


def mm(v):
    return pcbnew.FromMM(v)


def vec(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def variant_config(variant):
    if variant == "smd":
        return {
            "pico_af": ("Module", "RaspberryPi_Pico_SMD"),
            "pico_b": ("Module", "RaspberryPi_Pico_SMD"),
            "opto": ("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm"),
            "res": ("Resistor_SMD", "R_0805_2012Metric"),
            "cap": ("Capacitor_SMD", "C_0805_2012Metric"),
            "usb": ("Connector_USB", "USB_A_Connfly_DS1095"),
        }
    if variant == "compact":
        # Double-sided: all SMD; Pico B mounts on the back under Pico A. Adds a
        # USB-C host port (J2) in parallel with the USB-A (J1), plus CC resistors.
        return {
            "pico_af": ("Module", "RaspberryPi_Pico_SMD"),
            "pico_b": ("Module", "RaspberryPi_Pico_SMD"),
            "opto": ("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm"),
            "res": ("Resistor_SMD", "R_0805_2012Metric"),
            "cap": ("Capacitor_SMD", "C_0805_2012Metric"),
            # Horizontal edge-mount USB-A: matches the USB-C's edge layout and
            # (unlike the upright DS1095) ships a KiCad 3D model for the preview.
            "usb": ("Connector_USB", "USB_A_TE_292303-7_Horizontal"),
            "usbc": ("Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12"),
            "esd": ("Package_TO_SOT_SMD", "SOT-23-6"),  # USBLC6-2SC6 USB ESD array
        }
    return {
        "pico_af": ("Module", "RaspberryPi_Pico_Common_THT"),
        "pico_b": ("Module", "RaspberryPi_Pico_SMD"),
        "opto": ("Package_DIP", "DIP-8_W7.62mm_Socket"),
        "res": ("Resistor_THT", "R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"),
        "cap": ("Capacitor_THT", "C_Disc_D5.0mm_W2.5mm_P5.00mm"),
        "usb": ("Connector_USB", "USB_A_Connfly_DS1095"),
    }


class Builder:
    def __init__(self, variant):
        self.variant = variant
        self.cfg = variant_config(variant)
        self.b = pcbnew.BOARD()
        self.nets = {}
        self.comps = {}
        self._setup_rules()

    def _setup_rules(self):
        ds = self.b.GetDesignSettings()
        # The compact variant packs a 0.5mm-pitch USB-C and two stacked Picos, so
        # it uses the finer geometry the JLCPCB 6/6 process allows (fan-out vias
        # behind the connector). smd/tht stay on the relaxed cheap-everywhere rules.
        if self.variant == "compact":
            track, clr, via_d, via_h = 0.2, 0.15, 0.5, 0.3
        else:
            track, clr, via_d, via_h = 0.35, CLEAR, 0.7, 0.35
        ds.m_TrackMinWidth = mm(min(track, 0.2))
        ds.m_MinClearance = mm(clr)
        nc = ds.m_NetSettings.GetDefaultNetclass()
        nc.SetTrackWidth(mm(track))
        nc.SetClearance(mm(clr))
        nc.SetViaDiameter(mm(via_d))
        nc.SetViaDrill(mm(via_h))
        self.clearance = clr

    # KiCad 10 ships no .step for our exact connector footprints, so their 3D
    # preview is empty. Substitute a visually-close model that does ship, with a
    # rotation/offset to orient it for THIS footprint. Cosmetic only - the pads,
    # courtyard, silkscreen and all fab outputs still come from the real footprint.
    # value: (model file, extra Z rotation deg, (dx, dy, dz) offset mm)
    M3D = "${KICAD10_3DMODEL_DIR}/Connector_USB.3dshapes"
    MODEL_SUBST = {
        # GCT USB-C model faces into the board on the HRO footprint; flip 180.
        "USB_C_Receptacle_HRO_TYPE-C-31-M-12": (
            f"{M3D}/USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal.step",
            180.0, (0.0, 0.0, 0.0)),
    }

    def load(self, ref, lib, name, x, y, rot=0, value=None, bottom=False):
        fp = pcbnew.FootprintLoad(f"{SS}/{lib}.pretty", name)
        if fp is None:
            raise RuntimeError(f"footprint {lib}:{name} not found")
        self.b.Add(fp)
        fp.SetReference(ref)
        sub = self.MODEL_SUBST.get(name)
        if sub and len(fp.Models()):
            fname, rz, off = sub
            m = fp.Models()[0]
            m.m_Filename = fname
            m.m_Rotation = pcbnew.VECTOR3D(0.0, 0.0, rz)
            m.m_Offset = pcbnew.VECTOR3D(*off)
        if value:
            fp.SetValue(value)
        fp.SetPosition(vec(0, 0))
        if rot:
            fp.SetOrientationDegrees(rot)
        if bottom:                       # mount on the back copper layer
            fp.Flip(pcbnew.VECTOR2I(0, 0), False)
        # Re-centre on the pad bounding box so (x, y) is the visual centre
        # regardless of the footprint's origin (SMD Pico = centre, THT = pin 1).
        xs = [p.GetPosition().x for p in fp.Pads()]
        ys = [p.GetPosition().y for p in fp.Pads()]
        cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
        fp.Move(pcbnew.VECTOR2I(mm(x) - cx, mm(y) - cy))
        self.comps[ref] = fp
        return fp

    def net(self, name):
        if name not in self.nets:
            n = pcbnew.NETINFO_ITEM(self.b, name)
            self.b.Add(n)
            self.nets[name] = n
        return self.nets[name]

    def connect(self, net_name, *pads):
        """pads: (ref, padnum). Assigns the net to every pad with that number
        (castellated Pico pads have top+bottom entries sharing a number)."""
        n = self.net(net_name)
        for ref, num in pads:
            fp = self.comps[ref]
            hits = [p for p in fp.Pads() if p.GetNumber() == str(num)]
            if not hits:
                raise RuntimeError(f"{ref} has no pad {num}")
            for p in hits:
                p.SetNet(n)

    def pad(self, ref, num):
        for p in self.comps[ref].Pads():
            if p.GetNumber() == str(num):
                return p
        raise RuntimeError(f"{ref} pad {num} missing")

    def pos(self, ref, num):
        p = self.pad(ref, num).GetPosition()
        return pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)

    # ---- placement ----
    # Compact row: [Pico B][Pico A] | gap+opto | [Forwarder]. Domain 1 (B, A,
    # USB-A) sits left of the optocoupler, domain 2 (Forwarder) to the right.
    # SMD is packed tight; THT is roomier for the larger DIP/axial parts.
    PLACEMENT = {
        # ref:  (x, y, rot)
        "smd": {"A2": (24, 30, 180), "A1": (50, 30, 0), "U1": (67, 30, 0),
                "A3": (88, 30, 0), "R1": (63, 12, 0), "R2": (73, 12, 0),
                "C1": (73, 48, 0), "J1": (24, 67, 0)},
        "tht": {"A2": (24, 30, 180), "A1": (50, 30, 0), "U1": (72, 30, 0),
                "A3": (100, 30, 0), "R1": (61, 12, 0), "R2": (85, 12, 0),
                "C1": (85, 48, 0), "J1": (24, 67, 0)},
        # Compact double-sided. Domain 1 (left): Pico A on top with Pico B stacked
        # on the back (same x,y), plus host connectors J1/J2 and CC resistors.
        # Domain 2 (right): Forwarder, opto output, R2, C1. Opto straddles the gap.
        # The three small domain-1 parts (U2/R3/R4) tuck into the Y-gap between J1
        # and J2 in the connector column, so Pico A abuts the connectors and the
        # whole right-hand cluster sits 8mm further left than a naive layout.
        "compact": {"A1": (32, 30, 0), "A2": (32, 30, 0),
                    "U1": (51, 26, 0), "A3": (69, 30, 0),
                    "R1": (46, 22, 0), "R2": (56, 22, 0), "C1": (55, 40, 0),
                    # U2 = USB ESD array; R3/R4 = USB-C CC pull-ups. All domain 1,
                    # parked in the pocket between J1 (above) and J2 (below) on the
                    # left edge, close to the connector data/CC pins they tap.
                    "U2": (16, 27.5, 0), "R3": (16, 31, 0), "R4": (16, 33.5, 0),
                    # Both host connectors face the left board edge (mouth = -X),
                    # verified via pad-centroid-vs-courtyard, NOT the cosmetic 3D
                    # model. They have different depths, so they're placed by X to
                    # land both mouths at the same edge: USB-A (deep) at x=15, the
                    # shallower USB-C at x=8 so its mouth lines up with the USB-A's.
                    "J1": (15, 16, 270), "J2": (8, 41, 270)},
    }
    # Refs mounted on the back copper layer (compact variant only).
    BOTTOM = {"compact": {"A2"}}
    PARTS = {"A2": "pico_b", "A1": "pico_af", "A3": "pico_af", "U1": "opto",
             "R1": "res", "R2": "res", "C1": "cap", "J1": "usb",
             "R3": "res", "R4": "res", "J2": "usbc", "U2": "esd"}
    VALUES = {"A2": "RP2350_PicoB", "A1": "RP2350_PicoA", "A3": "RP2350_Fwd",
              "U1": "6N137", "R1": "470R", "R2": "680R", "C1": "100nF",
              "J1": "USB_A_Host", "R3": "56k", "R4": "56k", "J2": "USB_C_Host",
              "U2": "USBLC6-2SC6"}

    def place(self):
        # Pico B (host) rotated 180 so its serial pins (1-5) face Pico A and its
        # USB/test-point end faces toward the host connectors. In the compact
        # variant Pico B mounts on the back, directly under Pico A.
        bottom = self.BOTTOM.get(self.variant, set())
        for ref, (x, y, rot) in self.PLACEMENT[self.variant].items():
            self.load(ref, *self.cfg[self.PARTS[ref]], x, y, rot=rot,
                      value=self.VALUES[ref], bottom=ref in bottom)
        # Isolation gap from the opto's own pads: domain-1 input pins (2,3) on the
        # left, domain-2 output pins (5,6,8) on the right.
        in_x = max(self.pos("U1", n)[0] for n in (2, 3))
        out_x = min(self.pos("U1", n)[0] for n in (5, 6, 8))
        self.iso_l = round(in_x + 0.5, 2)
        self.iso_r = round(out_x - 0.5, 2)

    # ---- netlist ----
    def wire(self):
        # Domain 1: Pico A <-> Pico B serial (crossed UART0 + flow control)
        self.connect("SER_A0_B1", ("A1", 1), ("A2", 2))   # A.GPIO0 -> B.GPIO1
        self.connect("SER_A1_B0", ("A1", 2), ("A2", 1))   # A.GPIO1 -> B.GPIO0
        self.connect("SER_A2_B3", ("A1", 4), ("A2", 5))   # A.GPIO2 -> B.GPIO3
        self.connect("SER_A3_B2", ("A1", 5), ("A2", 4))   # A.GPIO3 -> B.GPIO2
        # Domain 1 power. Pico GND pins: 3,8,13,18,23,28,33,38 (all tied to pour).
        gnd = [3, 8, 13, 18, 23, 28, 33, 38]
        self.connect("VBUS1", ("A1", 40), ("A2", 40), ("J1", 1))
        self.connect("GND1", *[("A1", g) for g in gnd], *[("A2", g) for g in gnd],
                     ("J1", 4), ("J1", "SH"), ("A2", "TP1"))
        # Opto TX side (domain 1): 3V3 - R1 - anode ; cathode - GPIO20
        self.connect("OPTO_3V3", ("A1", 36), ("R1", 1))
        self.connect("OPTO_ANODE", ("R1", 2), ("U1", 2))
        self.connect("OPTO_CATH", ("U1", 3), ("A1", 26))   # A.GPIO20
        # USB-A host on Pico B native USB (test points)
        self.connect("USB_DM", ("J1", 2), ("A2", "TP2"))
        self.connect("USB_DP", ("J1", 3), ("A2", "TP3"))
        # Domain 2: Forwarder + opto output side (isolated)
        self.connect("VBUS2", ("A3", 40), ("U1", 8), ("C1", 1))
        self.connect("GND2", *[("A3", g) for g in gnd], ("U1", 5), ("C1", 2))
        self.connect("FWD_3V3", ("A3", 36), ("R2", 1))
        self.connect("OPTO_VO", ("R2", 2), ("U1", 6), ("A3", 12))  # A3.GPIO9
        # Compact variant: USB-C host port (J2) in parallel with the USB-A on
        # Pico B's native USB. Only one connector may be used at a time.
        if "J2" in self.comps:
            self.connect("VBUS1", ("J2", "A4"), ("J2", "B4"), ("J2", "A9"), ("J2", "B9"))
            self.connect("GND1", ("J2", "A1"), ("J2", "B1"), ("J2", "A12"),
                         ("J2", "B12"), ("J2", "SH"))
            self.connect("USB_DP", ("J2", "A6"), ("J2", "B6"))
            self.connect("USB_DM", ("J2", "A7"), ("J2", "B7"))
            # Host advertises default USB power: 56k Rp from each CC line to VBUS1.
            self.connect("CC1", ("J2", "A5"), ("R3", 1))
            self.connect("CC2", ("J2", "B5"), ("R4", 1))
            self.connect("VBUS1", ("R3", 2), ("R4", 2))
            # SBU1/SBU2 (A8/B8) unused.
        # ESD protection (U2 = USBLC6-2SC6) shunt-clamps the host data lines to
        # VBUS1/GND1. Both I/O1 pins (1,6) tap USB_DP, both I/O2 pins (3,4) tap
        # USB_DM; pin5 VBUS, pin2 GND. Domain 1 only - no effect on isolation.
        if "U2" in self.comps:
            self.connect("USB_DP", ("U2", 1), ("U2", 6))
            self.connect("USB_DM", ("U2", 3), ("U2", 4))
            self.connect("VBUS1", ("U2", 5))
            self.connect("GND1", ("U2", 2))

    # ---- board outline ----
    def outline(self):
        # smd/compact are packed tight: size from copper courtyards, ignoring silk
        # overhang. THT uses the full footprint extent + larger margin so the
        # autorouter has room to finish around the bigger through-hole parts.
        tight = self.variant in ("smd", "compact")
        margin = 2.5 if tight else 4.0
        # compact host connectors (J1/J2) are horizontal edge-mount with mouths
        # facing -X; the left board edge sits flush with their mouths (0 margin)
        # so a cable can plug in, while every other edge keeps the full margin.
        flush_left = self.variant == "compact"
        left_cands, xs_r, ys_t, ys_b = [], [], [], []
        for ref, fp in self.comps.items():
            # back-mounted parts (compact A2) carry their courtyard on B_CrtYd
            cy = fp.GetCourtyard(pcbnew.F_CrtYd)
            if not cy.OutlineCount():
                cy = fp.GetCourtyard(pcbnew.B_CrtYd)
            if tight and cy.OutlineCount():
                bb = cy.BBox()
            else:
                bb = fp.GetBoundingBox()
            m = 0.0 if (flush_left and ref in ("J1", "J2")) else margin
            left_cands.append(pcbnew.ToMM(bb.GetLeft()) - m)
            xs_r.append(pcbnew.ToMM(bb.GetRight()))
            ys_t.append(pcbnew.ToMM(bb.GetTop()))
            ys_b.append(pcbnew.ToMM(bb.GetBottom()))
        l = min(left_cands)
        t = min(ys_t) - margin
        r = max(xs_r) + margin
        bt = max(ys_b) + margin
        self._rect(l, t, r, bt)

    def _rect(self, l, t, r, bt):
        self.bounds = (l, t, r, bt)
        corners = [(l, t), (r, t), (r, bt), (l, bt)]
        for i in range(4):
            seg = pcbnew.PCB_SHAPE(self.b)
            seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
            seg.SetStart(vec(*corners[i]))
            seg.SetEnd(vec(*corners[(i + 1) % 4]))
            seg.SetLayer(pcbnew.Edge_Cuts)
            seg.SetWidth(mm(0.15))
            self.b.Add(seg)

    # ---- unplaced export: footprints + netlist, no curated placement/routing ----
    # Placement-area rectangle the auto-placer arranges within. Sized a little
    # larger than the hand-placed boards (smd 92x82, tht 112x106) to give room.
    UNPLACED_AREA = {"smd": (95, 85), "tht": (115, 110)}

    def scatter(self):
        """Load every footprint with no curated placement - parked in a column
        just right of the board outline for an external auto-placer to arrange."""
        w, _ = self.UNPLACED_AREA[self.variant]
        y = 0.0
        for ref in self.PLACEMENT[self.variant]:
            fp = self.load(ref, *self.cfg[self.PARTS[ref]], w + 30, y + 30,
                           value=self.VALUES[ref])
            y += pcbnew.ToMM(fp.GetBoundingBox().GetHeight()) + 6

    def area_outline(self):
        w, h = self.UNPLACED_AREA[self.variant]
        self._rect(0, 0, w, h)

    # ---- isolation keepout: no copper allowed in the gap between domains ----
    def keepout(self):
        _, t, _, bt = self.bounds
        ka = pcbnew.ZONE(self.b)
        ka.SetIsRuleArea(True)
        ka.SetDoNotAllowTracks(True)
        ka.SetDoNotAllowVias(True)
        ka.SetDoNotAllowZoneFills(True)
        ls = pcbnew.LSET()
        ls.AddLayer(pcbnew.F_Cu)
        ls.AddLayer(pcbnew.B_Cu)
        ka.SetLayerSet(ls)
        poly = ka.Outline()
        poly.NewOutline()
        for x, y in [(self.iso_l, t - 1), (self.iso_r, t - 1),
                     (self.iso_r, bt + 1), (self.iso_l, bt + 1)]:
            poly.Append(mm(x), mm(y))
        self.b.Add(ka)

    def dump_pads(self):
        keys = {
            "A1": [1, 2, 4, 5, 26, 36, 40, 38],
            "A2": [1, 2, 4, 5, 40, 38, "TP1", "TP2", "TP3"],
            "A3": [12, 36, 40, 38],
            "U1": [2, 3, 5, 6, 8],
            "R1": [1, 2], "R2": [1, 2], "C1": [1, 2],
            "J1": [1, 2, 3, 4],
        }
        for ref, nums in keys.items():
            parts = " ".join(f"{n}=({self.pos(ref, n)[0]:.2f},{self.pos(ref, n)[1]:.2f})" for n in nums)
            print(f"{ref}: {parts}")
        print("bounds", tuple(round(v, 1) for v in self.bounds))

    def save(self, path):
        self.b.BuildConnectivity()
        self.b.Save(path)


def build(variant, out, dsn):
    bd = Builder(variant)
    bd.place()
    bd.wire()
    bd.outline()
    bd.keepout()
    # GND routes as tracks via freerouting (headless zone-fill is unavailable),
    # so no copper pours here; the keepout still enforces domain isolation.
    bd.dump_pads()
    bd.save(out)
    if not pcbnew.ExportSpecctraDSN(bd.b, dsn):
        raise RuntimeError("DSN export failed")
    print(f"built {out} -> {dsn}  iso_gap={bd.iso_l},{bd.iso_r}  clr={bd.clearance}")


def build_unplaced(variant, out):
    bd = Builder(variant)
    bd.scatter()
    bd.wire()
    bd.area_outline()
    bd.save(out)
    w, h = bd.UNPLACED_AREA[variant]
    print(f"built unplaced {out}  ({w}x{h}mm placement area, "
          f"{len(bd.comps)} footprints, netlist only, no routing)")


def finalize(unrouted, ses, out):
    b = pcbnew.LoadBoard(unrouted)
    if not pcbnew.ImportSpecctraSES(b, ses):
        raise RuntimeError("SES import failed")
    b.BuildConnectivity()
    b.Save(out)
    print("finalized", out, "tracks:", len(list(b.GetTracks())))


def main():
    mode = sys.argv[1]
    if mode == "build":
        variant, out, dsn = sys.argv[2], sys.argv[3], sys.argv[4]
        build(variant, out, dsn)
    elif mode == "finalize":
        unrouted, ses, out = sys.argv[2], sys.argv[3], sys.argv[4]
        finalize(unrouted, ses, out)
    elif mode == "unplaced":
        variant, out = sys.argv[2], sys.argv[3]
        build_unplaced(variant, out)
    else:
        raise SystemExit("usage: build_board.py build|finalize|unplaced ...")


if __name__ == "__main__":
    main()
