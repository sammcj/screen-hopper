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
    if variant == "header":
        # All three Picos are plug-in 0.1" THT headers (no hand-reflow). Pico B
        # mounts on the back, offset in Y from Pico A so the two parts' plated
        # holes interleave instead of colliding. There is NO on-board host
        # connector: the mouse/keyboard plugs into Pico B's own micro-USB (via an
        # OTG adapter), electrically the same lines (D-/D+/VBUS/GND) the other
        # variants route to a receptacle. Dropping the USB-C receptacle, its CC
        # pull-ups, the ESD array and the USB tap header shrinks the board and
        # removes the hand-wiring. Only the opto + its passives are SMD (gap reclaim
        # in the channels under the raised Pico bodies).
        return {
            "pico_af": ("Module", "RaspberryPi_Pico_Common_THT"),
            "pico_b": ("Module", "RaspberryPi_Pico_Common_THT"),
            "opto": ("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm"),
            "res": ("Resistor_SMD", "R_0805_2012Metric"),
            "cap": ("Capacitor_SMD", "C_0805_2012Metric"),
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
        if self.variant in ("compact", "header"):
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
        # Header (plug-in) two-row layout. Picos rotated horizontal (long axis = X).
        # Top row = domain 1: Pico A with Pico B on the back, offset +8.89mm in Y so
        # the two THT hole-grids interleave (rows at Y 5.11/14/22.89/31.78, all
        # 8.89mm apart). Bottom row = domain 2: Forwarder. Opto straddles the gap
        # between rows (input pins to domain 1 above, output to domain 2 below);
        # isolation keepout is a HORIZONTAL strip there. No host connector (Pico B's
        # own micro-USB is the host port), so the only SMD parts are the opto and
        # its passives: R1 in the Pico A/B inter-row channel, R2/C1 in the
        # Forwarder's clear inter-row channel - all between offset rows, clear of holes.
        "header": {"A1": (40, 14, 90), "A2": (40, 22.89, 270), "A3": (40, 50.5, 90),
                   "U1": (40, 36.7, 270), "R1": (52, 18.5, 0),
                   "R2": (28, 50.5, 0), "C1": (52, 50.5, 0)},
    }
    # Refs mounted on the back copper layer (Pico B in the double-sided variants).
    BOTTOM = {"compact": {"A2"}, "header": {"A2"}}
    PARTS = {"A2": "pico_b", "A1": "pico_af", "A3": "pico_af", "U1": "opto",
             "R1": "res", "R2": "res", "C1": "cap", "J1": "usb",
             "R3": "res", "R4": "res", "J2": "usbc", "U2": "esd", "J3": "tap"}
    VALUES = {"A2": "RP2350_PicoB", "A1": "RP2350_PicoA", "A3": "RP2350_Fwd",
              "U1": "6N137", "R1": "470R", "R2": "680R", "C1": "100nF",
              "J1": "USB_A_Host", "R3": "56k", "R4": "56k", "J2": "USB_C_Host",
              "U2": "USBLC6-2SC6", "J3": "USB_tap"}

    def place(self):
        # Pico B (host) rotated 180 so its serial pins (1-5) face Pico A and its
        # USB/test-point end faces toward the host connectors. In the compact
        # variant Pico B mounts on the back, directly under Pico A.
        bottom = self.BOTTOM.get(self.variant, set())
        values = dict(self.VALUES)
        # SO-8 variants use the HCPL-0601 (the 6N137 is DIP-8 / gull-wing only, not a
        # SOIC-8); the tht DIP-socket variant keeps the 6N137. See gen_sch.components().
        if self.variant in ("smd", "compact", "header"):
            values["U1"] = "HCPL-0601"
        for ref, (x, y, rot) in self.PLACEMENT[self.variant].items():
            self.load(ref, *self.cfg[self.PARTS[ref]], x, y, rot=rot,
                      value=values[ref], bottom=ref in bottom)
        # Isolation gap from the opto's own pads: domain-1 input pins (2,3) vs
        # domain-2 output pins (5,6,8). smd/tht/compact split left/right (a vertical
        # strip on X); the header two-row layout splits top/bottom (horizontal, on
        # Y). The strip sits between whichever group is lower and whichever is higher.
        ax = 1 if self.variant == "header" else 0
        self.iso_axis = "y" if ax else "x"
        ins = [self.pos("U1", n)[ax] for n in (2, 3)]
        outs = [self.pos("U1", n)[ax] for n in (5, 6, 8)]
        if sum(ins) / len(ins) < sum(outs) / len(outs):
            self.iso_lo = round(max(ins) + 0.5, 2)
            self.iso_hi = round(min(outs) - 0.5, 2)
        else:
            self.iso_lo = round(max(outs) + 0.5, 2)
            self.iso_hi = round(min(ins) - 0.5, 2)
        self.iso_l, self.iso_r = self.iso_lo, self.iso_hi  # back-compat names

    # ---- netlist ----
    def wire(self):
        # Domain 1: Pico A <-> Pico B serial (crossed UART0 + flow control)
        self.connect("SER_A0_B1", ("A1", 1), ("A2", 2))   # A.GPIO0 -> B.GPIO1
        self.connect("SER_A1_B0", ("A1", 2), ("A2", 1))   # A.GPIO1 -> B.GPIO0
        self.connect("SER_A2_B3", ("A1", 4), ("A2", 5))   # A.GPIO2 -> B.GPIO3
        self.connect("SER_A3_B2", ("A1", 5), ("A2", 4))   # A.GPIO3 -> B.GPIO2
        # Domain 1 power. Pico GND pins: 3,8,13,18,23,28,33,38 (all tied to pour).
        gnd = [3, 8, 13, 18, 23, 28, 33, 38]
        header = self.variant == "header"
        vbus1 = [("A1", 40), ("A2", 40)]
        gnd1 = [*[("A1", g) for g in gnd], *[("A2", g) for g in gnd]]
        # The header variant has no on-board host connector - the device plugs into
        # Pico B's own micro-USB - so it adds no J1 power/ground or USB data lines.
        if not header:
            vbus1.append(("J1", 1))            # USB-A supplies host VBUS
            gnd1 += [("J1", 4), ("J1", "SH"), ("A2", "TP1")]
        self.connect("VBUS1", *vbus1)
        self.connect("GND1", *gnd1)
        # Opto TX side (domain 1): 3V3 - R1 - anode ; cathode - GPIO20
        self.connect("OPTO_3V3", ("A1", 36), ("R1", 1))
        self.connect("OPTO_ANODE", ("R1", 2), ("U1", 2))
        self.connect("OPTO_CATH", ("U1", 3), ("A1", 26))   # A.GPIO20
        # Host USB data lines: the smd/tht/compact boards route Pico B's TP pads to
        # the USB-A receptacle. The header variant has no on-board host port, so no
        # USB_DM/USB_DP nets (Pico B hosts through its own micro-USB connector).
        if not header:
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
        tight = self.variant in ("smd", "compact", "header")
        # header: all parts plug into sockets, so the Pico bodies float clear above
        # the board and only their holes need to fit. Size to the PAD extents (not
        # the body courtyard) with a tight edge margin - the Pico body / USB end may
        # overhang the board edge harmlessly. The others size to the courtyard.
        pad_box = self.variant == "header"
        margin = 2.0 if pad_box else (2.5 if tight else 4.0)
        # compact host connectors (J1/J2) are horizontal edge-mount with mouths
        # facing -X; the left board edge sits flush with their mouths (0 margin)
        # so a cable can plug in, while every other edge keeps the full margin.
        flush_left = self.variant == "compact"
        left_cands, xs_r, ys_t, ys_b = [], [], [], []
        for ref, fp in self.comps.items():
            if pad_box:
                pads = list(fp.Pads())
                hw = max(pcbnew.ToMM(p.GetSize().x) for p in pads) / 2
                hh = max(pcbnew.ToMM(p.GetSize().y) for p in pads) / 2
                pxs = [pcbnew.ToMM(p.GetPosition().x) for p in pads]
                pys = [pcbnew.ToMM(p.GetPosition().y) for p in pads]
                left_cands.append(min(pxs) - hw - margin)
                xs_r.append(max(pxs) + hw)
                ys_t.append(min(pys) - hh)
                ys_b.append(max(pys) + hh)
                continue
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
        l, _, r, _ = self.bounds
        if self.iso_axis == "y":     # horizontal strip spanning the full width
            pts = [(l - 1, self.iso_lo), (r + 1, self.iso_lo),
                   (r + 1, self.iso_hi), (l - 1, self.iso_hi)]
        else:                        # vertical strip spanning the full height
            pts = [(self.iso_lo, t - 1), (self.iso_hi, t - 1),
                   (self.iso_hi, bt + 1), (self.iso_lo, bt + 1)]
        for x, y in pts:
            poly.Append(mm(x), mm(y))
        self.b.Add(ka)

    def dump_pads(self):
        header = self.variant == "header"
        keys = {
            "A1": [1, 2, 4, 5, 26, 36, 40, 38],
            "A2": [1, 2, 4, 5, 40, 38] + ([] if header else ["TP1", "TP2", "TP3"]),
            "A3": [12, 36, 40, 38],
            "U1": [2, 3, 5, 6, 8],
            "R1": [1, 2], "R2": [1, 2], "C1": [1, 2],
        }
        if not header:                 # smd/tht/compact have the USB-A host connector
            keys["J1"] = [1, 2, 3]
        for ref, nums in keys.items():
            parts = " ".join(f"{n}=({self.pos(ref, n)[0]:.2f},{self.pos(ref, n)[1]:.2f})" for n in nums)
            print(f"{ref}: {parts}")
        print("bounds", tuple(round(v, 1) for v in self.bounds))

    def _pad_box(self, ref):
        fp = self.comps[ref]
        xs = [pcbnew.ToMM(p.GetPosition().x) for p in fp.Pads()]
        ys = [pcbnew.ToMM(p.GetPosition().y) for p in fp.Pads()]
        return min(xs), min(ys), max(xs), max(ys)

    def _silk_text(self, txt, x, y, layer, size=1.2, angle=0, mirror=False):
        t = pcbnew.PCB_TEXT(self.b)
        t.SetText(txt)
        t.SetLayer(layer)
        t.SetPosition(vec(x, y))
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(round(size * 0.15, 3)))
        if angle:
            t.SetTextAngleDegrees(angle)
        if mirror:
            t.SetMirrored(True)
        self.b.Add(t)
        return t

    def silk(self):
        """Module names at the outer edges, clear of the opto in the centre gap:
        Pico A above the top row (front), Pico B above it on the back (B_Silk,
        mirrored), the Forwarder below the bottom row (front). Header variant only -
        the other boards keep their stock silk."""
        if self.variant != "header":
            return
        al, at, ar, _ = self._pad_box("A1")
        top = at - 1.5
        self._silk_text("Pico A", (al + ar) / 2, top, pcbnew.F_SilkS, size=1.2)
        bl, _, br, _ = self._pad_box("A2")
        self._silk_text("Pico B (back)", (bl + br) / 2, top, pcbnew.B_SilkS,
                        size=1.2, mirror=True)
        fl, _, fr, fb = self._pad_box("A3")
        self._silk_text("Forwarder", (fl + fr) / 2, fb + 1.5, pcbnew.F_SilkS, size=1.2)
        # Corner pin numbers (orientation + counting), pushed just off the pad ends.
        for ref, layer, mir in (("A1", pcbnew.F_SilkS, False),
                                ("A2", pcbnew.B_SilkS, True),
                                ("A3", pcbnew.F_SilkS, False)):
            lo_x, _, hi_x, _ = self._pad_box(ref)
            cx = (lo_x + hi_x) / 2
            for num in ("1", "20", "21", "40"):
                p = self.pad(ref, num).GetPosition()
                px, py = pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)
                self._silk_text(num, px + (-1.4 if px < cx else 1.4), py, layer,
                                size=0.7, mirror=mir)
        # (The GPIO signal map - A<->B serial GP0-3, opto A.GP20 / Fwd.GP9, host =
        # Pico B micro-USB - lives in the README wiring table; the tightened board
        # has no clear band for a legend, and the Pico names + pin numbers suffice.)

    def save(self, path):
        self.b.BuildConnectivity()
        self.b.Save(path)


