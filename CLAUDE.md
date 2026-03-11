# Screen Hopper Hardware Reference

## 6N137 Optocoupler Pinout (DIP-8)

Datasheet: `/Users/samm/Downloads/6N137.PDF`

```
      dot/notch
  NC  [1]   [8] VCC
   A  [2]   [7] VE (enable, active low, has internal pull-down)
   C  [3]   [6] VO (output, active low, open collector)
  NC  [4]   [5] GND
```

- Pin 1: NC (no connection)
- Pin 2: Anode (IR LED input, positive)
- Pin 3: Cathode (IR LED input, negative/ground side)
- Pin 4: NC (no connection)
- Pin 5: GND (output side ground)
- Pin 6: VO (output, active low open collector, needs pull-up)
- Pin 7: VE (enable, active low, internally pulled low = enabled by default)
- Pin 8: VCC (output side power, 4.5V-5.5V)

## PCB Wiring (Triple Pico Version)

### Pico A to Optocoupler (TX side)

- Pico A 3V3 -> 470 ohm resistor -> Pin 2 (Anode)
- Pico A GPIO20 -> Pin 3 (Cathode)

When GPIO20 goes LOW (UART transmitting), current flows from 3V3 through
the resistor, through the IR LED (Anode to Cathode), to GPIO20. This
activates the optocoupler.

### Forwarder to Optocoupler (RX side)

- Forwarder VBUS -> Pin 8 (VCC)
- Forwarder GND -> Pin 5 (GND)
- Forwarder 3V3 -> 680 ohm resistor -> Pin 6 (VO)
- Forwarder GPIO9 -> Pin 6 (VO)

The 680 ohm resistor acts as a pull-up for the open collector output.
When the IR LED activates, VO pulls low, which the forwarder reads on GPIO9.

### Pico A to Pico B (Serial Link)

- Pico A GPIO0 (UART0 TX) -> Pico B GPIO1 (UART0 RX)
- Pico A GPIO1 (UART0 RX) -> Pico B GPIO0 (UART0 TX)
- Pico A GPIO2 (UART0 CTS) -> Pico B GPIO3 (UART0 RTS)
- Pico A GPIO3 (UART0 RTS) -> Pico B GPIO2 (UART0 CTS)
- VBUS shared, GND shared

## Firmware UART Configuration

- UART0 (serial.cc): A-to-B link, 4 Mbaud, GPIO0-3, hardware flow control
- UART1 (remapper.cc): A-to-forwarder link, 1 Mbaud, GPIO20 TX only
- UART1 (forwarder.cc): Forwarder RX, 1 Mbaud, GPIO9 RX only

## Config Baking

`make pico2 CONFIG=path/to.json` bakes a config in as the firmware's default: `config-tool/bake_config.py` packs the JSON into the same 4096-byte flash-sector image `persist_config()` writes and emits `firmware/src/baked_config.h` (gitignored). `load_config()` prefers a USB-persisted config in flash and falls back to the baked one. `CONFIG` is sticky across plain rebuilds; clear with `make bake CONFIG=`. The bake struct layout must stay in sync with `persist_config_t`/`screen_def_t`/`mapping_config_t`/`profile_slot_t`/`device_persist_header_t` in `types.h`.

The JSON can be either a legacy single-profile config OR the multi-profile shape `{ "version": 8, "active_profile": 0, "profiles": [...] }`. `config-tool/config-samm-profiles.json` is the canonical sample: two profiles, both binding F14 to cycle profile (`0xfff30000`), F15 to toggle jiggler (`0xfff30010`) and F16 to switch screen (`0xfff20001`).

**Flashing does NOT erase the persisted config sector** (it lives at the end of flash, outside any UF2/ELF image). A board that already has a persisted config from a previous `set_config.py` run will keep using it after re-flashing the firmware, ignoring any newly baked default. To force the new config to apply, push it via `set_config.py` after flashing - this overwrites the persisted sector and applies live.

## Applying Config Changes (preferred path)

For config-only edits (screens, mappings, jiggler interval, edge resistance, etc.) use `make apply-config CONFIG=path/to/config.json`. It pushes the JSON to the running Pico A over USB HID via `set_config.py` (live, persisted, no reboot) **and** re-bakes `src/baked_config.h` so the next `make flash` carries the same default. Reserve `make flash` for actual firmware code changes - it's heavier, asks for confirmation, and risks the persisted-vs-baked mismatch.

Only Pico A holds screen config. Pico B (USB host) and the forwarder don't read screens or mappings, so config-only changes never need them reflashed.

Verify what the firmware is actually running with `make get-config`.

## Flashing

You must run flash commands outside the sandbox.

