// Screen Hopper - two-part 3D-printed enclosure for the "compact" triple-Pico board.
//
// Geometry is taken directly from hardware/compact/screen-hopper-compact.kicad_pcb:
//   board outline 80.77 x 58.94 mm (plain rectangle), 1.6 mm thick, no mounting holes.
//   Connectors (board-local, origin at the board's min corner = kicad (2.31, 0.24)).
//   All three Pico micro-USBs are on the SAME edge (front, Y-min); A2 is mounted on
//   the underside directly beneath A1, so its connector sits below the board on that
//   same front edge (verified against a kicad-cli render of both board sides):
//     A1 micro-USB  front edge (Y-min)  X = 29.69   TOP side    (above board)
//     A2 micro-USB  front edge (Y-min)  X = 29.69   BOTTOM side (below board, under A1)
//     A3 micro-USB  front edge (Y-min)  X = 66.69   TOP side    (above board)
//     J2 USB-C      left  edge (X-min)  Y = 40.76   top side
//     J1 USB-A      left  edge (X-min)  Y = 15.76   top side
//   The Pico USB mouths sit ~2 mm inside the board edge, so cutouts are sized for the
//   plug shell to reach through the wall+recess tunnel.
//
// Because A2 hangs below the board, the board drops into the base vertically. Split is
// at ~board-top: the BASE is a shallow tray that holds the board on risers and carries
// A2's (bottom-side) cutout; the LID is a tall cover carrying the four top-side
// connector cutouts and clamping the board onto the risers. On the front wall the A1
// (lid) and A2 (base) openings stack either side of the seam at X = 29.69.
//
// Render a single part for printing:
//   openscad -D 'part="base"' -o base.stl screen-hopper-enclosure.scad
//   openscad -D 'part="lid"'  -o lid.stl  screen-hopper-enclosure.scad
// part="both" (default) shows the assembly; part="exploded" separates the halves.

part = "both";          // "base" | "lid" | "both" | "exploded"

$fn = 64;
eps = 0.02;

/* ---------------- fit / wall parameters (mm) ---------------- */
clr        = 0.2;       // print fit clearance (snap engagement, lid register)
board_clr  = 0.3;       // XY gap around the PCB inside the cavity
wall       = 2.0;       // side-wall thickness
floor_th   = 2.0;       // base floor thickness
top_th     = 2.0;       // lid top thickness

/* ---------------- board ---------------- */
bw = 80.77;             // board width  (X)
bd = 58.94;             // board depth  (Y)
bth = 1.6;              // board thickness

/* ---------------- vertical stack (Z measured from base outer bottom) ---------------- */
bottom_gap = 5.0;       // PCB bottom above base inner floor - clears A2 underside (~3.6)
top_gap    = 7.5;       // PCB top to lid inner ceiling - clears USB-A shell (6.5) + margin

board_bot = floor_th + bottom_gap;     // 7.0
board_top = board_bot + bth;           // 8.6
seam      = board_top - 0.6;           // 8.0  base-wall top / lid joint
ceil_in   = board_top + top_gap;       // 16.1 lid inner ceiling
outer_h   = ceil_in + top_th;          // 18.1 overall outer height

/* ---------------- XY footprint ---------------- */
cav_w = bw + 2*board_clr;
cav_d = bd + 2*board_clr;
outer_w = cav_w + 2*wall;
outer_d = cav_d + 2*wall;

bx0 = wall + board_clr;     // PCB min corner in part coords (2.3, 2.3)
by0 = wall + board_clr;

/* connector centres in part coords (board-local + board origin) */
A1x = bx0 + (32.00 - 2.31);
A3x = bx0 + (69.00 - 2.31);
A2x = bx0 + (32.00 - 2.31);
J2y = by0 + (41.00 - 0.24);
J1y = by0 + (16.00 - 0.24);

/* connector aperture sizes [width-along-wall], generous so the plug shell clears the
   wall+recess tunnel (mouths are ~2 mm inside the board edge) */
mUSB_w = 10.0;          // micro-USB through-cut (plug nose/shell)
usbc_w = 12.0;          // USB-C
usba_w = 15.0;          // USB-A

/* micro-USB ports are recessed ~2.4 mm behind the board edge. To let the plug seat,
   the front wall is thinned to `thin_wall` over a pocket around each micro-USB cutout,
   giving the overmould a lead-in and the metal plug more reach into the socket. */