def build(variant, out, dsn):
    bd = Builder(variant)
    bd.place()
    bd.wire()
    bd.outline()
    bd.keepout()
    bd.silk()
    # Signals route as tracks via freerouting. The header variant adds per-domain
    # GND pours at finalize() (KiCad 10 fills zones headless); the others route GND
    # as tracks too. The keepout enforces domain isolation in every case.
    bd.dump_pads()
    bd.save(out)
    if not pcbnew.ExportSpecctraDSN(bd.b, dsn):
        raise RuntimeError("DSN export failed")
    print(f"built {out} -> {dsn}  iso_gap={bd.iso_lo},{bd.iso_hi}  "
          f"iso_axis={bd.iso_axis}  clr={bd.clearance}")


def build_unplaced(variant, out):
    bd = Builder(variant)
    bd.scatter()
    bd.wire()
    bd.area_outline()
    bd.save(out)
    w, h = bd.UNPLACED_AREA[variant]
    print(f"built unplaced {out}  ({w}x{h}mm placement area, "
          f"{len(bd.comps)} footprints, netlist only, no routing)")


def add_gnd_pours(b, iso_lo, iso_hi, axis):
    """Flood each ground domain with a copper pour on both layers, clipped to its
    side of the isolation gap so the two domains stay separate. Connects every GND
    pad (incl. the inner pins the autorouter strands) and gives proper planes.
    GND1 = domain 1 (the low side of the gap), GND2 = domain 2 (the high side)."""
    bb = b.GetBoardEdgesBoundingBox()
    l, t = pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop())
    r, bt = pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())
    ni = b.GetNetInfo()

    def rect(net, layer, x0, y0, x1, y1):
        z = pcbnew.ZONE(b)
        z.SetLayer(layer)
        z.SetNet(ni.GetNetItem(net))
        # Solid pad connection + a thin min-width so the pour necks between the
        # dense interleaved Pico holes and reaches every GND pin (incl. inner ones
        # the autorouter strands) instead of fragmenting into thermal islands.
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        z.SetMinThickness(mm(0.13))
        z.SetLocalClearance(mm(0.15))
        poly = z.Outline()
        poly.NewOutline()
        for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
            poly.Append(mm(x), mm(y))
        b.Add(z)

    for net, lo, hi in (("GND1", None, iso_lo), ("GND2", iso_hi, None)):
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            if axis == "y":
                rect(net, layer, l - 1, t - 1 if lo is None else lo,
                     r + 1, bt + 1 if hi is None else hi)
            else:
                rect(net, layer, l - 1 if lo is None else lo, t - 1,
                     r + 1 if hi is None else hi, bt + 1)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())


