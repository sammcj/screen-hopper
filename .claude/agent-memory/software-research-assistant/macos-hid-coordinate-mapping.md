# macOS HID Absolute Coordinate Mapping Research

## Key Finding: Mouse Usage = Single Display, Digitizer Usage = Full Desktop (with caveats)

### The Decision Point

macOS's `IOHIDEventDriver` (kernel, open source) decides how to route HID input based on **element parsing priority order**:

1. `parseDigitizerElement()` -- checks for Digitizer Usage Page (0x0D)
2. `parseGameControllerElement()`
3. `parseMultiAxisElement()`
4. `parseRelativeElement()` -- checks kIOHIDElementFlagsRelativeMask
5. `parseScrollElement()`
6. etc.

The first parser that claims an element wins. If `parseDigitizerElement` claims the X/Y axes (because the device uses Usage Page 0x0D), the device is treated as a digitizer with absolute coordinates mapped to the full desktop. If `parseRelativeElement` claims them (because the device uses Usage Page 0x01 with Mouse usage and the Relative flag), it becomes a relative pointer.

### Current Screen Hopper Descriptor Problem

The current descriptor uses:
- Usage Page 0x01 (Generic Desktop)
- Usage 0x02 (Mouse)
- X/Y with Absolute flag (no Relative flag set)
- Logical range 0-32767

This makes it an **absolute mouse**. macOS's IOHIDEventDriver (10.9 source, confirmed in latest) handles this as a digitizer-like device because:
- `isRelative` stays false (no kIOHIDElementFlagsRelativeMask)
- X/Y get calibrated via `calibrateDigitizerElement()` (normalised 0-1)
- It dispatches via `dispatchDigitizerEventWithTiltOrientation()`

BUT macOS maps an absolute Mouse (Usage 0x02) to the **primary display only**, not the full desktop. This is because IOHIDSystem's display bounds mapping treats Mouse-usage devices differently from Digitizer-usage devices at the WindowServer level.

### What Determines Per-Display vs Full-Desktop

| Factor | Single Display | Full Desktop |
|--------|---------------|--------------|
| Usage Page 0x01, Usage 0x02 (Mouse) + Absolute X/Y | YES (primary only) | NO |
| Usage Page 0x0D, Usage 0x01 (Digitizer) + Pen/Stylus | Possibly | YES (if working) |
| Usage Page 0x0D, Usage 0x04 (Touch Screen) | Possibly | YES (if working) |
| Usage Page 0x0D, Usage 0x05 (Touch Pad) | NO (gestures) | NO (treated as trackpad) |

### macOS Digitizer Support Status (Sonoma/Sequoia)

**What works driverlessly:**
- Pen/Stylus digitizers (Usage Page 0x0D, Usage 0x02 Pen) with In Range, Tip Switch -- partial support, cursor movement confirmed
- Touch Screen (Usage Page 0x0D, Usage 0x04) -- single touch may work, multi-touch does NOT work without custom driver

**What does NOT work driverlessly:**
- Touch Pad digitizers (Usage Page 0x0D, Usage 0x05) -- completely ignored by AppleUserHIDEventDriver
- Multi-touch anything via standard HID descriptors -- requires custom DEXT driver
- dispatchDigitizerTouchEvent() -- even custom DEXT drivers report this does nothing

### QMK Digitizer Descriptor (Known Working)

QMK's digitizer uses this descriptor structure (confirmed from source):
```
Usage Page (Digitizers)       0x05, 0x0D
Usage (Digitizer)             0x09, 0x01    // NOT Pen, NOT Touch Screen
Collection (Application)
  Usage (Stylus)              0x09, 0x20
  Collection (Physical)
    Usage (In Range)          0x09, 0x32
    Usage (Tip Switch)        0x09, 0x42
    Usage (Barrel Switch)     0x09, 0x44
    [3 bits data, 5 bits padding]
    Usage Page (Generic Desktop)
    Usage (X)                 0x09, 0x30
    Usage (Y)                 0x09, 0x31
    Logical Max (32767)
    Report Count (2)
    Report Size (16)
    Unit (Inch, English Linear)  0x13
    Unit Exponent (-2)           0x0E
    Input (Data, Var, Abs)
  End Collection
End Collection
```

Key elements:
- Top-level: Usage Page 0x0D (Digitizer), Usage 0x01 (Digitizer device type)
- Transducer: Usage 0x20 (Stylus) in Physical collection
- Required fields: In Range (0x32), Tip Switch (0x42)
- X/Y use Generic Desktop page with absolute flag
- Units specified (inches with exponent -2)
- QMK docs note: "the OS will likely map these coordinates to the virtual desktop" (all monitors)

