#!/usr/bin/env python3
"""Send text to the running Pico A so it types the content on the forwarder machine.

Usage:
    pbpaste | ./type_text.py
    ./type_text.py < file.txt
    ./type_text.py            # falls back to pbpaste on macOS

The Pico holds the text in a 4 KB buffer and replays it as USB HID keystrokes
through the forwarder, which the second computer sees as a normal USB keyboard.
Characters outside printable US-ASCII (plus tab and newline) are dropped on
the firmware side, so non-ASCII input is transliterated here first: box drawing
becomes +-|, smart quotes and dashes become their ASCII forms, accents are
stripped, symbols are spelled out, and ANSI escapes plus control bytes are
removed. Characters with no ASCII form (emoji, CJK) are dropped with a warning.
Pass --raw to disable that; anything still unsupported is reported
before sending. The remote
machine must be on a US keyboard layout for punctuation to land correctly -
letters and digits are unaffected by layout.
"""

import argparse
import binascii
import re
import struct
import subprocess
import sys
import unicodedata

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

# Unicode the firmware can't type, mapped to the nearest ASCII. Box drawing is
# the common case (tables pasted out of a terminal); the rest is typographic
# punctuation that word processors and LLM output love.
_TRANSLIT = {
    # Box drawing: verticals, horizontals, corners, tees, crosses.
    **{c: "|" for c in "│┃║┊┋┆┇╎╏"},
    **{c: "-" for c in "─━═┄┅┈┉╌╍"},
    **{
        c: "+"
        for c in (
            "┌┍┎┏┐┑┒┓"
            "└┕┖┗┘┙┚┛"
            "├┣┤┫┬┳┴┻┼╋"
            "╔╗╚╝╠╣╦╩╬"
            "╞╟╡╢╤╥╧╨╪╫"
            "╭╮╯╰╱╲╳"
        )
    },
    # Block/shade characters used as bar-chart fills.
    **{c: "#" for c in "█▉▊▋▌▍▎▏▁▂▃▄▅▆▇▔░▒▓■□▢▣"},
    # Typographic punctuation.
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "′": "'", "«": '"', "»": '"', "‹": "'", "›": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-", "⁻": "-",
    "…": "...", " ": " ", " ": " ", " ": " ",
    " ": " ", "​": "", "﻿": "",
    "⁄": "/", "∕": "/", "⧸": "/", "＼": "\\",
    " ": "\n", " ": "\n", "­": "",
    "•": "*", "●": "*", "▪": "*", "·": "*",
    "◦": "*", "‣": "*", "▸": ">", "▶": ">",
    "◂": "<", "◀": "<",
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>",
    "⇐": "<=", "↑": "^", "↓": "v",
    "⌘": "cmd", "⌥": "opt", "⌃": "ctrl", "⇧": "shift",
    "⏎": "enter", "␣": " ",
    "≤": "<=", "≥": ">=", "≠": "!=", "≈": "~=",
    "×": "x", "÷": "/", "±": "+/-", "∞": "inf",
    "√": "sqrt", "µ": "u", "μ": "u", "‰": " per mille",
    "✓": "y", "✔": "y", "☑": "[x]", "✗": "x",
    "✘": "x", "☐": "[ ]", "★": "*", "☆": "*",
    "⚠": "!", "❌": "x", "✅": "y", "❗": "!",
    "©": "(c)", "®": "(R)", "™": "(TM)", "§": "S",
    "¶": "P", "†": "+", "‡": "++", "№": "No.",
    "°": " deg", "€": "EUR", "£": "GBP", "¥": "JPY",
    "¢": "c", "¡": "!", "¿": "?",
    "\r\n": "\n", "\r": "\n",
}

# CSI (colour/cursor) and OSC (title/hyperlink) escape sequences, which arrive
# whenever raw terminal output lands on the clipboard. Left alone the ESC byte
# is dropped by the firmware but its parameters type as literal garbage
# ("[0;32mgreen"), so whole sequences get stripped here.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.")

# Control bytes the firmware has no keycode for. Tab, newline and carriage
# return are handled above and deliberately absent here.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def transliterate(raw):
    """Fold UTF-8 input down to bytes ascii_to_hid() can actually type.

    Returns (ascii_bytes, dropped), where `dropped` lists the characters that
    had no ASCII equivalent at all (emoji, CJK, Greek) and were discarded.
    """
    text = raw.decode("utf-8", errors="replace")
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    out = []
    dropped = set()
    for ch in text:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
            continue
        if ord(ch) < 0x80:
            out.append(ch)
            continue
        # Strip combining accents (e -> e), then keep whatever is ASCII.
        folded = "".join(
            c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)
        )
        # NFKD can expand into further non-ASCII (1/2 -> "1", U+2044, "2"), so
        # run the table over the decomposition too.
        folded = "".join(_TRANSLIT.get(c, c) for c in folded)
        ascii_only = "".join(c for c in folded if ord(c) < 0x80)
        if not ascii_only.strip() and ch.strip():
            dropped.add(ch)
            continue
        out.append(ascii_only)
    return "".join(out).encode("ascii", errors="ignore"), sorted(dropped)


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
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Skip Unicode-to-ASCII transliteration (box drawing, smart quotes, dashes).",
    )
    args = parser.parse_args()

    use_clipboard = sys.stdin.isatty()
    raw = read_input(use_clipboard)
    if not raw:
        sys.exit("No text to type.")

    if not args.raw:
        folded, dropped = transliterate(raw)
        if folded != raw:
            print("note: transliterated non-ASCII characters.", file=sys.stderr)
        if dropped:
            sample = " ".join(dropped[:16])
            print(
                f"warning: {len(dropped)} character(s) had no ASCII form and "
                f"were dropped: {sample}",
                file=sys.stderr,
            )
        raw = folded
    if not raw:
        sys.exit("Nothing left to type after transliteration.")

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
