# Screen Hopper - triple Pico 2 carrier PCB

A carrier board for the triple-Pico version of Screen Hopper. The three Raspberry Pi Pico 2 boards, a high-speed optocoupler, the two resistors, a bypass capacitor and a USB-A host port all mount on one PCB, replacing the breadboard and jumper wires described in [HARDWARE.md](../HARDWARE.md).

Four variants are provided from the same netlist:

| Variant | Folder | Size | Assembly |
| ------- | ------ | ---- | -------- |
| Compact SMD | [`smd/`](smd/) | 92 x 82 mm | Optocoupler + passives reflowed by the fab; you mount the three Picos flat, single-sided |
| Through-hole | [`tht/`](tht/) | 112 x 106 mm | You solder everything; Picos A and Forwarder on headers |
| Double-sided | [`compact/`](compact/) | 81 x 59 mm | Smallest by area. Pico B reflows on the back under Pico A; adds a USB-C host port |
| Plug-in header | [`header/`](header/) | 54 x 60 mm | No Pico reflow - all three Picos plug into 0.1" pin-headers. No host connector (device uses Pico B's micro-USB). Fab places only the opto + 3 passives |

![SMD board top view](smd/preview-top.png)

The double-sided `compact` variant is the smallest board. It stacks Pico B on the back copper directly beneath Pico A and offers both a USB-C and a USB-A host receptacle - **populate only one** (see [Host connector](#host-connector-compact-variant)). It needs the finer JLCPCB 6/6 process (0.2 mm tracks, 0.15 mm clearance, 0.5/0.3 mm vias) to fan out the 0.5 mm-pitch USB-C, where the other two run on the relaxed cheap-everywhere rules.

The `header` variant exists so you never reflow a Pico. All three Picos plug into 0.1" pin-headers (soldering a straight header strip is far easier than reflowing a castellated module), so the fab only places the SMD optocoupler and its three passives. Pico A is on the front top row and Pico B on the back directly behind it; because two through-hole Picos can't share holes, Pico B's hole grid is **offset by 8.89 mm** (half the 17.78 mm row spacing) so the grids interleave. There is no on-board host connector: the mouse/keyboard plugs into Pico B's own micro-USB through an OTG adapter (the same D-/D+/VBUS/GND lines the other variants route to a receptacle), which is what keeps this board small and simple. It uses the same fine 6/6 process as `compact` for the dense interleaved Pico holes. See [Plug-in header variant](#plug-in-header-variant).

## The one thing that matters: galvanic isolation

The optocoupler exists to keep the two host computers electrically isolated. Pico A and Pico B share power and ground fed from computer 1 (and the input device); the Forwarder runs on its own power and ground fed from computer 2. The only connection between the two sides is the optocoupler's internal LED-to-phototransistor light path.

The board enforces this with **two separate ground/power nets** (`GND1`/`VBUS1` and `GND2`/`VBUS2`) and a **copper keepout gap under the optocoupler** that no track or pour may cross. Domain 1 copper stops just left of the opto's input pins; domain 2 copper starts just right of its output pins. Do not bridge the two sides with a wire, a ground pour, or a mounting screw/standoff that ties the grounds together - that defeats the isolation and can create a ground loop between two separate computers.

## What to send to the PCB fab

For a **bare board** (any variant), upload the gerber zip:

- `smd/screen-hopper-smd-gerbers.zip`, `tht/screen-hopper-tht-gerbers.zip`, `compact/screen-hopper-compact-gerbers.zip` or `header/screen-hopper-header-gerbers.zip`

Fab settings: **2 layers, 1.6 mm FR-4, 1 oz copper, HASL or ENIG**. The `smd`/`tht` boards use 0.35 mm tracks, 0.2 mm clearance and 0.35 mm drills, which every fab (PCBWay, JLCPCB, etc.) handles at the cheapest tier. The `compact` board needs 0.2 mm tracks, 0.15 mm clearance and 0.3 mm drills to escape the USB-C, and the `header` board uses the same finer rules to route between its dense interleaved Pico holes - order both from **JLCPCB** (its standard 6/6 process and cheapest tier cover this).

For an **SMD board assembled** by the fab, also supply:

- `BOM-smd.csv`, `BOM-compact.csv` or `BOM-header.csv` - bill of materials
- the matching `-positions.csv` - centroid / pick-and-place file

The fab can place the optocoupler (SOIC-8), the 0805 resistors and the 0805 capacitor (and, on `compact`/`header`, the SMD USB-C receptacle, the two CC resistors and the SOT-23-6 ESD array). The three Pico 2 modules are not standard catalogue parts, so either hand-solder them onto the castellated pads yourself or consign them to the fab. On `smd`/`tht` the USB-A receptacle is a through-hole vertical part, hand-soldered. On `compact` the USB-A is a horizontal edge-mount part with SMD contacts the fab can place plus two through-hole shield posts you solder by hand for mechanical strength. The `compact` board places parts on **both sides** - tell the fab it is a double-sided assembly (Pico B is the only part on the back). On `header` the fab places only the four front-side SMD parts (opto, two resistors, bypass cap); the three Picos are 0.1" through-hole parts you fit yourself, flagged DNP in the Rev0 BOM. There is no host connector - the device plugs into Pico B's micro-USB.

## Bill of materials

See [`BOM-smd.csv`](BOM-smd.csv), [`BOM-tht.csv`](BOM-tht.csv), [`BOM-compact.csv`](BOM-compact.csv) and [`BOM-header.csv`](BOM-header.csv). Summary:

| Ref | Part | SMD | THT | Compact | Header |
| --- | ---- | --- | --- | ------- | ------ |
| A1, A2, A3 | Raspberry Pi Pico 2 (x3) | flat castellated mount | A/Fwd on headers, B flat | flat; B on the back under A | all on 0.1" headers; B on the back, grid offset 8.89 mm |
| U1 | High-speed optocoupler | HCPL-0601 (SO-8) | 6N137 (DIP-8 + socket) | HCPL-0601 (SO-8) | HCPL-0601 (SO-8) |
| R1 | 470 R (opto LED limit) | 0805 | axial 1/4 W | 0805 | 0805 |
| R2 | 680 R (opto VO pull-up) | 0805 | axial 1/4 W | 0805 | 0805 |
| R3, R4 | 56 k (USB-C CC pull-ups) | - | - | 0805 | - |
| C1 | 100 nF (opto bypass) | 0805 | ceramic disc | 0805 | 0805 |
| J1 | USB-A receptacle (host) | through-hole (vertical) | through-hole (vertical) | horizontal edge-mount (SMD + posts) | - |
| J2 | USB-C receptacle (host) | - | - | SMD 16-pin | - |
| U2 | USBLC6-2SC6 USB ESD array | - | - | SOT-23-6 | - |

Suggested MPNs in the CSVs are starting points - confirm against your supplier's stock.

U1 note: the **6N137 has no true SO-8 package** - its surface-mount form is a wide gull-wing DIP (~10 mm span, 2.54 mm pitch) that does not fit an SO-8 land. The SO-8 builds (smd, compact, header) use the **HCPL-0601** (Broadcom; same family, identical pinout, true SO-8; HCPL-0611 is a higher-CMR drop-in). Only the through-hole build uses a real 6N137, in a DIP-8 socket.

### Host connector (compact variant)

The `compact` board carries **both** a USB-A (`J1`) and a USB-C (`J2`) host receptacle, both horizontal edge-mount parts whose openings face the left board edge, wired to the same D+/D- lines on Pico B's native USB. **Populate and use only one** - they share the data lines, so fitting both and plugging a device into each shorts two devices together. The USB-C port is wired as a host (downstream-facing): `R3`/`R4` (56 k) pull CC1/CC2 up to VBUS to advertise default USB power to whatever plugs in. Leave `R3`/`R4` and `J2` unpopulated if you only want USB-A, or leave `J1` off if you only want USB-C.

Both connectors are commonly-stocked cheap parts: the USB-C is the HRO TYPE-C-31-M-12 (LCSC C165948, a JLCPCB basic part), and the USB-A is a standard horizontal Type-A edge-mount (TE 292303-7 footprint; any equivalent fits the same pads). KiCad 10 ships no 3D model for the exact HRO USB-C footprint, so `J2`'s model is remapped in `build_board.py` to a near-identical 16-pin USB-C (`USB_C_Receptacle_GCT_USB4105...`) purely so the 3D viewer shows a body - the pads, gerbers and pick-and-place still come from the real HRO footprint.

### Plug-in header variant

The `header` board replaces every Pico's flat castellated footprint with a pair of 1x20 0.1" through-hole headers, so you solder a straight pin-header strip (easy) instead of reflowing a 40-pad castellated module (hard). Two things make the layout work:

- **Two-row stack, back-side Pico B.** Pico A sits on the front top row, the Forwarder on the front bottom row, and Pico B on the **back** directly behind Pico A. Two through-hole Picos cannot share holes, so Pico B's hole grid is shifted **8.89 mm** (half the 17.78 mm inter-row spacing) along the board so the two grids interleave - the nearest drilled holes of A and B clear each other by ~7.9 mm. The board is 54 x 60 mm.
- **No host connector.** The mouse/keyboard plugs into **Pico B's own micro-USB** (through a micro-USB OTG / host adapter). Pico B is the USB host; its native USB (D-/D+/VBUS/GND) is on that connector, electrically the same lines the `smd`/`tht`/`compact` boards route out to a USB-A/USB-C receptacle. Dropping the on-board receptacle (and the CC pull-ups, ESD array and USB tap header it needed) is what makes this the simplest, smallest variant. Trade-off: Pico B's micro-USB is also its BOOTSEL/flashing port, so unplug the input device when you reflash Pico B.

**Mounting the Picos - Pico B must sit in a socket.** Pico B (`A2`, on the back, stacked behind Pico A) is the one part that *requires* a female 0.1" socket strip (`SKT1` in the BOM, 2 x 1x20). Solder the socket into A2's hole rows from the back and clip its tails flush on the front; Pico B's own pins then plug into the socket body and never pass through the board. This is necessary because Pico A's pins, soldered on the front, protrude ~4.4 mm out the back (6 mm pins through a 1.6 mm board) directly under Pico B's body - the socket lifts Pico B ~8.5 mm clear so A's through-pins stop ~4 mm short of it (and the two pin grids interleave 8.89 mm apart, so the pins themselves never line up either). Pico A and the Forwarder sit on the front with nothing stacked over them, so they may be soldered to plain male pin-headers or socketed for easy swapping - your choice. The PCB is identical either way: a socket reuses the same plated holes, so it has no separate footprint (which is why it does not appear in the 3D render).

Silkscreen on the board names each Pico (Pico A / Pico B (back) / Forwarder), prints the corner pin numbers (1/20/21/40) for each Pico, and carries a one-line signal legend (A<->B serial on GP0-3, opto on A.GP20 / Fwd.GP9, host = Pico B micro-USB).

The fab populates only the four SMD parts (`U1`, `R1`, `R2`, `C1`); the three Picos are flagged DNP in [`header/Rev0_BOM-screen-hopper-header.csv`](header/Rev0_BOM-screen-hopper-header.csv) since you fit them by hand. Every populated MPN in the Rev0 BOM was confirmed to return itself as an exact in-stock match in NextPCB's catalogue, so the no-review automated matcher sources them without substituting a wrong value. Ground is poured per-domain (`GND1` on the host side, `GND2` on the Forwarder side) and floods stop short of the isolation gap; a few inner GND pins the autorouter leaves stranded are tied in with explicit stitch tracks, so the board verifies at 0 unconnected.

## Schematic

Each variant ships an editable KiCad schematic alongside its board:

- `smd/screen-hopper-smd.kicad_sch`
- `tht/screen-hopper-tht.kicad_sch`
- `compact/screen-hopper-compact.kicad_sch`

Upload this `.kicad_sch` to tools that ask for one (e.g. Quilter, which uses it to group related components and assign bypass capacitors). It carries the same netlist as the board, so the connectivity matches. A rendered overview is in [`schematic.svg`](schematic.svg).

## Wiring

Authoritative net list:

| Net | Connections |
| --- | ----------- |
| UART0 (A<->B) | A.GPIO0-B.GPIO1, A.GPIO1-B.GPIO0, A.GPIO2-B.GPIO3, A.GPIO3-B.GPIO2 |
| VBUS1 | A.VBUS, B.VBUS, J1.VBUS (header: no J1 - VBUS1 is just A.VBUS + B.VBUS) |
| GND1 | A.GND, B.GND, J1.GND + shield, B.TP1 (header: just A.GND + B.GND) |
| Opto TX | A.3V3 - R1(470) - U1.2 (anode); U1.3 (cathode) - A.GPIO20 |
| USB host | J1.D- - B.TP2 (USB_DM); J1.D+ - B.TP3 (USB_DP). Header has no on-board host port - the device uses Pico B's own micro-USB |
| ESD (compact only) | U2.I/O1 (pins 1,6) - USB_DP; U2.I/O2 (pins 3,4) - USB_DM; U2.VBUS (5) - VBUS1; U2.GND (2) - GND1 |
| VBUS2 | Fwd.VBUS - U1.8 (VCC) - C1 |
| GND2 | Fwd.GND - U1.5 (GND) - C1 |
| Opto RX | Fwd.3V3 - R2(680) - U1.6 (VO); Fwd.GPIO9 - U1.6 (VO) |

`C1` (100 nF) bypasses the optocoupler's VCC (pin 8) to GND (pin 5) - recommended by the 6N137 datasheet. Optocoupler pin 7 (VE, enable) is left unconnected, matching the working breadboard build (the 6N137's internal pull-down on VE leaves it enabled).

Two deliberate choices, both inherited from the proven breadboard build:

- **`R1` = 470 Ω** sets the opto LED drive to roughly 3.8 mA from the 3.3 V rail. That is slightly below the 6N137's typical 5 mA recommended forward current, but it switches reliably at 1 Mbaud on the breadboard and stays well clear of the LED's 20 mA limit. If you want more margin you can drop to ~330 Ω (about 5.7 mA); leave it at 470 Ω to match the validated design.
- **ESD protection** (`U2`, compact variant only): a USBLC6-2SC6 low-capacitance TVS array sits next to the host connectors and clamps the D+/D- lines to VBUS1/GND1, shunting a static-discharge spike to ground before it reaches Pico B's USB transceiver. It's invisible during normal operation and adds negligible capacitance at full-speed HID rates. It's in domain 1 only and has no effect on the galvanic isolation. The `smd`/`tht` boards omit it (matching the original build); add equivalent protection there yourself if you want it.

## Unplaced board (for external auto-placers)

`make unplaced` writes a board per variant with every footprint loaded and the full netlist assigned, but **no placement and no routing** - the parts are parked in a column just right of an empty board outline:

- `unplaced/screen-hopper-smd-unplaced.kicad_pcb` (95 x 85 mm outline)
- `unplaced/screen-hopper-tht-unplaced.kicad_pcb` (115 x 110 mm outline)
- matching `.kicad_sch` for tools that also want the schematic

Feed one of these to an auto-placement tool, then route it (or let the tool route). Two things the tool won't know about, so check them in the result:

- **Galvanic isolation.** The placer sees `GND1`/`VBUS1` and `GND2`/`VBUS2` as distinct nets but has no reason to keep the two domains physically apart. After placement, confirm domain 2 (the Forwarder `A3`, opto output pins 5/6/8, `C1`, `R2`) sits on one side and everything else on the other, with the optocoupler straddling the boundary and a clear copper gap between - see the isolation section above. The hand-placed boards in `smd/` and `tht/` already do this.
- **Pico B orientation.** Its USB test points (TP1/2/3) must face the USB-A receptacle `J1`.

## Assembly notes

- **Pico B is always SMD-mounted flat**, even on the through-hole board. The USB-A host port wires to Pico B's native USB through its bottom test points (TP1/TP2/TP3 = USB_GND/USB_DM/USB_DP), and only the flat (castellated) footprint exposes those pads.
- **Pico B's micro-USB connector shares the same D+/D- lines as the USB-A port.** Leave Pico B's micro-USB unplugged in normal use - plug it in only to flash Pico B (BOOTSEL).
- **Flashing**: Pico A gets `screenhopper_a.uf2`, Pico B gets `screenhopper_b.uf2`, the Forwarder gets `forwarder.uf2`. Reflash by holding BOOTSEL while connecting that Pico's micro-USB to a computer. Re-upload your config to Pico A afterwards.
- **No ground pour** on `smd`/`tht`/`compact` - ground is routed as copper tracks. For lower EMI you can open the `.kicad_pcb` in KiCad, add a `GND1` zone on the left domain and a `GND2` zone on the right domain (keep them clear of the isolation gap), and refill. The `header` board already pours both domains (the two-row layout strands inner GND pins that pours plus stitch tracks tidy up).
- **No mounting holes** are placed (to avoid accidentally bridging the two ground domains through a metal standoff). Add your own in KiCad if you need them - keep any in the isolation gap non-plated, and don't let a screw or standoff connect domain-1 ground to domain-2 ground.
- **Compact variant is double-sided.** Pico A reflows on the top, Pico B on the back directly beneath it (the only back-side part). Reflow or hand-solder the back first, then the top, or have the fab do both. The USB-C host port (`J2`) and USB-A (`J1`) are mutually exclusive - see [Host connector](#host-connector-compact-variant).

## Regenerating

The boards are generated, routed and verified by scripts in [`lib/`](lib/):

```sh
cd hardware
make all      # build + autoroute + verify + export gerbers for all four variants
make check    # re-run the geometry checks on the built boards
make clean    # delete generated boards and fab files
# or build one: make smd | make tht | make compact | make header
```

Requirements: KiCad 10 (provides the bundled `pcbnew` Python module and `kicad-cli`) and a Java runtime. Download `freerouting.jar` (v2.x, from the freerouting GitHub releases) into `lib/`.

Pipeline: `build_board.py` places the parts and defines the netlist with `pcbnew`, exports a Specctra DSN, the freerouting autorouter routes it, the routes are imported back, and `kicad-cli` exports the gerbers/drill/centroid. `gen_sch.py` then writes the matching `.kicad_sch` and `check_sch.py` verifies its netlist against the same net table.

## Verification

`kicad-cli pcb drc` crashes on this macOS/KiCad 10.0.3 build (a tool bug, reproducible on an empty board), so verification runs through [`lib/check_board.py`](lib/check_board.py), which uses KiCad's own geometry engine to confirm, on every variant:

- **0 unconnected** nets (every pad reaches its net)
- **0 copper-clearance violations** - no different-net copper closer than each variant's design-rule clearance less a 0.02 mm rounding margin (so 0.18 mm for `smd`/`tht` whose rule is 0.2 mm; 0.13 mm for `compact`/`header` whose rule is 0.15 mm)
- **0 copper in the isolation gap** (domains bridged only by the optocoupler)

As a final belt-and-braces step before ordering, open the `.kicad_pcb` in KiCad and run **Inspect > Design Rules Checker** once, and eyeball the board against the wiring table above.
