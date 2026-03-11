#!/usr/bin/env python3
"""Send text to the running Pico A so it types the content on the forwarder machine.

Usage:
    pbpaste | ./type_text.py
    ./type_text.py < file.txt
    ./type_text.py            # falls back to pbpaste on macOS

The Pico holds the text in a 4 KB buffer and replays it as USB HID keystrokes
through the forwarder, which the second computer sees as a normal USB keyboard.
Characters outside printable US-ASCII (plus tab and newline) are dropped on
the firmware side; this script warns about them before sending. The remote
machine must be on a US keyboard layout for punctuation to land correctly -
letters and digits are unaffected by layout.
"""

import argparse
import binascii
import struct
import subprocess
import sys

import hid

VENDOR_ID = 0xCAFE
PRODUCT_ID = 0xBAF3

CONFIG_VERSION = 11
CONFIG_SIZE = 32
REPORT_ID_CONFIG = 100

TYPE_TEXT = 18

TYPE_TEXT_FLAG_RESET = 0x01
TYPE_TEXT_FLAG_GO = 0x02

# Must match TYPE_BUFFER_SIZE in firmware/src/remapper.cc.
MAX_TEXT_BYTES = 4096

# type_text_t payload: 1 byte flags + 1 byte len + 24 bytes data = 26 bytes,
# matching set_feature_t::data[26].
CHUNK_SIZE = 24

# Set defined by ascii_to_hid() in firmware/src/remapper.cc. Anything outside
# this set is dropped silently by the firmware; we warn the user beforehand.
_SUPPORTED = set(b"abcdefghijklmnopqrstuvwxyz")
_SUPPORTED |= set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_SUPPORTED |= set(b"0123456789")
_SUPPORTED |= set(b" \t\n")
_SUPPORTED |= set(b"-=[]\\;'`,./")
_SUPPORTED |= set(b"!@#$%^&*()_+{}|:\"~<>?")


def add_crc(buf):
    return buf + struct.pack("<L", binascii.crc32(buf[1:]))


def send_chunk(device, data, reset, go):
    """Send one TYPE_TEXT feature report. `data` must be at most CHUNK_SIZE bytes."""
    flags = (TYPE_TEXT_FLAG_RESET if reset else 0) | (TYPE_TEXT_FLAG_GO if go else 0)
    payload = struct.pack("<BB", flags, len(data)) + data
    payload = payload.ljust(26, b"\x00")
    buf = struct.pack("<BBB", REPORT_ID_CONFIG, CONFIG_VERSION, TYPE_TEXT) + payload
    device.send_feature_report(add_crc(buf))


def read_input(use_clipboard):
    if not use_clipboard:
        return sys.stdin.buffer.read()
    # stdin was a terminal - reach for the clipboard.
    if sys.platform != "darwin":
        raise SystemExit(
            "No piped input and pbpaste only runs on macOS. "
            "Pipe text in or pass it via stdin redirect."
        )
    result = subprocess.run(
        ["pbpaste"], check=True, capture_output=True
    )
    return result.stdout


def main():
    parser = argparse.ArgumentParser(
        description="Type stdin (or the clipboard) on the forwarder machine via Pico A."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Send even if there are unsupported characters (they'll be dropped).",
    )
    args = parser.parse_args()

    use_clipboard = sys.stdin.isatty()
    raw = read_input(use_clipboard)
    if not raw:
        sys.exit("No text to type.")

    truncated = False
    if len(raw) > MAX_TEXT_BYTES:
        raw = raw[:MAX_TEXT_BYTES]
        truncated = True

    unsupported = sorted({b for b in raw if b not in _SUPPORTED})
    if unsupported and not args.force:
        sample = "".join(
            chr(b) if 0x20 <= b < 0x7F else f"\\x{b:02x}" for b in unsupported[:16]
        )
        print(
            f"warning: {len(unsupported)} unsupported byte value(s) will be dropped: {sample}",
            file=sys.stderr,
        )
        print("re-run with --force to send anyway.", file=sys.stderr)
        sys.exit(2)

    device = hid.Device(VENDOR_ID, PRODUCT_ID)

    chunks = [raw[i : i + CHUNK_SIZE] for i in range(0, len(raw), CHUNK_SIZE)] or [b""]
    for i, chunk in enumerate(chunks):
        send_chunk(
            device,
            chunk,
            reset=(i == 0),
            go=(i == len(chunks) - 1),
        )

    source = "clipboard" if use_clipboard else "stdin"
    note = f" (truncated to {MAX_TEXT_BYTES} bytes)" if truncated else ""
    print(f"Queued {len(raw)} bytes from {source}{note}.", file=sys.stderr)


if __name__ == "__main__":
    main()
