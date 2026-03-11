#!/usr/bin/env python3
"""Fail if any host-side tool's CONFIG_VERSION drifts from the firmware's.

The firmware (firmware/src/config.cc) rejects any REPORT_ID_CONFIG feature
report whose wire version != CONFIG_VERSION. The rejection is SILENT on the
host: send_feature_report() still succeeds at the USB layer, so a stale tool
prints "success" while the command is dropped and nothing happens on the
device. That is exactly how type_text.py broke when CONFIG_VERSION went 8 -> 11.

This guard reads the authoritative version from config.cc and checks every
config tool (config-tool/*.py, config-tool-web/*.js) that declares a
CONFIG_VERSION against it. Run from the firmware build so a version bump that
misses a tool fails loudly. Exit 0 = all consistent, 1 = mismatch/missing.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_SRC = REPO_ROOT / "firmware" / "src" / "config.cc"

FIRMWARE_RE = re.compile(r"const\s+uint8_t\s+CONFIG_VERSION\s*=\s*(\d+)")
TOOL_RE = re.compile(r"\bCONFIG_VERSION\s*=\s*(\d+)")

# Globs of host tools that talk the config wire protocol. Any file matching a
# glob that declares a CONFIG_VERSION is checked; files without one are skipped
# (not every script talks the protocol), so new tools are covered automatically.
TOOL_GLOBS = ["config-tool/*.py", "config-tool-web/*.js"]


def firmware_version() -> int:
    text = FIRMWARE_SRC.read_text()
    m = FIRMWARE_RE.search(text)
    if not m:
        sys.exit(f"error: no CONFIG_VERSION found in {FIRMWARE_SRC}")
    return int(m.group(1))


def tool_versions():
    for glob in TOOL_GLOBS:
        for path in sorted(REPO_ROOT.glob(glob)):
            if path.name == Path(__file__).name:
                continue
            m = TOOL_RE.search(path.read_text())
            if m:
                yield path.relative_to(REPO_ROOT), int(m.group(1))


def main() -> int:
    expected = firmware_version()
    stale = [(p, v) for p, v in tool_versions() if v != expected]
    if stale:
        print(f"CONFIG_VERSION mismatch (firmware/src/config.cc = {expected}):", file=sys.stderr)
        for path, version in stale:
            print(f"  {path}: CONFIG_VERSION = {version} (expected {expected})", file=sys.stderr)
        print(
            "\nBump CONFIG_VERSION in the listed tool(s) to match the firmware. A "
            "stale version is rejected silently by the device.",
            file=sys.stderr,
        )
        return 1
    print(f"CONFIG_VERSION consistent across all config tools (= {expected}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