thin_wall  = 1.0;
mUSB_pkt_w = 14.0;      // pocket width (overmould lead-in)
mUSB_pkt_h = 8.0;       // pocket height

/* connector Z spans (absolute), open toward the seam so parts assemble vertically */
// A2 (bottom side, base FRONT wall): centre ~ board_bot-2.3=4.7, open up to seam.
A2_z0 = floor_th;  A2_z1 = seam + eps;
// top-side (lid walls): open down to the seam, up to just over each connector top.
mUSB_z1 = 13.2;         // A1/A3 micro-USB top (board_top+1.0 pico +2.6 conn +margin)
usbc_z1 = 12.5;         // USB-C top (board_top + 3.26 + margin)
usba_z1 = 15.9;         // USB-A top (board_top + 6.5 + margin)

/* ---------------- support / clamp posts (bare-FR4 spots, clear top & bottom) ----------------
   board-local XY; risers sit under them (z floor..board_bot), lid ribs above (board_top..ceil). */
post_xy = [[10,2],[48,2],[74,2],[10,57],[48,57],[74,57]];
post = 4.0;             // square post side

/* ---------------- snap clips ----------------
   {face, pos} on connector-free wall spots. Cantilever arm on the lid hooks a window
   in the base wall. */
snap_w   = 9.0;         // clip width
arm_t    = 1.8;         // cantilever arm thickness
barb     = 1.5;         // barb engagement depth
win_z0   = 3.6;         // base window bottom
win_z1   = 5.6;         // base window top (barb hooks under this edge)
arm_gap  = clr;         // gap between arm inner face and base wall outer face

snaps = [
  ["front", (A1x+A3x)/2],
  ["back",  outer_w*0.66],
  ["left",  (J1y+J2y)/2],
  ["right", outer_d/2],
];

/* ==================================================================== */
/* helpers                                                              */
/* ==================================================================== */

// Cut a rectangular aperture through one wall, between z0 and z1.
module wall_cut(face, pos, width, z0, z1) {
  d = wall + 2;  h = z1 - z0;
  if (face == "front")
    translate([pos - width/2, -1,           z0]) cube([width, d, h]);
  else if (face == "back")
    translate([pos - width/2, outer_d-d+1,  z0]) cube([width, d, h]);
  else if (face == "left")
    translate([-1,           pos - width/2, z0]) cube([d, width, h]);
  else if (face == "right")
    translate([outer_w-d+1,  pos - width/2, z0]) cube([d, width, h]);
}

// Thin a wall to `thin_wall` over a pocket (removes the OUTER face material), giving a
// recessed connector port a lead-in. Leaves `thin_wall` of material on the inner side.
module wall_pocket(face, pos, width, z0, z1) {
  rd = wall - thin_wall + 1;  h = z1 - z0;          // outer depth removed (+1 overrun)
  if (face == "front")
    translate([pos - width/2, -1, z0]) cube([width, rd, h]);
  else if (face == "back")
    translate([pos - width/2, outer_d - rd + 1, z0]) cube([width, rd, h]);
  else if (face == "left")
    translate([-1, pos - width/2, z0]) cube([rd, width, h]);
  else if (face == "right")
    translate([outer_w - rd + 1, pos - width/2, z0]) cube([rd, width, h]);
}

/* ==================================================================== */
/* BASE                                                                 */
/* ==================================================================== */
module base() {
  difference() {
    union() {
      // outer shell up to the seam
      cube([outer_w, outer_d, seam]);
    }
    // hollow cavity (above the floor, inside the walls), open at the top
    translate([wall, wall, floor_th])
      cube([cav_w, cav_d, seam - floor_th + eps]);
    // A2 connector aperture (FRONT wall, bottom side, under A1), open to seam so the
    // board drops in. Stacks below A1's lid opening at the same X.
    wall_cut("front", A2x, mUSB_w, A2_z0, A2_z1);
    wall_pocket("front", A2x, mUSB_pkt_w, floor_th, seam);   // recess lead-in for A2
    // snap windows
    for (s = snaps) snap_window(s[0], s[1]);
  }
  // risers the PCB rests on
  for (p = post_xy)
    translate([bx0 + p[0] - post/2, by0 + p[1] - post/2, floor_th])
      cube([post, post, bottom_gap]);
}

