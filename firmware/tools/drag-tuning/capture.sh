#!/bin/bash
# Capture one drag: run the host-position trace and the firmware cursor sampler
# together for DUR seconds, then analyse with merge.py.
#
#   source <(./symbols.sh)     # refresh RAM addresses after a rebuild
#   ./capture.sh [dur]         # default 10s; do ONE drag-and-hold during the window
#
# Procedure: press -> drag -> release -> HOLD STILL. Holding still leaves the
# post-release warp armed (defer=1) but unfired, so merge.py reads its exact
# magnitude without triggering the jump. Must run UNSANDBOXED (probe + WindowServer).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DUR="${1:-10}"
export DRAG_DIR="${DRAG_DIR:-${TMPDIR:-/tmp}/sh-drag}"
mkdir -p "$DRAG_DIR"

pkill -f "openocd.*rp2350" 2>/dev/null; sleep 0.3
# host trace (swift compiles ~2-3s before it starts sampling)
swift "$DIR/cursorlog.swift" "$DUR" "$DRAG_DIR/host_trace.csv" &
SW=$!
# firmware cursor sampler (spawns its own openocd, ~1-2s init)
python3 "$DIR/sampler.py" "$DUR" "$DRAG_DIR/fw_trace.csv"
wait $SW 2>/dev/null
echo "--- merge ---"
python3 "$DIR/merge.py" "$DRAG_DIR/fw_trace.csv" "$DRAG_DIR/host_trace.csv"