### IOHIDEventDriver Decision Flow (from Apple OSS source)

In `handleInterruptReport()`:

```
if (multiAxis.capable)
    dispatchMultiAxisPointerEvent()          // 3D mice, trackballs
else if (digitizerHandled || (isAbsoluteAxis && !relativeX && !relativeY && inRange))
    dispatchDigitizerEventWithTiltOrientation()  // Digitizer path -> full desktop
else if (relativeX || relativeY || buttonChanged)
    dispatchRelativePointerEvent()           // Standard mouse path
else
    // event dropped
```

The critical condition: `isAbsoluteAxis && !relativeX && !relativeY` with `inRange` set. If these are all true, the digitizer dispatch path is taken regardless of whether the device declared itself as a Mouse or Digitizer at the usage level.

However, the downstream mapping (IOHIDSystem -> WindowServer) treats the event differently based on whether the device was classified as a digitizer (Usage Page 0x0D) or a mouse (Usage Page 0x01).

### IOHIDSystem Display Bounds

IOHIDSystem maintains virtual screens with bounds via `setDisplayBoundsGated()`. The cursor position mapping works as:
- For digitizer events: coordinates are mapped across the full virtual desktop bounding box
- For absolute mouse events: coordinates are mapped to the display associated with that device (typically primary)

This is handled in WindowServer (SkyLight framework, closed source) not in the open-source IOHIDFamily.

### Apple's Own Devices

- Sidecar (iPad as display): Uses private/proprietary HID descriptors, not standard digitizer page
- Apple Pencil: Processed through MultitouchSupport.framework, not standard HID digitizer path
- Magic Trackpad: Uses Digitizer Usage Page 0x0D with Touch Pad usage, plus Apple-specific vendor extensions
- All Apple digitizer devices use entitlement-protected APIs and vendor-specific HID usages

### Practical Implications for Screen Hopper

**Option A: Switch to Digitizer descriptor (recommended to test)**
- Change Usage Page from 0x01 to 0x0D
- Change Usage from Mouse (0x02) to Digitizer (0x01)
- Add Stylus (0x20) transducer in Physical collection
- Add In Range (0x32) and Tip Switch (0x42) fields
- Keep X/Y as absolute 0-32767
- Add Unit (Inch) and Unit Exponent
- This SHOULD map to full desktop on macOS
- Risk: macOS may ignore it entirely (as seen with touch pad descriptors)

**Option B: Keep Mouse descriptor, compute per-display coordinates**
- Keep current descriptor (single-display mapping)
- In firmware, compute which display the cursor should be on
- Map coordinates to that display's portion of the 0-32767 range
- Problem: still limited to primary display coordinate space

**Option C: Dual descriptor approach**
- Present both Mouse (relative, for compatibility) and Digitizer (absolute, for positioning)
- Note from CircuitPython docs: on macOS, Mouse device MUST come before Digitizer in USB composite
- JetKVM uses this approach (separate absolute and relative mouse endpoints)

### Version-Specific Notes

- macOS 10.12 (Sierra): `dispatchAbsolutePointerEvent` broke for kext drivers
- macOS 10.13+: HIDDriverKit (DEXT) is the supported path for custom drivers
- macOS 14.7 (Sonoma): Standard digitizer touchpad descriptors do NOT work
- macOS 14.7 (Sonoma): Standard mouse descriptors work fine
- macOS 15 (Sequoia): No confirmed changes to digitizer handling
- Apple's DTS has a stylus DEXT example that partially works

### Key Sources

- Apple OSS IOHIDFamily: https://github.com/apple-oss-distributions/IOHIDFamily
- IOHIDEventDriver.cpp (latest): 5236 lines, handles all HID event classification
- Apple Forum DEXT thread: https://forums.developer.apple.com/forums/thread/768586
- QMK digitizer descriptor: https://github.com/qmk/qmk_firmware/blob/master/tmk_core/protocol/usb_descriptor.c
- Apple Accessory Design Guidelines R23, section 15: https://developer.apple.com/accessories/Accessory-Design-Guidelines.pdf
- IOHIDeous writeup (IOHIDSystem internals): https://github.com/Siguza/IOHIDeous
- JetKVM coordinate mapping: https://github.com/jetkvm/kvm/issues/523