// rectangular through-window in the base wall for a snap barb (clearance each side)
module snap_window(face, pos) {
  wall_cut(face, pos, snap_w + 2*clr, win_z0, win_z1);
}

/* ==================================================================== */
/* LID                                                                  */
/* ==================================================================== */
module lid() {
  difference() {
    union() {
      // top plate
      translate([0, 0, ceil_in]) cube([outer_w, outer_d, top_th]);
      // side walls from seam up to the ceiling
      difference() {
        cube([outer_w, outer_d, ceil_in + eps]);
        translate([wall, wall, -1]) cube([cav_w, cav_d, ceil_in + 2]);
        // remove everything below the seam (that volume belongs to the base)
        translate([-1, -1, -1]) cube([outer_w+2, outer_d+2, seam + 1]);
      }
    }
    // top-side connector apertures (open at the seam = lid wall bottom)
    wall_cut("front", A1x, mUSB_w, seam - eps, mUSB_z1);   // A1 micro-USB
    wall_cut("front", A3x, mUSB_w, seam - eps, mUSB_z1);   // A3 micro-USB
    wall_cut("left",  J2y, usbc_w, seam - eps, usbc_z1);   // J2 USB-C
    wall_cut("left",  J1y, usba_w, seam - eps, usba_z1);   // J1 USB-A
    // recess lead-ins for the (recessed) micro-USB ports
    wall_pocket("front", A1x, mUSB_pkt_w, seam, mUSB_z1 + 1);
    wall_pocket("front", A3x, mUSB_pkt_w, seam, mUSB_z1 + 1);
  }
  // clamp ribs press the PCB top onto the risers
  for (p = post_xy)
    translate([bx0 + p[0] - post/2, by0 + p[1] - post/2, board_top])
      cube([post, post, ceil_in - board_top]);
  // snap arms
  for (s = snaps) snap_arm(s[0], s[1]);
}

// Cantilever snap arm hanging from the lid, hooking a window in the base wall.
// Built in a canonical frame (wall outer face at Y=0, hanging in -Y, centred at X=0)
// then rotated/translated onto the requested face.
module snap_arm(face, pos) {
  if (face == "front")      translate([pos, 0,       0])               snap_arm_canon();
  else if (face == "back")  translate([pos, outer_d, 0]) rotate([0,0,180]) snap_arm_canon();
  else if (face == "left")  translate([0,   pos,     0]) rotate([0,0,-90]) snap_arm_canon();
  else if (face == "right") translate([outer_w, pos, 0]) rotate([0,0,90])  snap_arm_canon();
}

// Canonical arm: outer wall face at Y=0, material inward (+Y), arm hangs in -Y.
module snap_arm_canon() {
  arm_top = seam + 1.0;          // anchored slightly up into the lid wall
  arm_bot = win_z0 - 1.0;        // reaches below the window
  arm_h   = arm_top - arm_bot;
  // anchor: fuses the arm root to the lid wall over the seam..arm_top band
  translate([-snap_w/2, -(arm_gap + arm_t), seam])
    cube([snap_w, arm_gap + arm_t + 0.8, arm_top - seam]);
  // flexing slab, just outside the wall
  translate([-snap_w/2, -(arm_gap + arm_t), arm_bot])
    cube([snap_w, arm_t, arm_h]);
  // barb hook: from the arm inward into the window, hooking under its top edge
  translate([-snap_w/2, -arm_gap, win_z0])
    cube([snap_w, arm_gap + barb, win_z1 - win_z0]);
  // lead-in ramp under the barb (cams the arm outward during assembly)
  hull() {
    translate([-snap_w/2, -arm_gap, win_z0]) cube([snap_w, arm_gap + barb, eps]);
    translate([-snap_w/2, -arm_gap, win_z0 - (arm_gap + barb)]) cube([snap_w, eps, eps]);
  }
}

/* ==================================================================== */
/* assembly                                                             */
/* ==================================================================== */
module board_ghost() {
  color([0.1,0.5,0.2,0.35])
    translate([bx0, by0, board_bot]) cube([bw, bd, bth]);
}

if (part == "base") base();
else if (part == "lid") lid();
else if (part == "exploded") {
  base();
  board_ghost();
  translate([0, 0, 30]) lid();
} else {
  base();
  board_ghost();
  color([0.7,0.7,0.75,0.55]) lid();
}
