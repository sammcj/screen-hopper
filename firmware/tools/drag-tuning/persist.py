#!/usr/bin/env python3
"""Persist the current live/pending config (incl. active_profile) to Pico A flash.
Run UNSANDBOXED (USB HID). `make apply-config` already persists; use this only to
commit a profile switch made with setprofile.py."""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "config-tool"))
import hid
import set_config as sc

dev = hid.Device(sc.VENDOR_ID, sc.PRODUCT_ID)
sc.send(dev, sc.SUSPEND)
sc.send(dev, sc.PERSIST_CONFIG)
sc.send(dev, sc.RESUME)
print("persisted (SUSPEND, PERSIST_CONFIG, RESUME)")
dev.close()
