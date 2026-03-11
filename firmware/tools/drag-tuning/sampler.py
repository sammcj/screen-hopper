#!/usr/bin/env python3
"""Fast non-halting sampler of Pico A cursor_x/cursor_y/active_screen/defer via a
persistent openocd TCL-RPC session. Records at ~990Hz for DURATION seconds.

The Raspberry Pi Debug Probe (CMSIS-DAP) reads RP2350 RAM through the AHB-AP
WITHOUT halting the core - openocd's "not halted" messages just mean the core
never stopped, so there's no input freeze. Must run UNSANDBOXED (USB access).

Addresses come from the environment (see symbols.sh); the fallbacks are only a
last resort and WILL be wrong after a rebuild:
    source <(./symbols.sh) && ./sampler.py 10 out.csv

Usage: sampler.py [duration_s] [out_csv]
"""
import socket, subprocess, sys, time, os, signal

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
DEFAULT_DIR = os.environ.get("DRAG_DIR") or (os.environ.get("TMPDIR", "/tmp").rstrip("/") + "/sh-drag")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DEFAULT_DIR, "fw_trace.csv")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

# Addresses: env first (symbols.sh), then stale fallbacks. int(x,0) handles 0x...
ADDR_CURSOR = int(os.environ.get("ADDR_CURSOR", "0x20020370"), 0)  # int64 cursor_x, int64 cursor_y
ADDR_ACTIVE = int(os.environ.get("ADDR_ACTIVE", "0x200235ed"), 0)  # u8 active_screen
ADDR_DEFER  = int(os.environ.get("ADDR_DEFER",  "0x200235f1"), 0)  # u8 defer_abs_after_drag

oo = subprocess.Popen(
    ["openocd", "-f", "interface/cmsis-dap.cfg", "-f", "target/rp2350.cfg",
     "-c", "adapter speed 5000", "-c", "init"],   # 5 MHz SWD; default tcl port 6666
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    s = None
    for _ in range(100):
        try:
            s = socket.create_connection(("127.0.0.1", 6666), timeout=1.0)
            break
        except OSError:
            time.sleep(0.1)
    if s is None:
        print("could not connect to openocd tcl port (probe wedged? replug it)", file=sys.stderr)
        sys.exit(1)
    sock = s

    def cmd(c):
        sock.sendall(c.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            buf += sock.recv(4096)
        return buf[:-1].decode(errors="replace")

    def sample():
        w = cmd(f"read_memory 0x{ADDR_CURSOR:08x} 32 4").split()
        a = cmd(f"read_memory 0x{ADDR_ACTIVE:08x} 8 1").split()
        d = cmd(f"read_memory 0x{ADDR_DEFER:08x} 8 1").split()
        vals = [int(x, 0) for x in w]
        cx = vals[0] | (vals[1] << 32)
        cy = vals[2] | (vals[3] << 32)
        if cx >= (1 << 63): cx -= (1 << 64)
        if cy >= (1 << 63): cy -= (1 << 64)
        return cx, cy, int(a[0], 0), int(d[0], 0)

    rows = []
    t0 = time.time()
    while time.time() - t0 < DUR:
        t = time.time()
        cx, cy, asc, defer = sample()
        rows.append((t, cx, cy, asc, defer))

    with open(OUT, "w") as f:
        f.write("epoch,cursor_x,cursor_y,active_screen,defer\n")
        for r in rows:
            f.write(f"{r[0]:.4f},{r[1]},{r[2]},{r[3]},{r[4]}\n")
    rate = len(rows) / (rows[-1][0] - rows[0][0]) if len(rows) > 1 else 0
    print(f"{len(rows)} samples in {DUR}s -> {rate:.0f} Hz, wrote {OUT}")
    print(f"last: cursor_x={rows[-1][1]} cursor_y={rows[-1][2]} active={rows[-1][3]} defer={rows[-1][4]}")
finally:
    oo.send_signal(signal.SIGTERM)
    try: oo.wait(timeout=3)
    except Exception: oo.kill()