def _seg_clear(b, a, c, layer, netcode, clr_mm=0.2):
    """True if a 0.3mm track from a to c on `layer` clears every other-net item."""
    seg = pcbnew.SHAPE_SEGMENT(pcbnew.VECTOR2I(a.x, a.y),
                               pcbnew.VECTOR2I(c.x, c.y), mm(0.3))
    clr = mm(clr_mm)
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() != netcode and p.IsOnLayer(layer) \
               and seg.Collide(p.GetEffectiveShape(layer), clr):
                return False
    for t in b.GetTracks():
        if t.GetNetCode() != netcode and t.IsOnLayer(layer) \
           and seg.Collide(t.GetEffectiveShape(layer), clr):
            return False
    return True


def stitch_grounds(b):
    """Guarantee every GND pad is copper-connected. The autorouter can strand an
    inner GND pin in the dense interleaved-hole band, and a pour can fragment
    around it. Union-find the GND pads over the routed tracks, then add a straight
    track from any stray to its nearest in-cluster neighbour on a verified-clear
    layer (the stranded pins sit in clear 8.9mm channels next to another GND pin)."""
    added = 0
    for netname in ("GND1", "GND2"):
        ni = b.GetNetInfo().GetNetItem(netname)
        if ni is None:
            continue
        code = ni.GetNetCode()
        pads = [p for fp in b.GetFootprints() for p in fp.Pads()
                if p.GetNetCode() == code]
        segs = [t for t in b.GetTracks() if t.GetNetCode() == code
                and t.Type() == pcbnew.PCB_TRACE_T]
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, c):
            parent[find(a)] = find(c)

        def key(pt):
            return (round(pt.x / 1000), round(pt.y / 1000))
        ends = set()
        for t in segs:
            union(key(t.GetStart()), key(t.GetEnd()))
            ends.add(key(t.GetStart()))
            ends.add(key(t.GetEnd()))
        for t in b.GetTracks():
            if t.GetNetCode() == code and t.Type() == pcbnew.PCB_VIA_T:
                ends.add(key(t.GetStart()))
        for p in pads:                       # tie a pad to a coincident track end
            pk = key(p.GetPosition())
            r = pcbnew.ToMM(p.GetBoundingRadius())
            for e in ends:
                if abs(e[0] - pk[0]) <= r * 1000 and abs(e[1] - pk[1]) <= r * 1000:
                    union(pk, e)
        from collections import defaultdict
        comp = defaultdict(list)
        for p in pads:
            comp[find(key(p.GetPosition()))].append(p)
        if len(comp) <= 1:
            continue
        main = max(comp.values(), key=len)
        mainset = {id(p) for p in main}
        for p in pads:
            if id(p) in mainset:
                continue
            pp = p.GetPosition()
            tgt = min(main, key=lambda m: (m.GetPosition().x - pp.x) ** 2
                      + (m.GetPosition().y - pp.y) ** 2)
            for layer in (pcbnew.B_Cu, pcbnew.F_Cu):
                if _seg_clear(b, pp, tgt.GetPosition(), layer, code):
                    tr = pcbnew.PCB_TRACK(b)
                    tr.SetStart(pp)
                    tr.SetEnd(tgt.GetPosition())
                    tr.SetWidth(mm(0.3))
                    tr.SetLayer(layer)
                    tr.SetNet(ni)
                    b.Add(tr)
                    added += 1
                    break
    return added


