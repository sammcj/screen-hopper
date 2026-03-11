#!/usr/bin/env python3
"""Push a config (single-profile or multi-profile) to a running Pico A over USB HID.

Accepts the same two shapes as bake_config.py:

  Single profile (legacy):
    {"version": 8, "screens": [...], "mappings": [...], ...}
    -> written to slot 0; profile_count is set to 1; active_profile = 0.

  Multi-profile (new):
    {"version": 8, "active_profile": 0,
     "profiles": [ {...}, {...}, ... ]}
    -> each profile written to its slot; profile_count = len(profiles);
       active_profile set from the JSON.

Reads JSON from stdin (matching the original interface).
"""

import sys
import hid
import binascii
import struct
import json

VENDOR_ID = 0xCAFE
PRODUCT_ID = 0xBAF3

CONFIG_VERSION = 11
CONFIG_SIZE = 32
REPORT_ID_CONFIG = 100

# ConfigCommand enum values (keep in sync with firmware/src/types.h).
SET_CONFIG = 2
CLEAR_MAPPING = 4
ADD_MAPPING = 5
PERSIST_CONFIG = 7
SUSPEND = 10
RESUME = 11
SET_SCREEN = 12
SELECT_PROFILE = 14
SET_ACTIVE_PROFILE = 15
SET_PROFILE_COUNT = 16

UNMAPPED_PASSTHROUGH_FLAG = 0x01
STICKY_FLAG = 0x01

NSCREENS = 6
NPROFILES = 4
MAX_MAPPINGS_PER_PROFILE = 32


def add_crc(buf):
    return buf + struct.pack("<L", binascii.crc32(buf[1:]))


def send(device, command, payload=b""):
    """Send one SET feature report carrying `command` plus up to 26 bytes of
    payload, padded with zeros to the fixed CONFIG_SIZE before the CRC."""
    if len(payload) > 26:
        raise SystemExit(f"payload too large for command {command}: {len(payload)} > 26")
    payload = payload.ljust(26, b"\x00")
    data = struct.pack("<BBB", REPORT_ID_CONFIG, CONFIG_VERSION, command) + payload
    device.send_feature_report(add_crc(data))


def push_profile(device, slot, profile):
    """Stream one profile's worth of config into the given slot."""
    if len(profile.get("mappings", [])) > MAX_MAPPINGS_PER_PROFILE:
        raise SystemExit(
            f"profile {slot} has {len(profile.get('mappings', []))} mappings; "
            f"limit is {MAX_MAPPINGS_PER_PROFILE}"
        )

    send(device, SELECT_PROFILE, struct.pack("<B", slot))

    flags = UNMAPPED_PASSTHROUGH_FLAG if profile.get("unmapped_passthrough", True) else 0
    set_config_payload = struct.pack(
        "<BLBBLLLH",
        flags,
        profile.get("partial_scroll_timeout", 1000000),
        profile.get("interval_override", 0),
        profile.get("constraint_mode", 0),
        profile.get("offscreen_sensitivity", 1000),
        profile.get("coord_scale", 0),
        profile.get("edge_resistance", 0),
        profile.get("jiggle_interval", 0),
    )
    send(device, SET_CONFIG, set_config_payload)

    send(device, CLEAR_MAPPING)
    for mapping in profile.get("mappings", []):
        target_usage = int(mapping["target_usage"], 16)
        source_usage = int(mapping["source_usage"], 16)
        scaling = mapping.get("scaling", 1000)
        layer = mapping.get("layer", 0)
        sticky_flag = STICKY_FLAG if mapping.get("sticky", False) else 0
        payload = struct.pack("<LLlBB", target_usage, source_usage, scaling, layer, sticky_flag)
        send(device, ADD_MAPPING, payload)

    for i, screen in enumerate(profile.get("screens", [])):
        if i >= NSCREENS:
            break
        # sensitivity, scale, drag_gain and drag_curve_k are uint16 (CONFIG_VERSION
        # 10); realistic values fit, but reject anything that would truncate.
        sensitivity = screen.get("sensitivity", 1000)
        scale = screen.get("scale", 0)
        drag_gain = screen.get("drag_gain", 1000)
        drag_curve_k = screen.get("drag_curve_k", 0)
        for name, val in (("sensitivity", sensitivity), ("scale", scale),
                          ("drag_gain", drag_gain), ("drag_curve_k", drag_curve_k)):
            if not 0 <= val <= 0xFFFF:
                raise SystemExit(f"screen {i} {name}={val} out of uint16 range (0..65535)")
        payload = struct.pack(
            "<BLLLLHBHHH",
            i,
            screen["x"],
            screen["y"],
            screen["w"],
            screen["h"],
            sensitivity,
            screen.get("output", 0),
            scale,
            drag_gain,
            drag_curve_k,
        )
        send(device, SET_SCREEN, payload)
    # Zero out any unused screen slots so a profile that uses fewer screens
    # doesn't inherit stale geometry from whatever was previously in this slot.
    for i in range(len(profile.get("screens", [])), NSCREENS):
        payload = struct.pack("<BLLLLHBHHH", i, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        send(device, SET_SCREEN, payload)


def normalise(config):
    """Return (active_profile, profiles[]) accepting either input shape."""
    if "profiles" in config and isinstance(config["profiles"], list):
        profiles = list(config["profiles"])
        active = int(config.get("active_profile", 0))
    else:
        profiles = [config]
        active = 0
    if not profiles:
        raise SystemExit("at least one profile is required")
    if len(profiles) > NPROFILES:
        raise SystemExit(f"too many profiles: {len(profiles)} > {NPROFILES}")
    if active >= len(profiles):
        active = 0
    return active, profiles


def main():
    config = json.load(sys.stdin)
    active, profiles = normalise(config)

    device = hid.Device(VENDOR_ID, PRODUCT_ID)

    send(device, SUSPEND)

    # Push each profile in turn. The order doesn't matter because every write
    # is slot-routed via SELECT_PROFILE; we push the active profile last so the
    # live state ends in a consistent place no matter what.
    inactive = [i for i in range(len(profiles)) if i != active]
    for slot in inactive:
        push_profile(device, slot, profiles[slot])
    push_profile(device, active, profiles[active])

    send(device, SET_PROFILE_COUNT, struct.pack("<B", len(profiles)))
    send(device, SET_ACTIVE_PROFILE, struct.pack("<B", active))
    send(device, PERSIST_CONFIG)
    send(device, RESUME)


if __name__ == "__main__":
    main()
