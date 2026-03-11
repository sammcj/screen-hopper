#!/usr/bin/env python3
"""Dump the live config from a running Pico A over USB HID.

Walks every configured profile slot by issuing SELECT_PROFILE then GET_CONFIG /
GET_MAPPING / GET_SCREEN, and writes the result as multi-profile JSON to stdout.
For a device that only has one profile, the output is still a multi-profile
JSON (with profiles list of length 1) so the format stays uniform.
"""

import hid
import binascii
import struct
import json

VENDOR_ID = 0xCAFE
PRODUCT_ID = 0xBAF3

CONFIG_VERSION = 11
CONFIG_SIZE = 32
REPORT_ID_CONFIG = 100

GET_CONFIG = 3
GET_MAPPING = 6
GET_SCREEN = 13
SELECT_PROFILE = 14
GET_DEVICE_INFO = 17

UNMAPPED_PASSTHROUGH_FLAG = 0x01

NSCREENS = 6


def check_crc(buf, crc_):
    if binascii.crc32(buf[1:29]) != crc_:
        raise Exception("CRC mismatch")


def add_crc(buf):
    return buf + struct.pack("<L", binascii.crc32(buf[1:]))


def send(device, command, payload=b""):
    if len(payload) > 26:
        raise SystemExit(f"payload too large for command {command}: {len(payload)} > 26")
    payload = payload.ljust(26, b"\x00")
    data = struct.pack("<BBB", REPORT_ID_CONFIG, CONFIG_VERSION, command) + payload
    device.send_feature_report(add_crc(data))


def read_feature(device):
    return device.get_feature_report(REPORT_ID_CONFIG, CONFIG_SIZE + 1)


def get_device_info(device):
    send(device, GET_DEVICE_INFO)
    data = read_feature(device)
    (
        _report_id,
        version,
        nprofiles,
        max_mappings,
        active_profile,
        profile_count,
        io_target_slot,
        _pad,
        crc,
    ) = struct.unpack("<BBBBBBB22sL", data)
    check_crc(data, crc)
    # A pre-v8 firmware that didn't know GET_DEVICE_INFO returned an all-zero
    # response with a valid CRC; surface a clear error instead of silently
    # walking zero profiles.
    if version != CONFIG_VERSION:
        raise SystemExit(
            f"firmware version mismatch: got {version}, expected {CONFIG_VERSION}. "
            f"Re-flash with the matching firmware."
        )
    return {
        "nprofiles": nprofiles,
        "max_mappings_per_profile": max_mappings,
        "active_profile": active_profile,
        "profile_count": profile_count,
        "io_target_slot": io_target_slot,
    }


def read_profile(device):
    """Read the profile currently targeted by io_target_slot."""
    send(device, GET_CONFIG)
    data = read_feature(device)
    (
        report_id,
        version,
        flags,
        partial_scroll_timeout,
        mapping_count,
        our_usage_count,
        their_usage_count,
        interval_override,
        constraint_mode,
        offscreen_sensitivity,
        coord_scale,
        edge_resistance,
        jiggle_interval,
        crc,
    ) = struct.unpack("<BBBLHHHBBLLLHL", data)
    check_crc(data, crc)

    profile = {
        "unmapped_passthrough": (flags & UNMAPPED_PASSTHROUGH_FLAG) != 0,
        "partial_scroll_timeout": partial_scroll_timeout,
        "interval_override": interval_override,
        "constraint_mode": constraint_mode,
        "offscreen_sensitivity": offscreen_sensitivity,
        "coord_scale": coord_scale,
        "edge_resistance": edge_resistance,
        "jiggle_interval": jiggle_interval,
        "screens": [],
        "mappings": [],
    }

    for i in range(mapping_count):
        send(device, GET_MAPPING, struct.pack("<L", i))
        data = read_feature(device)
        (
            report_id,
            target_usage,
            source_usage,
            scaling,
            layer,
            mflags,
            *_rest,
            crc,
        ) = struct.unpack("<BLLlBB14BL", data)
        check_crc(data, crc)
        profile["mappings"].append({
            "target_usage": "{0:#010x}".format(target_usage),
            "source_usage": "{0:#010x}".format(source_usage),
            "scaling": scaling,
            "layer": layer,
            "sticky": (mflags & 0x01) != 0,
        })

    for i in range(NSCREENS):
        send(device, GET_SCREEN, struct.pack("<L", i))
        data = read_feature(device)
        (
            report_id,
            x,
            y,
            w,
            h,
            sensitivity,
            output,
            scale,
            drag_gain,
            drag_curve_k,
            *_rest,
            crc,
        ) = struct.unpack("<BLLLLHBHHH3BL", data)
        check_crc(data, crc)
        if w == 0 and h == 0:
            continue  # unconfigured slot
        profile["screens"].append({
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "sensitivity": sensitivity,
            "output": output,
            "scale": scale,
            "drag_gain": drag_gain,
            "drag_curve_k": drag_curve_k,
        })

    return profile


def main():
    device = hid.Device(VENDOR_ID, PRODUCT_ID)
    info = get_device_info(device)

    profiles = []
    for slot in range(info["profile_count"]):
        send(device, SELECT_PROFILE, struct.pack("<B", slot))
        profiles.append(read_profile(device))

    # Restore the device's io_target_slot to the active profile so subsequent
    # reads from any tool see the live profile by default.
    send(device, SELECT_PROFILE, struct.pack("<B", info["active_profile"]))

    output = {
        "version": CONFIG_VERSION,
        "active_profile": info["active_profile"],
        "profiles": profiles,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
