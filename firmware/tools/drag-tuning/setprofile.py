#!/usr/bin/env python3
"""Switch Pico A's active profile live (no flash write). Run UNSANDBOXED (USB HID).
Usage: setprofile.py [slot]   (slot 0-based; default 0)"""
import sys, os, struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "config-tool"))
import hid
import set_config as sc

slot = int(sys.argv[1]) if len(sys.argv) > 1 else 0
dev = hid.Device(sc.VENDOR_ID, sc.PRODUCT_ID)
sc.send(dev, sc.SET_ACTIVE_PROFILE, struct.pack("<B", slot))
print(f"sent SET_ACTIVE_PROFILE {slot}")
dev.close()
