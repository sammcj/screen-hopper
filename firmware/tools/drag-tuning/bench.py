#!/usr/bin/env python3
"""Hands-off test bench for Pico A's pointer handling.

Drives the pointer through the firmware's real input path by poking its probe
test hooks over SWD (dbg_move, dbg_rel, dbg_buttons, drag_mode in remapper.cc)
and reads the result back from macOS through hostprobe.swift. Nobody needs to
touch the mouse, and the pointer is yours again when it exits.

    ./bench.py curve            # macOS gain per relative report vs host_accel.h
    ./bench.py sync [seconds]   # random motion across the local displays, counts desyncs
    ./bench.py drag [mode]      # drags on a click-proof pad, prints the post-release error

Run UNSANDBOXED (debug probe + WindowServer), with the pointer on the main Mac.
Addresses come from the ELF, so rebuild and reflash together.
"""
import json, math, os, random, signal, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ELF = os.environ.get("ELF", os.path.join(HERE, "../../build-pico2/screenhopper_a.elf"))
CONFIG = os.environ.get("CONFIG", os.path.join(HERE, "../../../config-tool/config-samm-profiles.json"))
SAMPLE_S = 0.008  # 125 Hz, the mouse's report rate


def symbols():
    nm = subprocess.run(["arm-none-eabi-nm", ELF], capture_output=True, text=True, check=True).stdout
    table = {parts[2]: int(parts[0], 16) for parts in (line.split() for line in nm.splitlines()) if len(parts) == 3}
    return {name: table[name] for name in ("cursor_x", "cursor_y", "active_screen", "dbg_move", "dbg_rel", "dbg_buttons", "drag_mode")}


