#!/bin/bash
# Print the Pico A RAM addresses the probe scripts read, extracted from the ELF.
# These addresses SHIFT on every firmware rebuild, so re-run this (and re-source
# its output) after any `make flash TARGET=a` before capturing.
#
#   source <(./symbols.sh)            # export ADDR_CURSOR / ADDR_ACTIVE / ADDR_DEFER
#   ./symbols.sh /path/to/other.elf   # override the ELF
#
# Needs arm-none-eabi-nm (brew install arm-none-eabi-binutils).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ELF="${1:-${ELF:-$DIR/../../build-pico2/screenhopper_a.elf}}"

if [[ ! -f "$ELF" ]]; then
    echo "ELF not found: $ELF" >&2
    echo "build first (cd firmware && make pico2) or pass the path" >&2
    exit 1
fi

NM="$(command -v arm-none-eabi-nm || command -v llvm-nm || command -v nm)"
sym() { "$NM" "$ELF" | awk -v s="$1" '$3==s {print "0x"$1}'; }

cx="$(sym cursor_x)"; asc="$(sym active_screen)"; def="$(sym defer_abs_after_drag)"
[[ -z "$cx" || -z "$asc" || -z "$def" ]] && { echo "missing symbols in $ELF" >&2; exit 1; }

echo "export ADDR_CURSOR=$cx"   # int64 cursor_x then int64 cursor_y (contiguous)
echo "export ADDR_ACTIVE=$asc"  # int8 active_screen
echo "export ADDR_DEFER=$def"   # uint8 defer_abs_after_drag (1 = post-release warp armed)