def finalize(unrouted, ses, out, pour=None):
    b = pcbnew.LoadBoard(unrouted)
    if not pcbnew.ImportSpecctraSES(b, ses):
        raise RuntimeError("SES import failed")
    stitched = stitch_grounds(b)
    if pour:
        add_gnd_pours(b, *pour)
    b.BuildConnectivity()
    b.Save(out)
    extra = (f"  GND pours: {len(list(b.Zones())) - 1}  stitched: {stitched}"
             if pour else "")
    print("finalized", out, "tracks:", len(list(b.GetTracks())), extra)


def main():
    mode = sys.argv[1]
    if mode == "build":
        variant, out, dsn = sys.argv[2], sys.argv[3], sys.argv[4]
        build(variant, out, dsn)
    elif mode == "finalize":
        unrouted, ses, out = sys.argv[2], sys.argv[3], sys.argv[4]
        pour = None
        if len(sys.argv) >= 8:        # iso_lo iso_hi axis -> add GND pours
            pour = (float(sys.argv[5]), float(sys.argv[6]), sys.argv[7])
        finalize(unrouted, ses, out, pour)
    elif mode == "unplaced":
        variant, out = sys.argv[2], sys.argv[3]
        build_unplaced(variant, out)
    else:
        raise SystemExit("usage: build_board.py build|finalize|unplaced ...")


if __name__ == "__main__":
    main()
