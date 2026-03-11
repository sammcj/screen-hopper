#!/usr/bin/env python3
"""Bake a JSON config (single-profile or multi-profile) into a C header the
firmware loads as its default.

Produces the exact 4096-byte flash-sector image that `persist_config()` would
write: a `device_persist_header_t` followed by `profile_slot_t[NPROFILES]`
followed by zero padding and a trailing CRC32. `load_config()` validates and
loads it with the same checksum / version checks it uses for the real flash
sector, so a freshly flashed board comes up with this config without needing
set_config.py.

Two input shapes are accepted:

  Single profile (legacy / one-config use):
    {"version": 8, "screens": [...], "mappings": [...], ...}
    -> baked as profile 0, profile_count = 1, active_profile = 0.

  Multi-profile (new):
    {"version": 8, "active_profile": 0,
     "profiles": [ {...}, {...}, ... ]}
    -> baked as profiles 0..len-1, profile_count = len(profiles).

Usage:
    bake_config.py <config.json>   > src/baked_config.h
    bake_config.py --empty         > src/baked_config.h   (no baked config)
"""

import binascii
import json
import struct
import sys

CONFIG_VERSION = 11
NSCREENS = 6
NPROFILES = 4
MAX_MAPPINGS_PER_PROFILE = 32
FLASH_SECTOR_SIZE = 4096

UNMAPPED_PASSTHROUGH_FLAG = 0x01
STICKY_FLAG = 0x01

# Layout sizes (must match firmware structs in types.h):
#   persist_config_t  = 26 header bytes + NSCREENS * 25 screen bytes      = 176
#   mapping_config_t  = 14
#   profile_slot_t    = persist_config_t + MAX_MAPPINGS_PER_PROFILE * 14  = 624
#   device_header_t   = 16
#   Total payload     = 16 + NPROFILES * 624                              = 2512
#   Sector            = FLASH_SECTOR_SIZE (4096), CRC at last 4 bytes
PERSIST_CONFIG_HEADER_SIZE = 26
SCREEN_DEF_SIZE = 25
PERSIST_CONFIG_T_SIZE = PERSIST_CONFIG_HEADER_SIZE + NSCREENS * SCREEN_DEF_SIZE
MAPPING_CONFIG_T_SIZE = 14
PROFILE_SLOT_T_SIZE = PERSIST_CONFIG_T_SIZE + MAX_MAPPINGS_PER_PROFILE * MAPPING_CONFIG_T_SIZE
DEVICE_HEADER_SIZE = 16


def _profile_header_bytes(profile):
    """Pack the persist_config_t header (26 bytes) and the 6 screen entries
    (NSCREENS * 25 bytes) for a single profile dict in firmware-struct order."""
    flags = UNMAPPED_PASSTHROUGH_FLAG if profile.get("unmapped_passthrough", True) else 0
    mappings = profile.get("mappings", [])
    screens = profile.get("screens", [])

    if len(mappings) > MAX_MAPPINGS_PER_PROFILE:
        raise SystemExit(
            f"profile has {len(mappings)} mappings, exceeds MAX_MAPPINGS_PER_PROFILE={MAX_MAPPINGS_PER_PROFILE}"
        )

    buf = struct.pack(
        "<BBLLBbLLLH",
        CONFIG_VERSION,
        flags,
        profile.get("partial_scroll_timeout", 1000000),
        len(mappings),
        profile.get("interval_override", 0),
        profile.get("constraint_mode", 0),
        profile.get("offscreen_sensitivity", 1000),
        profile.get("coord_scale", 0),
        profile.get("edge_resistance", 0),
        profile.get("jiggle_interval", 0),
    )

    for i in range(NSCREENS):
        if i < len(screens):
            s = screens[i]
            # sensitivity, scale, drag_gain, drag_curve_k are uint16 (CONFIG_VERSION
            # 10); reject values that would silently truncate when packed.
            sensitivity = s.get("sensitivity", 1000)
            scale = s.get("scale", 0)
            drag_gain = s.get("drag_gain", 1000)
            drag_curve_k = s.get("drag_curve_k", 0)
            for name, val in (("sensitivity", sensitivity), ("scale", scale),
                              ("drag_gain", drag_gain), ("drag_curve_k", drag_curve_k)):
                if not 0 <= val <= 0xFFFF:
                    raise SystemExit(f"screen {i} {name}={val} out of uint16 range (0..65535)")
            buf += struct.pack(
                "<LLLLHBHHH",
                s["x"], s["y"], s["w"], s["h"],
                sensitivity,
                s.get("output", 0),
                scale,
                drag_gain,
                drag_curve_k,
            )
        else:
            buf += struct.pack("<LLLLHBHHH", 0, 0, 0, 0, 0, 0, 0, 0, 0)

    assert len(buf) == PERSIST_CONFIG_T_SIZE, (len(buf), PERSIST_CONFIG_T_SIZE)
    return buf