`make flash TARGET=<a|b|forwarder|single> [BOARD=pico1|pico2] [CONFIG=path]` builds (re-baking if `CONFIG=` is given), then runs `scripts/flash.sh`. The script auto-detects the BOOTSEL volume (`/Volumes/RP2350` for Pico 2, `/Volumes/RPI-RP2` for Pico 1) and uses `picotool load -x` to flash and reboot cleanly. Falls back to the Raspberry Pi Debug Probe via `openocd` (CMSIS-DAP) when no BOOTSEL device is present. After flashing Pico A (or the single-Pico build), if `CONFIG=` was passed the script also pushes the config via `set_config.py` so the persisted sector gets overwritten. Confirms the target with the user before writing because neither path can distinguish which physical Pico is connected. Default board is `pico2`; `make` alone builds pico2 only.

## Firmware Behaviour Notes

- **`generate_config.py` display detection uses Swift** (`CGDisplayBounds` via `swift -e ...`). In headless or sandboxed contexts where `swift` can't run, pass displays manually: `--displays 3840x2160@0,0 1728x1117@-1728,602`.
- **Forwarder USB identity** is overridden via the `IS_FORWARDER` compile define (in `firmware/CMakeLists.txt`) so the secondary machine sees `Generic` / `USB Keyboard/Mouse` instead of `RP2040` / `Screen Hopper`. Pico A keeps the original strings - config tools key off VID/PID `0xCAFE`/`0xBAF3`, not strings.
- **Jiggler idleness is tracked per-output**: `maybe_jiggle()` in `remapper.cc` uses `last_input_per_output_us[2]` stamped against `screens[active_screen].output`. Using one machine doesn't reset the other's idle clock, so the inactive side still gets nudged. Don't reintroduce a single global idle timer - it starves the inactive side under normal use.
- **Forwarder HID descriptor must match Pico A's report IDs**. If Pico A introduces a new report ID without the forwarder also being reflashed, the forwarder relays an unknown report and the secondary machine's HID stack stalls (dead mouse and keyboard until that machine reboots). Reflash both Pico A and the forwarder whenever report IDs change in `our_descriptor.h`.

## Profiles & Vendor Target Usages (CONFIG_VERSION 8+)

Pico A stores up to `NPROFILES` (4) full profiles. Each holds its own screens, mappings, and scalars - nothing is shared. Switching is live: a hotkey edge triggers `activate_profile(slot)`, which mirrors the slot into the live globals (`screens`, `config_mappings`, scalars), rebuilds `reverse_mapping` and bounds, and re-centres the cursor on the first local screen of the new profile.

Bind any source usage to one of these vendor target usages (same mechanism as `0xfff20001` Switch Screen):

- `0xfff30000` **Cycle profile** - advance `active_profile` by 1 modulo `profile_count`.
- `0xfff30001`..`0xfff30004` **Activate profile 1..4** - jump to a specific slot.
- `0xfff30010` **Toggle jiggler** - flip the runtime `jiggle_enabled` flag (does NOT touch the persisted `jiggle_interval`). The onboard LED blinks **3 times for ON, 5 times for OFF** so you can confirm the toggle without looking at the host. On OFF -> ON only, also kicks `start_jiggle_activation_sweep()`: a ~1 s down/up/right/left cursor wiggle on the forwarder via `queue_relative_movement()`, returning to the starting position. 25 steps × 10 px every 10 ms per leg.

The cycle/activate hotkey lives in each profile's own mappings, so put it in every profile if you want it to keep working after a switch. The jiggler-toggle binding similarly must be present in whichever profile is active when you want to toggle.

## Wire Protocol Additions

CONFIG_VERSION bumped 7 -> 8. New `ConfigCommand` enum values (`firmware/src/types.h`):

- `SELECT_PROFILE = 14` (payload: `uint8_t slot`) - routes subsequent SET_*/GET_*/CLEAR_MAPPING to a slot. Defaults to the active slot on boot.
- `SET_ACTIVE_PROFILE = 15` (payload: `uint8_t slot`) - mirrors the slot into live state immediately.
- `SET_PROFILE_COUNT = 16` (payload: `uint8_t count`) - sets how many slots are valid (1..NPROFILES).
- `GET_DEVICE_INFO = 17` - returns `nprofiles, max_mappings_per_profile, active_profile, profile_count, io_target_slot`.
- `TYPE_TEXT = 18` (payload: `type_text_t` = `uint8_t flags`, `uint8_t len`, `uint8_t data[24]`) - chunked append into a 4 KB `type_buffer` on Pico A. Flag bit 0 = RESET (clear buffer), bit 1 = GO (start replay). Replay is a press/release state machine in `service_type_text()` that emits `REPORT_ID_KEYBOARD` to whatever screen has `output == 1`, at ~7 ms press + 7 ms release per char (~71 cps). `ascii_to_hid()` is US-layout only - non-ASCII bytes are skipped without delay. Host CLI: `config-tool/type_text.py`.

