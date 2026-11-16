---
name: macos-pointer-acceleration-model
description: How macOS accelerates relative HID pointer reports (IOHIDPointerScrollFilter parametric curve, per-report velocity, per-device disable via hidutil) and how IOHIDEventDriver assigns buttons in mixed rel+abs interfaces
metadata:
  type: reference
---

Researched 2026-11-16 from apple-oss-distributions/IOHIDFamily tag IOHIDFamily-2238.100.59, LinearMouse v0.13.0 source, and live `hidutil`/`ioreg` reads on macOS 27.2.1. Re-verify against the live system before relying on numbers.

**Acceleration model (IOHIDEventSystemPlugIns/IOHIDAcceleration.cpp, IOHIDAccelerationAlgorithm.cpp)**
- Velocity is per report: `floor(sqrt(dx^2+dy^2))` (Euclidean, not |dx|+|dy|). Time between reports is only used when `HIDPointerReportRate` is set; it is (null) on the Screen Hopper service, so there is no time smoothing in the open source.
- Parametric curve: x = v * 67 / HIDPointerResolution(400). out_px = (96/67) * [GL*x + (GP*x)^2 + (GC*x)^3] up to TangentSpeedLinear, then tangent line, then sqrt segment past TangentSpeedParabolicRoot. Curve picked from the service's `HIDAccelCurves` by `HIDMouseAcceleration` (= com.apple.mouse.scaling, 1.0 on this Mac -> GL 0.96, GP 1.06, GC 0.18, TSL 8.6, TSPR 15).
- Predicted gain at scaling 1.0: 0.2304 + 0.0452*v + 0.0000393*v^2 px/count for v <= 51 counts/report. Matches the firmware's fitted 0.26 + 0.039..0.043*v.
- Absolute pointer events are never accelerated (`!IOHIDEventIsAbsolute(event)`).

**Per-device disable**
- `hidutil property --matching '{"VendorID":0xCAFE,"ProductID":0xBAF3}' --set '{...}'` resolves to the one service (matching verified with --get, needs to run unsandboxed). `HIDMouseAcceleration` < 0 (IOFixed, -65536) removes the accelerator. `HIDUseLinearScalingMouseAcceleration` != 0 gives a fixed multiplier equal to HIDMouseAcceleration/65536.
- Property lives in the per-service filter instance, so it is lost on replug/reboot. LinearMouse (IOHIDServiceClientSetProperty) re-applies on device add, wake and property-change notifications.
- No descriptor-level off switch. Descriptor Physical Min/Max + Unit Exponent on the relative X element only sets HIDPointerResolution (rescales the curve).

**Buttons in a mixed rel+abs interface (IOHIDEventDriver.cpp parseElements)**
- If any relative X/Y exists, every Button element from every report ID goes to `_relative.elements`. Abs X/Y under a Mouse collection becomes a digitizer transducer with no buttons, so absolute pointer events carry button mask 0.
- In handleRelativeReport the last Button element in array order wins per usage (setButtonState clears on 0).
- The live driver is the closed DriverKit `AppleUserHIDEventDriver`; the kernel source is the nearest reference, not proof.

Related: [[macos-hid-coordinate-mapping]]