def _profile_mappings_bytes(profile):
    """Pack the fixed-capacity mappings[] array for one profile slot."""
    mappings = profile.get("mappings", [])
    buf = b""
    for m in mappings:
        buf += struct.pack(
            "<LLlBB",
            int(m["target_usage"], 16),
            int(m["source_usage"], 16),
            m.get("scaling", 1000),
            m.get("layer", 0),
            STICKY_FLAG if m.get("sticky", False) else 0,
        )
    # Pad the mapping array out to its fixed capacity so subsequent slots are
    # at predictable offsets.
    buf = buf.ljust(MAX_MAPPINGS_PER_PROFILE * MAPPING_CONFIG_T_SIZE, b"\x00")
    return buf


def _empty_profile_slot_bytes():
    return _profile_header_bytes({}) + _profile_mappings_bytes({})


def _normalise(config):
    """Return (active_profile, profile_count, profiles[NPROFILES]).

    A single-profile JSON gets wrapped into a one-element profiles list; a
    multi-profile JSON is taken as-is. The result is always padded to
    NPROFILES so callers can index any slot.
    """
    if "profiles" in config and isinstance(config["profiles"], list):
        profiles = list(config["profiles"])
        active = int(config.get("active_profile", 0))
    else:
        # Legacy single-profile shape: the top-level object IS the profile.
        profiles = [config]
        active = 0

    if len(profiles) == 0:
        raise SystemExit("at least one profile is required")
    if len(profiles) > NPROFILES:
        raise SystemExit(f"too many profiles: {len(profiles)} > {NPROFILES}")

    profile_count = len(profiles)
    if active >= profile_count:
        active = 0

    return active, profile_count, profiles


def build_sector(config):
    active, profile_count, profiles = _normalise(config)

    # device_persist_header_t: version, flags, active_profile, profile_count,
    # then 12 reserved bytes.
    buf = struct.pack(
        "<BBBB12s",
        CONFIG_VERSION,
        0,            # device flags reserved
        active,
        profile_count,
        b"\x00" * 12,
    )

    for i in range(NPROFILES):
        if i < len(profiles):
            buf += _profile_header_bytes(profiles[i])
            buf += _profile_mappings_bytes(profiles[i])
        else:
            buf += _empty_profile_slot_bytes()

    expected = DEVICE_HEADER_SIZE + NPROFILES * PROFILE_SLOT_T_SIZE
    if len(buf) != expected:
        raise SystemExit(f"internal layout mismatch: built {len(buf)} bytes, expected {expected}")
    if len(buf) > FLASH_SECTOR_SIZE - 4:
        raise SystemExit(f"config too large: {len(buf)} bytes > {FLASH_SECTOR_SIZE - 4}")

    buf = buf.ljust(FLASH_SECTOR_SIZE - 4, b"\x00")
    buf += struct.pack("<L", binascii.crc32(buf))
    return buf


def emit_header(sector):
    lines = [
        "// Generated by config-tool/bake_config.py - do not edit.",
        "#ifndef _BAKED_CONFIG_H_",
        "#define _BAKED_CONFIG_H_",
        "",
        "#include <stdint.h>",
        "",
    ]
    if sector is None:
        lines += ["#define HAVE_BAKED_CONFIG 0", "", "#endif", ""]
        return "\n".join(lines)

    lines += [
        "#define HAVE_BAKED_CONFIG 1",
        "",
        f"const uint8_t baked_config[{len(sector)}] = {{",
    ]
    for i in range(0, len(sector), 16):
        chunk = ", ".join(f"0x{b:02x}" for b in sector[i:i + 16])
        lines.append(f"    {chunk},")
    lines += ["};", "", "#endif", ""]
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "--empty":
        sys.stdout.write(emit_header(None))
        return
    with open(sys.argv[1]) as f:
        config = json.load(f)
    sys.stdout.write(emit_header(build_sector(config)))


if __name__ == "__main__":
    main()
