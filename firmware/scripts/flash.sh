#!/usr/bin/env bash
# Flash a built Pico firmware image to the connected board.
#
# Uses picotool when a BOOTSEL device (volume) is detected, falling back to the
# Raspberry Pi Debug Probe over SWD via openocd. After flashing Pico A (or the
# single-Pico build), if CONFIG= was passed, pushes the config via set_config.py
# so the new layout overrides any persisted config in flash (which load_config()
# prefers over the baked default).
#
# Usage:
#   ./scripts/flash.sh <target> [board] [config_json]
#     target      = a | b | forwarder | single
#     board       = pico1 | pico2 (default: pico2)
#     config_json = path to JSON to apply after flash (only for a/single)
#
# Neither path can tell A from B from forwarder - the wiring is up to you, so
# the script asks for confirmation before writing.

set -euo pipefail

TARGET="${1:?usage: flash.sh <a|b|forwarder|single> [pico1|pico2] [config.json]}"
BOARD="${2:-pico2}"
CONFIG_JSON="${3:-}"

case "$TARGET" in
    a)         UF2_NAME=screenhopper_a.uf2 ; ELF_NAME=screenhopper_a.elf ; LABEL="Pico A (USB device to main computer)" ;;
    b)         UF2_NAME=screenhopper_b.uf2 ; ELF_NAME=screenhopper_b.elf ; LABEL="Pico B (USB host for mouse/keyboard)" ;;
    forwarder) UF2_NAME=forwarder.uf2      ; ELF_NAME=forwarder.elf      ; LABEL="Forwarder (USB device to secondary machine)" ;;
    single)    UF2_NAME=screenhopper.uf2   ; ELF_NAME=screenhopper.elf   ; LABEL="Single-Pico (combined host+device)" ;;
    *) echo "Unknown target: $TARGET" >&2; exit 2 ;;
esac

case "$BOARD" in
    pico1) BOOTSEL_VOLUME=/Volumes/RPI-RP2 ; OPENOCD_TARGET=target/rp2040.cfg ;;
    pico2) BOOTSEL_VOLUME=/Volumes/RP2350  ; OPENOCD_TARGET=target/rp2350.cfg ;;
    *) echo "Unknown board: $BOARD" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UF2_PATH="$SCRIPT_DIR/../$BOARD/$UF2_NAME"
ELF_PATH="$SCRIPT_DIR/../build-$BOARD/$ELF_NAME"

if [[ ! -f "$UF2_PATH" ]]; then
    echo "Missing UF2: $UF2_PATH" >&2
    echo "Build first: make $BOARD" >&2
    exit 1
fi

confirm() {
    local prompt="$1"
    local reply=""
    # Honour CONFIRM=y / FLASH_YES=1 for unattended runs (e.g. when the
    # interactive prompt is being eaten by some surrounding wrapper).
    if [[ "${CONFIRM:-}" =~ ^[Yy]$ || "${FLASH_YES:-}" == "1" ]]; then
        return 0
    fi
    # Route BOTH the prompt and the read through /dev/tty so they share the
    # same channel as the user's terminal. Going via stderr broke under some
    # make/zsh wrappers - the prompt vanished but the read still blocked or
    # silently EOF'd, leaving the user staring at a hung-looking script.
    if [[ ! -e /dev/tty ]]; then
        echo "No /dev/tty available; pass CONFIRM=y to bypass confirm." >&2
        return 1
    fi
    printf '\n%s [y/N] ' "$prompt" >/dev/tty
    read -r reply </dev/tty || reply=""
    [[ "$reply" =~ ^[Yy]$ ]]
}