Persisted sector layout (4 KB): `device_persist_header_t` (16 B) + `profile_slot_t[NPROFILES]` (4 × 624 B = 2496 B) + zero padding + trailing CRC32. Single CRC over the whole sector minus the last 4 bytes.

## Drag-Gain Affine Model (CONFIG_VERSION 11)

During a drag the firmware emits relative motion and dead-reckons the internal `cursor_x`, because the absolute report sent on the next move warps the host cursor to it. macOS applies _speed-dependent_ pointer acceleration to that relative motion, so it renders a different number of pixels per emitted count than 1:1, and the post-drag absolute report snaps by the prediction error. It can't be removed host-side without crippling direct mouse use (`com.apple.mouse.scaling -1` makes the pointer linear but too slow).

Measured at ~990 Hz via the SWD probe (see `probe-live-debug-tooling` memory), macOS's gain (host px per emitted relative count) rises with the per-flush speed `v = |rx| + |ry|`. The firmware models it as **affine**: `gain(v) = base + slope*v`. The true curve is concave (it flattens to roughly 0.7-0.8 at speed on the local Mac, on clean drags that never hit a screen edge), so the affine line is an approximation that over-predicts at high `v`. An earlier "rises past 1.0, median ~1.25" reading came from an edge-clamp-contaminated fast flick and is unreliable - don't fit to it. The v9/v10 saturating curve `plateau*v^2/(v^2+k)` was removed; the firmware only supports affine now, do not reintroduce it.

`drag_advance_gain()` in `remapper.cc` returns `clamp(base + slope*v, 0, DRAG_GAIN_CAP_MILLI)` in milli; `cursor_x` advances by `rx * scale * gain / 1000` per flush. The two `screen_def_t` fields are reused (names unchanged): **`drag_gain` = base * 1000**, **`drag_curve_k` = slope * 1e4**; both 0 = flat 1.0 (the v8 behaviour). `DRAG_GAIN_CAP_MILLI` (1300) is a compile-time ceiling bounding fast-flick extrapolation. `drag_advance_gain()` is shared by the flush advance and the `drop_unsent_relative_motion()` rewind so they stay symmetric. Fields are per-screen but effectively per-output (read from the active screen, whose `output` selects the host); tune live with `make apply-config`, no reflash.

**Settled live values on the local Mac: base 260 / slope 390** (`drag_gain=260`, `drag_curve_k=390`). The residual can't reach zero: macOS accelerates on a time-smoothed velocity, not the instantaneous per-flush `v`, so a per-flush affine leaves a small post-release warp that flips sign with gesture speed - 260/390 centres it (a hair forward or back depending on the drag). Going tighter needs a firmware EWMA-velocity model feeding `drag_advance_gain()`, not a config tweak.

**Tune base and slope separately.** base sets slow-drag gain; slope sets fast/large-drag gain. Diagnostic: if the forward over-advance grows with drag size, the slope is too high; if slow drags are off but large ones are fine, adjust base. Whole-line cuts (both at once, e.g. 250/400 or 440/170) under-advanced because they dropped base when only slope was wrong.

See `drag-drift-speed-curve` and `probe-live-debug-tooling` memories for the full history. Tuning workflow (tools in `firmware/tools/drag-tuning/`, all run UNSANDBOXED):
- After any reflash, `source <(./symbols.sh)` to refresh the RAM addresses - they shift every build.
- `./capture.sh` runs the probe sampler + host trace for one drag; do a single press-drag-release then HOLD STILL (holding still reads the armed-but-unfired warp without triggering the jump).
- The metric is `merge.py`'s **H/F ratio**: >1 means under-advance (jump backward/rebound, raise gain), <1 means over-advance (jump forward in drag direction, lower gain). Do NOT trust per-flush gain fits - they back-divide cursor_x through the loaded gain, so they echo the current setting rather than measure macOS.

`screen_def_t` stays 25 bytes (v11 only reinterprets the two existing uint16s, no layout change). Field order is `x, y, w, h, sensitivity(u16), output(u8), scale(u16), drag_gain(u16), drag_curve_k(u16)` - keep the struct packing in `set_config.py`, `get_config.py`, `bake_config.py` and `config-tool-web/code.js` in sync with it. The web tool's "Drag base"/"Drag slope" inputs display base (field/1000) and slope (field/1e4).

## General Notes

- Do not assume things, validate them.
- When updating the config or config schema ensure the cli and web base config managers fully support the new shape and functionality.
- `CONFIG_VERSION` lives in `firmware/src/config.cc` and is duplicated in every host tool (`config-tool/*.py`, `config-tool-web/code.js`). The device silently drops any feature report whose wire version differs (the host's `send_feature_report` still succeeds, so the tool prints success while nothing happens). When bumping it, update all tools. `make pico1`/`pico2` runs `config-tool/check_protocol_version.py`, which fails the build listing any tool left behind.
