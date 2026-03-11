# Two-way text sending (forwarder machine -> main machine)

Design notes for adding the reverse text direction. Today text flows one way only: the
main machine (Pico A) types onto the forwarder machine via `config-tool/type_text.py`.
Sending text back, so the forwarder machine can type onto the main machine, is possible
but needs a hardware change. This file records what that would take so it can be picked
up later.

## Why it doesn't work today

The Pico A to forwarder link is a single optocoupler, and an optocoupler only passes
signal one way (LED to transistor). Pico A drives the opto LED from `GPIO20` (UART1 TX)
and the forwarder reads the opto transistor on `GPIO9` (UART1 RX). Pico A only ever
configures TX on UART1 (`forwarder_serial_init` in `remapper.cc`) and the forwarder only
configures RX (`forwarder_serial_init` in `forwarder.cc`). There is no electrical path
from the forwarder back to Pico A.

The Pico A to Pico B link (`serial.cc`, UART0) is full-duplex, but Pico B is the USB host
wired to the mouse and keyboard and has no connection to the second machine, so it can't
carry anything from the forwarder side.

So the reverse direction is blocked in hardware, not firmware. No firmware-only workaround
exists, because the one optocoupler physically cannot carry data the other way.

## What it would take

### 1. Hardware: a second optocoupler (the real blocker)

Add a second optocoupler (same HCPL-0601 on the SMD variants, 6N137 on through-hole) plus
its pull-up resistor, wired the opposite way:

- A forwarder TX GPIO (for example `GPIO8`, UART1 TX) drives the new opto's LED.
- Pico A reads the new opto's transistor on a free RX-capable pin (for example `GPIO21`,
  UART1 RX), giving Pico A a full-duplex UART1 (TX on 20, RX on 21).

This keeps the galvanic isolation that is the whole reason the link is optical (see
`hardware/README.md`, "The one thing that matters"). A bare wire between the two ground
domains would defeat the isolation. The current assembled board has no spare opto
footprint, so this is a hand-wired add-on now, or a footprint in the next board revision.

### 2. Firmware

- Forwarder (`forwarder.cc`): it is a USB device on the second machine, so that machine
  pushes text to it via a HID feature or output report. Fill in `tud_hid_set_report_cb`
  (currently empty) to accept a `TYPE_TEXT`-style report, buffer it, and `serial_write()`
  it over the new reverse UART. Add a TX pin to `forwarder_serial_init` (it inits RX only
  today). `serial_write()` already exists and is direction-agnostic.
- Pico A (`remapper.cc`): init UART1 RX, `serial_read()` the reverse channel, buffer into
  a second `type_buffer`, and replay as keyboard reports to a local screen (`output == 0`).
  This mirrors the existing `service_type_text()`, which targets `find_forwarder_screen()`;
  the reverse version targets `find_local_screen()`. The keyboard-to-main-machine path
  already exists from normal operation.

### 3. Host script on the forwarder machine

A mirror of `type_text.py` that grabs the second machine's clipboard and sends it to the
forwarder over USB HID, using the same chunked `TYPE_TEXT` framing (`send_chunk` in
`type_text.py`), pointed at the forwarder's VID/PID.

## Scope and effort

The firmware and host-script work is modest and reuses the existing SLIP/CRC framing and
the type-buffer replay state machine. The gating item is the second optocoupler: until the
reverse opto is wired in, the firmware and script have nothing to carry data over.

## Alternative if it doesn't have to go through the KVM

If the goal is just getting the second machine's clipboard onto the main machine, and it
doesn't have to traverse the hardware, a network path (or a shared file/note) is far less
work than a second optocoupler plus two firmware changes. The opto path only makes sense
if the second machine is deliberately isolated and routing through the hardware is the
point.