flash_via_volume() {
    echo "BOOTSEL volume detected: $BOOTSEL_VOLUME"
    if ! confirm "About to flash $LABEL. Is that the board currently in BOOTSEL?"; then
        echo "Aborted." >&2
        exit 1
    fi
    if command -v picotool >/dev/null 2>&1; then
        # On macOS the kernel's USB Mass Storage driver claims the BOOTSEL
        # device as soon as the volume mounts, which makes picotool fail with
        # "No accessible RP-series devices in BOOTSEL mode were found." Detach
        # the volume first so picotool can claim the underlying USB endpoint.
        # The board stays in BOOTSEL throughout (only its filesystem view goes
        # away), so picotool still sees it. After flashing, picotool's -x
        # reboots into the new firmware and the volume disappears for good.
        if [[ "$(uname -s)" == "Darwin" && -d "$BOOTSEL_VOLUME" ]]; then
            echo "Unmounting $BOOTSEL_VOLUME so picotool can claim the USB device ..."
            diskutil unmount "$BOOTSEL_VOLUME" >/dev/null 2>&1 || diskutil unmountDisk "$BOOTSEL_VOLUME" >/dev/null 2>&1 || true
        fi
        echo "Loading via picotool ..."
        if picotool load -x "$UF2_PATH"; then
            echo "Flashed."
            return
        fi
        echo "picotool failed; falling back to volume copy ..." >&2
        # If picotool failed (e.g. permissions, USB still locked), the volume
        # may still be mounted - the BOOTSEL device automounts unless we
        # successfully unmounted above.
        if [[ ! -d "$BOOTSEL_VOLUME" ]]; then
            echo "Re-attach the BOOTSEL device (hold BOOTSEL while replugging) and re-run." >&2
            exit 1
        fi
    else
        echo "picotool not found; using volume copy ..."
    fi
    cp "$UF2_PATH" "$BOOTSEL_VOLUME/"
    sync
    echo "Flashed."
}

flash_via_probe() {
    if ! command -v openocd >/dev/null 2>&1; then
        echo "openocd not found and no BOOTSEL volume. Install openocd or hold BOOTSEL and replug." >&2
        exit 1
    fi
    if [[ ! -f "$ELF_PATH" ]]; then
        echo "Missing ELF for probe flash: $ELF_PATH" >&2
        echo "Build first: make $BOARD" >&2
        exit 1
    fi
    echo "No BOOTSEL volume found. Falling back to Raspberry Pi Debug Probe (SWD)."
    if ! confirm "About to flash $LABEL via the Debug Probe. Is SWD wired to that board?"; then
        echo "Aborted." >&2
        exit 1
    fi
    echo "Running openocd ..."
    openocd \
        -f interface/cmsis-dap.cfg \
        -f "$OPENOCD_TARGET" \
        -c "adapter speed 5000" \
        -c "program \"$ELF_PATH\" verify reset exit"
    echo "Flashed via Debug Probe."
}

# Push the JSON config via USB HID so the new firmware uses it - load_config()
# prefers the persisted-in-flash config over the baked default, so a fresh
# flash on a board with an old config still loads the old one. Pushing here
# overwrites the persisted sector with the new layout, applies it live, and
# saves it for the next boot.
apply_config() {
    if [[ -z "$CONFIG_JSON" ]]; then
        return
    fi
    case "$TARGET" in
        a|single) ;;
        *)
            echo "Skipping config apply: target $TARGET has no config HID endpoint."
            return
            ;;
    esac
    if [[ ! -f "$CONFIG_JSON" ]]; then
        echo "Config file not found: $CONFIG_JSON" >&2
        exit 1
    fi
    echo "Waiting for device to re-enumerate ..."
    for _ in $(seq 1 30); do
        if python3 -c 'import hid; hid.Device(0xCAFE, 0xBAF3).close()' 2>/dev/null; then
            break
        fi
        sleep 0.5
    done
    if ! python3 -c 'import hid; hid.Device(0xCAFE, 0xBAF3).close()' 2>/dev/null; then
        echo "Device did not re-enumerate within 15s; skipping config apply." >&2
        echo "You can apply manually later: python3 ../config-tool/set_config.py < $CONFIG_JSON" >&2
        return
    fi
    echo "Applying config from $CONFIG_JSON ..."
    python3 "$SCRIPT_DIR/../../config-tool/set_config.py" < "$CONFIG_JSON"
    echo "Config applied and persisted."
}

if [[ -d "$BOOTSEL_VOLUME" ]]; then
    flash_via_volume
else
    flash_via_probe
fi

apply_config
