#!/usr/bin/env bash
# Build, autoroute, verify and export fab files for one board variant.
# Usage: pipeline.sh <smd|tht|compact>
set -euo pipefail

VARIANT="${1:?usage: pipeline.sh <smd|tht|compact>}"
HW="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$HW/lib"
OUT="$HW/$VARIANT"
WORK="${TMPDIR:-/tmp}/sh_build"
NAME="screen-hopper-$VARIANT"

KICAD_APP="/Applications/KiCad/KiCad.app/Contents"
PY="$KICAD_APP/Frameworks/Python.framework/Versions/Current/bin/python3"
CLI="$KICAD_APP/MacOS/kicad-cli"
JAVA="$(command -v java || echo /opt/homebrew/opt/openjdk/bin/java)"
FR="$LIB/freerouting.jar"

mkdir -p "$WORK" "$OUT/gerbers" "$WORK/frtmp"

echo ">> build $VARIANT"
BUILD_OUT=$("$PY" -u "$LIB/build_board.py" build "$VARIANT" \
        "$WORK/${NAME}_unrouted.kicad_pcb" "$WORK/$NAME.dsn" 2>/dev/null)
GAP=$(echo "$BUILD_OUT" | grep -oE 'iso_gap=[0-9.]+,[0-9.]+' | cut -d= -f2)
CLR=$(echo "$BUILD_OUT" | grep -oE 'clr=[0-9.]+' | cut -d= -f2)
AXIS=$(echo "$BUILD_OUT" | grep -oE 'iso_axis=[xy]' | cut -d= -f2); AXIS="${AXIS:-x}"
echo "   isolation gap: $GAP mm ($AXIS-axis)   clearance: $CLR mm"

echo ">> autoroute (freerouting)"
"$JAVA" -Djava.io.tmpdir="$WORK/frtmp" -Djava.awt.headless=true -jar "$FR" \
        -de "$WORK/$NAME.dsn" -do "$WORK/$NAME.ses" -mp 100 2>&1 \
        | grep -iE 'session completed' | sed 's/.*started/   started/'

echo ">> import routes"
# Header variant floods per-domain GND pours after routing (pass iso gap + axis);
# the others route GND as tracks (no extra args).
POUR=""; [ "$VARIANT" = "header" ] && POUR="${GAP%,*} ${GAP#*,} $AXIS"
"$PY" -u "$LIB/build_board.py" finalize \
        "$WORK/${NAME}_unrouted.kicad_pcb" "$WORK/$NAME.ses" \
        "$OUT/$NAME.kicad_pcb" $POUR 2>/dev/null | grep -iE 'finalized'

echo ">> verify"
"$PY" "$LIB/check_board.py" "$OUT/$NAME.kicad_pcb" "${GAP%,*}" "${GAP#*,}" "$CLR" "$AXIS" 2>/dev/null

echo ">> export gerbers + drill + centroid"
"$CLI" pcb export gerbers --no-protel-ext --layers \
  "F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,F.Paste,B.Paste,Edge.Cuts" \
  -o "$OUT/gerbers/" "$OUT/$NAME.kicad_pcb" >/dev/null 2>&1
"$CLI" pcb export drill --format excellon --drill-origin absolute \
  --excellon-units mm --generate-map --map-format gerberx2 \
  -o "$OUT/gerbers/" "$OUT/$NAME.kicad_pcb" >/dev/null 2>&1
"$CLI" pcb export pos --format csv --units mm --side both \
  -o "$OUT/$NAME-positions.csv" "$OUT/$NAME.kicad_pcb" >/dev/null 2>&1

( cd "$OUT" && rm -f "$NAME-gerbers.zip" && zip -qrj "$NAME-gerbers.zip" gerbers/ )

echo ">> schematic"
"$PY" "$LIB/gen_sch.py" "$VARIANT" "$OUT/$NAME.kicad_sch" >/dev/null
"$PY" "$LIB/check_sch.py" "$OUT/$NAME.kicad_sch" 2>/dev/null | tail -1

# Documentation preview (optional - needs rsvg-convert; not a fab file).
if command -v rsvg-convert >/dev/null 2>&1; then
  "$CLI" pcb export svg --layers "F.Cu,F.Silkscreen,Edge.Cuts" \
    --page-size-mode 2 --exclude-drawing-sheet \
    -o "$WORK/$NAME-prev.svg" "$OUT/$NAME.kicad_pcb" >/dev/null 2>&1
  rsvg-convert -w 1000 -b white "$WORK/$NAME-prev.svg" -o "$OUT/preview-top.png" 2>/dev/null
else
  echo "   (skipping preview-top.png: rsvg-convert not found)"
fi

echo ">> done: $OUT/$NAME.kicad_pcb + $NAME-gerbers.zip + positions.csv"
