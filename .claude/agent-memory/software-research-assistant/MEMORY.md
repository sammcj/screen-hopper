# Software Research Assistant Memory

## macOS USB HID Absolute Coordinate Mapping (Updated 2026-03-25)
- macOS maps USB HID absolute mouse (Usage Page 0x01, Usage 0x02) coordinates to **primary display only**, NOT full virtual desktop
- This is confirmed across JetKVM, NanoKVM, and multiple developer reports
- Digitizer **touchpad/multitouch** descriptors (Usage Page 0x0D, Usage 0x05) are completely ignored by AppleUserHIDEventDriver on macOS Sonoma/Sequoia
- However, **pen/stylus digitizer** descriptors (Usage Page 0x0D, Usage 0x01 Digitizer + Usage 0x20 Stylus) DO have partial macOS support and QMK firmware confirms they map to full virtual desktop
- QMK digitizer descriptor structure is a known-working reference (requires In Range + Tip Switch fields, Unit/UnitExponent)
- Barrier/Synergy uses CGWarpMouseCursorPosition as software workaround
- Detailed research: [macos-hid-coordinate-mapping.md](macos-hid-coordinate-mapping.md)
- Full digitizer descriptor research: see project file `sam_setup/digitizer-hid-research.md`

## Key Digitizer HID Facts
- Required fields: Tip Switch (0x42), In Range (0x32), X, Y. All others optional.
- In Range MUST be set (=1) for coordinates to register on host
- Real Wacom tablets use vendor-specific 0xFF0D, NOT standard 0x0D
- Wacom-compatible tablets (Huion, XP-Pen) use standard 0x0D and work driverless
- Button Page (0x09) usages can be embedded inside Digitizer collections (non-standard but common)
- Scroll wheel needs separate report or Mouse collection (not a digitizer concept)
- Physical size/unit fields may be required by some hosts; QMK includes them

## Screen Hopper Project Context
- Triple Pico KVM: A (USB device), B (USB host), Forwarder (second computer)
- Firmware based on jfedor2/screen-hopper, uses TinyUSB with jfedor2's fork
- Current descriptor: Usage Page 0x01 (Mouse), absolute X/Y 0-32767
- User has 3 monitors in L-shape layout, needs cursor across all of them
