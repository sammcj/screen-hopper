# Screen Hopper: a smart KVM switch

This is a USB input switcher that lets you use you mouse and keyboard with two computers by just dragging the mouse cursor from one screen to the other. It's like Logitech Flow, Mouse without Borders, Synergy or Apple Universal Control, but it works entirely in hardware. No special software is required on the computers. It works with Windows, Linux and Mac.

Here's a [demo video](https://www.youtube.com/watch?v=z24jG9Nh5gk).

This project is derived from [HID Remapper](https://github.com/jfedor2/hid-remapper) so it inherits all the nice features like input remapping, sensitivity adjustment, polling rate overclocking and more. Check out that project's documentation to see its full potential.

Screen shapes and their relative position are configurable [through a web browser](https://www.jfedor.org/screen-hopper-config/) using WebHID (Chrome or Chrome-based browser required).

In addition to dragging the cursor from one screen to the other, you can also map a key or button to switch between screens.

Wireless receivers are supported and multiple devices can be connected at the same time using a USB hub.

## Profiles

Pico A stores up to 4 independent profiles. Each profile has its own screens, mappings, sensitivity, edge-resistance and jiggler interval - nothing is shared - so you can switch between, say, a single-monitor layout and a multi-monitor layout without re-running the configuration tool. Profiles are managed from the web tool (Profile tabs + active-profile selector) or directly in the multi-profile JSON shape (`{ "version": 8, "active_profile": 0, "profiles": [...] }`).

Bind any key or button to one of these target usages to switch profiles live:

- `0xfff30000` - cycle to the next profile
- `0xfff30001` .. `0xfff30004` - jump directly to profile 1..4

The hotkey lives inside each profile's own mappings, so include it in every profile you want to be able to leave.

## Keep-awake jiggler

Set a profile's `jiggle_interval` (seconds) to have Pico A nudge whichever machine has been idle longest, stopping the inactive side from locking while you work on the other. Idleness is tracked per output, so using one machine doesn't reset the other's clock.

Bind any source to target usage `0xfff30010` for a runtime on/off toggle - the onboard LED blinks **3 times for ON, 5 times for OFF**. The toggle does not change the persisted interval; it only enables/disables the jiggler for the current power cycle. When the toggle turns the jiggler ON, the cursor on the forwarder machine does a brief down/up/right/left sweep (~250 px each way, returning to where it started) so you can see the toggle landed without looking at the LED.

## Type text from the clipboard

`config-tool/type_text.py` ships a small CLI that turns text on the Pico-A computer into keystrokes on the forwarder machine, useful for passing a URL, a code snippet or a short password across without needing networking or host software on the second computer.

```shell
pbpaste | python3 config-tool/type_text.py    # macOS clipboard
python3 config-tool/type_text.py < file.txt   # from a file
python3 config-tool/type_text.py              # falls back to pbpaste on macOS
```

To fire it from anywhere with a hotkey, bind a shortcut in a tool like BetterTouchTool to
an "Execute Shell Script" action running `pbpaste | python3 config-tool/type_text.py`. Use
absolute paths (the script's Python and `pbpaste`), because such tools run scripts in a
minimal shell without your normal `PATH`, and pipe `pbpaste` in explicitly rather than
relying on the clipboard fallback.

The text is buffered on Pico A (cap: 4096 bytes - longer input is truncated) and replayed at ~70 characters/second through the forwarder's USB HID keyboard interface. Bytes outside printable US-ASCII (plus tab and newline) are dropped by the firmware, so the CLI transliterates non-ASCII first: box drawing becomes `+-|`, smart quotes and dashes become their ASCII forms, accents are stripped (`café` -> `cafe`), symbols get spelled out (`©` -> `(c)`, `½` -> `1/2`, `⌘` -> `cmd`), and ANSI colour escapes plus stray control bytes are removed. Characters with no ASCII form at all (emoji, CJK, Greek) are dropped and listed as a warning. Pass `--raw` to send the bytes untouched; anything still unsupported is listed and the CLI exits unless you pass `--force`. The remote machine must be on a US keyboard layout for punctuation to land correctly; letters and digits are layout-independent.

![Screen hopper dual Pico version](images/screen-hopper.jpg)

## How is it possible?

As you might know, normal mice only send relative inputs (X/Y deltas) to the computer, they don't know where the cursor is on the screen. Screen Hopper needs to know this to be able to switch between the screens. So what it does is it keeps an internal state of where the cursor is based on the inputs received from the mouse and it sends the absolute X/Y position to the connected computers. It is a standard feature of the USB HID protocol, but normally it's only used by devices like touchscreens and graphic tablets.

There are some consequences to this mode of operation, for example the aspect ratios of the screens used need to be configured for Screen Hopper to be able to properly scale the horizontal and vertical inputs. Also it probably won't work very well with games that expect raw mouse inputs.

## How to make the device

There are two hardware versions of the Screen Hopper: the dual Pico version and the triple Pico version. They have the same functionality, but the triple Pico version has better device compatibility - some input devices work with either, but some will only work with the triple Pico version.

See [HARDWARE.md](HARDWARE.md) for details on how to make both versions of the device.

## How to use the configuration tool

A live version of the web configuration tool can be found [here](https://www.jfedor.org/screen-hopper-config/). It only works in Chrome and Chrome-based browsers. On Linux you might need to give yourself permissions to the appropriate `/dev/hidraw*` device. The configuration tool should be used on the computer connected to the Pico running `screenhopper.uf2` or `screenhopper_a.uf2`.

Some of the configuration options are inherited from [HID Remapper](https://github.com/jfedor2/hid-remapper) so check that project for the meaning of those settings (the mapping functionality can do really awesome things!).

Screen Hopper needs to know the screen shapes and their relative position to be able to move the cursor between the two computers. You configure that by entering the position (X, Y) and dimensions (width, height) of each screen. (A graphical preview would be nice here, but for now it's just numbers.) The dimensions use abstract units so their absolute values don't mean much, but you will find some combinations give you reasonable mouse sensitivity. You can adjust the sensitivity for each screen separately.

The "Restrict cursor" setting determines what should happen when you drag the mouse cursor outside of the screens (to the area that's not visible). If you select the "to screens" option, it will not be possible to go outside the visible area. If you select the "to bounding box" option, the cursor will be restricted to the smallest rectangle that covers both screens (this only makes a difference if the touching edges of the two screens are not the same length). If you select "don't restrict", the cursor will not stop at any of the screen edges.

![Screens configuration](images/screens-config.png)

Depending on the screens configuration and the "Restrict cursor" setting, it might be possible for the cursor to be outside of the visible area. There's a separate setting for mouse sensitivity for that situation.

If you configure the screens so that they don't touch each other (there's a gap) and select the "restrict to screens" option then it will not be possible to drag the cursor from one screen to the other. You can still switch between the screens by mapping some key or button to "Switch screen".

If you can't use the browser-based configuration tool, there's also a [command-line tool](config-tool) that takes JSON in the same format as the web tool on standard input. I only tested it on Linux, but in theory it should also run on Windows and Mac.

## How to compile the firmware

Both Raspberry Pi Pico (RP2040) and Pico 2 (RP2350) are supported.

```shell
git clone https://github.com/jfedor2/screen-hopper.git
cd screen-hopper/firmware

# for pico v1:
make pico1

# for pico v2:
make pico2
```

### Firmware output

After building, the UF2 firmware files are copied to `firmware/pico1/` or `firmware/pico2/`:

| File                 | Version | Purpose                  |
| -------------------- | ------- | ------------------------ |
| `screenhopper.uf2`   | Dual    | Main Pico (with PIO-USB) |
| `forwarder.uf2`      | Dual    | Second Pico              |
| `screenhopper_a.uf2` | Triple  | Pico A                   |
| `screenhopper_b.uf2` | Triple  | Pico B (USB host)        |

To flash, hold BOOTSEL while plugging in the Pico, then copy the appropriate UF2 file to the drive that appears. Alternatively, `make flash TARGET=<a|b|forwarder|single> [BOARD=pico1|pico2] [CONFIG=path]` wraps the whole thing - it builds (re-baking if `CONFIG=` is given), auto-detects the BOOTSEL volume and uses `picotool load -x` to flash and reboot, or falls back to a Raspberry Pi Debug Probe over SWD when no BOOTSEL device is present.

### Applying config changes without reflashing

For config-only edits (screens, mappings, profiles, jiggler interval, edge resistance) reach for `make apply-config CONFIG=path/to/config.json` instead of `make flash`. It pushes the JSON to the running Pico A over USB HID (live + persisted, no reboot) and also re-bakes `firmware/src/baked_config.h` so the next `make flash` carries the same default. Only Pico A holds screen/mapping config, so config changes never need Pico B or the forwarder reflashed. Verify what the firmware is actually running with `make get-config`.

### Baking a config into the firmware

Flashing a Pico does NOT erase the persisted config sector (it lives outside the UF2 image), but if a board has no persisted config yet, the firmware uses its baked default. Bake one in as a compile-time default by passing `CONFIG` to the build:

```shell
make pico2 CONFIG=../config-tool/config.json
```

The build runs `config-tool/bake_config.py`, which turns the JSON into the same flash-sector image the device would persist, and embeds it in `firmware/src/baked_config.h`. On boot, a config persisted over USB still wins; the baked config is the fallback used when flash holds none (so a freshly flashed board comes up configured without running the configuration tool). The JSON can be a legacy single-profile shape or the multi-profile shape (`{ "version": 8, "active_profile": 0, "profiles": [...] }`).

`CONFIG` is sticky: once baked, plain `make pico2` keeps it across rebuilds. To clear it, build with an empty value, `make bake CONFIG=`. The generated `firmware/src/baked_config.h` is gitignored, so a baked personal config is never committed. A build with no baked config uses the firmware's built-in defaults as before.
