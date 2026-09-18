---
name: forwarder-drops-on-busy-endpoint
description: Forwarder has no report queue - any change raising Pico A's output-1 report rate risks dropped key/button reports on the remote machine
metadata:
  type: project
---

The forwarder (`firmware/src/forwarder.cc`, `serial_callback`) drops any incoming report when `tud_hid_ready()` is false, and Pico A's `send_report()` drains output-1 reports over serial back-to-back without waiting for anything (the `tud_hid_ready()` gate in the main loop only protects A's own USB endpoint).

**Why:** Noted during the 2026-09-18 review of the 1 kHz absolute-position upsampler. Anything that makes A emit output-1 reports near or above the remote host's 1 kHz poll rate means a keyboard or relative (button) report landing right after an abs report gets discarded - lost keypress or stuck key/button on the remote.

**How to apply:** When reviewing any change that increases Pico A's report rate (upsampling, gestures, jiggler, type_text), check whether it applies to output 1 and flag the forwarder drop path unless smoothing is gated to output 0 or the forwarder gains a queue.
