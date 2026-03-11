# TODO - things I want to implement

- The ability to store macros on the Pico and trigger them with a press.

- Bidirectional clipboard sync. The Pico-A-to-forwarder direction is implemented via `config-tool/type_text.py` (host-side CLI reads the clipboard or stdin and the firmware replays it as USB HID keystrokes through the forwarder, capped at 4 KB, ~70 cps, US-ASCII). The reverse direction (forwarder machine -> Pico A machine) needs either a second optocoupler in the opposite direction (small PCB revision) or a separate host-side relay on the forwarder side; the current single-opto link is one-way only. Full design notes for the reverse direction: [`docs/two-way-text-sending.md`](docs/two-way-text-sending.md).

- File sharing via the Pico. Investigated and parked: would need USB Mass Storage emulation on top of the existing composite HID device, the optocoupler tops out around 100 KB/s, and FAT corruption is likely if both hosts mount the fake drive at once. Not a good fit for this hardware - use a USB stick / AirDrop / Syncthing instead.

- Investigate if there is a way we could somehow share / mirror the display of one of the machines, perhaps something like displaylink style emulation (without the need for special drivers).