class Probe:
    """Non-halting RAM access through openocd's TCL port."""

    def __init__(self):
        self.addr = symbols()
        self.openocd = subprocess.Popen(
            ["openocd", "-f", "interface/cmsis-dap.cfg", "-f", "target/rp2350.cfg", "-c", "adapter speed 5000", "-c", "init"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                self.sock = socket.create_connection(("127.0.0.1", 6666), timeout=1.0)
                return
            except OSError:
                time.sleep(0.1)
        self.close()
        sys.exit("could not reach openocd (probe busy or unplugged?)")

    def cmd(self, text):
        self.sock.sendall(text.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            buf += self.sock.recv(4096)
        return buf[:-1].decode(errors="replace")

    def read(self, name, width=8, count=1):
        return [int(v, 0) for v in self.cmd(f"read_memory 0x{self.addr[name]:08x} {width} {count}").split()]

    def write8(self, name, value):
        self.cmd(f"mwb 0x{self.addr[name]:08x} {value & 0xFF}")

    def write_pair(self, name, dx, dy):
        self.cmd(f"mww 0x{self.addr[name]:08x} 0x{((dy & 0xFFFF) << 16) | (dx & 0xFFFF):08x}")

    def cursor(self):
        def i64(name):
            lo, hi = self.read(name, 32, 2)
            value = lo | (hi << 32)
            return value - (1 << 64) if value >= (1 << 63) else value
        screen = self.read("active_screen")[0]
        return i64("cursor_x"), i64("cursor_y"), screen - 256 if screen > 127 else screen

    def close(self):
        try:
            self.write8("dbg_buttons", 0)
            self.write8("drag_mode", 0)
        except Exception:
            pass
        self.openocd.send_signal(signal.SIGTERM)
        try:
            self.openocd.wait(timeout=3)
        except Exception:
            self.openocd.kill()


class Host:
    def __init__(self, pad=False):
        self.proc = subprocess.Popen(["swift", os.path.join(HERE, "hostprobe.swift")] + (["--pad"] if pad else []),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert self.proc.stdin and self.proc.stdout
        self.stdin, self.stdout = self.proc.stdin, self.proc.stdout
        words = self.stdout.readline().split()
        self.displays, self.pad = [], None
        for i, word in enumerate(words):
            if word == "display":
                self.displays.append(tuple(int(v) for v in words[i + 1:i + 5]))
            if word == "pad":
                self.pad = tuple(int(v) for v in words[i + 1:i + 5])

    def state(self):
        self.stdin.write("\n")
        self.stdin.flush()
        x, y, left, locked, downs, drags, ups = self.stdout.readline().split()
        return {"x": float(x), "y": float(y), "left": left == "1", "locked": locked == "1",
                "downs": int(downs), "drags": int(drags), "ups": int(ups)}

    def close(self):
        self.proc.terminate()


class Bench:
    def __init__(self, pad=False):
        with open(CONFIG) as f:
            config = json.load(f)
        profile = config["profiles"][config.get("active_profile", 0)] if "profiles" in config else config
        self.screens = profile["screens"]
        local = [s for s in self.screens if s["output"] == 0]
        self.scale = local[0]["scale"]
        self.sensitivity = local[0]["sensitivity"]
        self.host = Host(pad)
        self.probe = Probe()
        # The firmware layout is the macOS arrangement shifted to start at 0.
        self.off_x = min(d[0] for d in self.host.displays) - min(s["x"] for s in local) / self.scale
        self.off_y = min(d[1] for d in self.host.displays) - min(s["y"] for s in local) / self.scale

    def close(self):
        self.probe.close()
        self.host.close()

    def fw(self):
        """Firmware pointer in host points, and whether it is on the local Mac."""
        cx, cy, screen = self.probe.cursor()
        local = 0 <= screen < len(self.screens) and self.screens[screen]["output"] == 0
        return cx / self.scale + self.off_x, cy / self.scale + self.off_y, local

    def move(self, px_x, px_y):
        """One mouse sample that moves the pointer about (px_x, px_y) with no button held."""
        k = self.scale / self.sensitivity
        self.probe.write_pair("dbg_move", round(px_x * k), round(px_y * k))
        time.sleep(SAMPLE_S)

    def goto(self, x, y, tolerance=3):
        for _ in range(600):
            fx, fy, _ = self.fw()
            dx, dy = x - fx, y - fy
            if abs(dx) <= tolerance and abs(dy) <= tolerance:
                break
            step = max(abs(dx), abs(dy))
            limit = min(1.0, 60 / step)
            self.move(dx * limit, dy * limit)
        time.sleep(0.15)

    def error(self):
        """Host minus firmware position in points, after things settle."""
        time.sleep(0.12)
        fx, fy, local = self.fw()
        h = self.host.state()
        return h["x"] - fx, h["y"] - fy, local


def model_px(counts):
    """host_accel.h at tracking speed 1.0 (same transcription the unit test checks)."""
    gl, gp, gc, t0, t1 = 62915 / 65536, 69468 / 65536, 11796 / 65536, 563610 / 65536, 983040 / 65536
    y0 = gl * t0 + (gp * t0) ** 2 + (gc * t0) ** 3
    m0 = gl + 2 * t0 * gp ** 2 + 3 * t0 ** 2 * gc ** 3
    b0 = y0 - m0 * t0
    y1 = m0 * t1 + b0
    m1 = 2 * y1 * m0
    b1 = y1 ** 2 - m1 * t1
    x = counts * 67 / 400
    y = gl * x + (gp * x) ** 2 + (gc * x) ** 3 if x <= t0 else (m0 * x + b0 if x <= t1 else math.sqrt(m1 * x + b1))
    return y * 96 / 67


def run_curve(bench):
    main = bench.host.displays[0]
    bench.goto(main[0] + main[2] / 2, main[1] + main[3] / 2)
    print("counts  measured_px  model_px  error_px")
    worst = 0
    for counts in (1, 2, 3, 5, 8, 12, 16, 24, 32, 48, 51, 60, 75, 90, 115, 150, 250, 400, 700):  # larger reports run off a 3360 px display from its centre
        moved = []
        for sign, axis in ((1, 0), (-1, 0), (1, 1), (-1, 1)):
            if axis == 1 and model_px(counts) > 800:
                break  # would leave the display vertically
            before = bench.host.state()
            bench.probe.write_pair("dbg_rel", sign * counts if axis == 0 else 0, sign * counts if axis == 1 else 0)
            time.sleep(0.06)
            after = bench.host.state()
            moved.append(abs(after["x"] - before["x"]) if axis == 0 else abs(after["y"] - before["y"]))
        measured = sum(moved) / len(moved)
        worst = max(worst, abs(measured - model_px(counts)))
        print(f"{counts:6d}  {measured:11.2f}  {model_px(counts):8.2f}  {measured - model_px(counts):+8.2f}")
        bench.move(1, 0)  # any real motion brings the absolute report back
        bench.move(-1, 0)
        time.sleep(0.05)
    print(f"worst error {worst:.2f} px")


def run_sync(bench, seconds):
    xs = [d[0] for d in bench.host.displays] + [d[0] + d[2] for d in bench.host.displays]
    ys = [d[1] for d in bench.host.displays] + [d[1] + d[3] for d in bench.host.displays]
    top, bottom = min(ys), max(ys)
    seams = sorted(set(xs))[1:-1]
    random.seed(1)
    checks = desyncs = 0
    worst = 0.0
    end = time.time() + seconds
    while time.time() < end:
        # Head for a point just across a seam, at a random height and speed.
        # Edge-hugging runs cover the top and bottom, where an overshooting
        # burst leaves the display.
        seam = random.choice(seams)
        fx, fy, local = bench.fw()
        if not local:
            for _ in range(80):
                bench.move(0, -40)  # come back up from the remote
            continue
        side = 1 if fx < seam else -1
        target_x = seam + side * random.uniform(40, 900)
        target_y = random.choice([top + random.uniform(0, 30), bottom - random.uniform(60, 90), random.uniform(top + 100, bottom - 200)])
        speed = random.choice([3, 8, 20, 50, 120, 300])
        for _ in range(400):
            fx, fy, local = bench.fw()
            dx, dy = target_x - fx, target_y - fy
            dist = math.hypot(dx, dy)
            if dist < 4 or not local:
                break
            step = min(speed * random.uniform(0.6, 1.4), dist)
            bench.move(dx / dist * step, dy / dist * step)
        ex, ey, local = bench.error()
        if not local:
            continue
        checks += 1
        miss = math.hypot(ex, ey)
        worst = max(worst, miss)
        if miss > 30:
            desyncs += 1
            fx, fy, _ = bench.fw()
            print(f"DESYNC host-fw=({ex:+.0f},{ey:+.0f}) at fw=({fx:.0f},{fy:.0f}) speed={speed} target=({target_x:.0f},{target_y:.0f})")
            # Sweep across the seam and back so the firmware's own crossings line the two up again.
            for direction in (-side, side, side, -side):
                for _ in range(60):
                    bench.move(direction * 120, 0)
    print(f"{checks} runs, {desyncs} desyncs, worst settled error {worst:.1f} px")
    return desyncs


def run_drag(bench, mode):
    if bench.host.state()["locked"]:
        sys.exit("screen is locked: the pad cannot take the clicks")
    px, py, pw, ph = bench.host.pad
    centre = (px + pw / 2, py + ph / 2)
    bench.probe.write8("drag_mode", mode)
    k = bench.scale / bench.sensitivity
    strokes = {
        "slow": [(2, 1)] * 120,
        "medium": [(9, -4)] * 50,
        "fast": [(35, 12)] * 16,
        "flick": [(8, 0), (30, 5), (70, 10), (110, 12), (70, 8), (30, 4), (8, 0)],
        "there and back": [(14, 6)] * 30 + [(-14, -6)] * 30,
        "mixed": [(1, 0)] * 20 + [(25, -10)] * 8 + [(3, 3)] * 30 + [(-40, 0)] * 6 + [(0, 20)] * 10,
    }

    def run(dx, dy, travel):
        """Samples of (dx, dy) that carry the host about travel px."""
        # The firmware emits drag reports in px-sized counts, so (dx, dy) is the report.
        per_sample = model_px(math.floor(math.hypot(dx, dy)))
        return [(dx, dy)] * max(1, round(min(travel, pw - 240) / per_sample))

    # Wide strokes start at the pad's left edge and cross about half the display.
    width = next(d[2] for d in bench.host.displays if d[0] <= px < d[0] + d[2])
    half = 0.5 * width
    strokes.update({
        "wide fast": run(35, 0, half),
        "wide very fast": run(70, 0, half),
        "wide flick": [(20, 0), (60, 2), (110, 4), (150, 5), (110, 4), (60, 2), (20, 0)],
        "wide diagonal": run(40, 8, half),
        "wide there and back": run(45, 0, half) + run(-45, 0, half),
    })
    print(f"drag_mode {mode}")
    print("stroke                fw_travel  host_travel  width  px/s   error_px(x,y)  held  drag_events")
    for name, samples in strokes.items():
        bench.goto(px + 80 if name.startswith("wide") else centre[0] - 250, centre[1])
        fx0, fy0, _ = bench.fw()
        h0 = bench.host.state()
        # A press that lands anywhere but the pad would drag whatever is under it.
        if not (px + 40 <= h0["x"] <= px + pw - 40 and py + 40 <= h0["y"] <= py + ph - 40):
            sys.exit(f"pointer at ({h0['x']:.0f},{h0['y']:.0f}) is off the pad: not pressing")
        bench.probe.write8("dbg_buttons", 1)
        time.sleep(0.1)
        if bench.host.state()["downs"] == h0["downs"]:
            bench.probe.write8("dbg_buttons", 0)
            sys.exit("the pad did not receive the press: released and stopped")
        held = True
        started, peak = time.time(), 0.0
        for dx, dy in samples:
            bench.probe.write_pair("dbg_move", round(dx * k), round(dy * k))
            time.sleep(SAMPLE_S)
            peak = max(peak, abs(bench.fw()[0] - fx0))
        elapsed = time.time() - started
        time.sleep(0.05)
        held = held and bench.host.state()["left"]
        bench.probe.write8("dbg_buttons", 0)
        time.sleep(0.15)  # hands still: the post-release correction stays unsent
        fx1, fy1, _ = bench.fw()
        h1 = bench.host.state()
        fw_travel = math.hypot(fx1 - fx0, fy1 - fy0)
        host_travel = math.hypot(h1["x"] - h0["x"], h1["y"] - h0["y"])
        print(f"{name:20s}  {fw_travel:9.1f}  {host_travel:11.1f}  {100 * peak / width:4.0f}%  {peak / elapsed:5.0f}  ({h1['x'] - fx1:+6.1f},{h1['y'] - fy1:+6.1f})  {str(held):5s} {h1['drags'] - h0['drags']:6d}")
        bench.move(1, 0)
        bench.move(-1, 0)


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what not in ("curve", "sync", "drag"):
        sys.exit(__doc__)
    bench = Bench(pad=(what == "drag"))
    try:
        if what == "curve":
            run_curve(bench)
        elif what == "sync":
            sys.exit(1 if run_sync(bench, float(sys.argv[2]) if len(sys.argv) > 2 else 60) else 0)
        else:
            run_drag(bench, int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    finally:
        bench.close()


if __name__ == "__main__":
    main()
