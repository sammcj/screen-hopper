#!/usr/bin/env python3
"""Watch Pico A's keep-awake jiggle globals live over the Debug Probe (no halt).

Polls last_jiggle_us / next_jiggle_gap_us / jiggle_accum_x / jiggle_accum_y /
jiggle_rng_state and prints one line each time a jiggle fires (last_jiggle_us
changes), showing the freshly chosen random gap to the next repeat and the
per-axis step taken. Lets you confirm the magnitude, direction and inter-jiggle
timing are randomised.

Addresses come from argv (they shift every rebuild - pull them from the ELF with
arm-none-eabi-nm). Must run UNSANDBOXED (USB).

Usage: jigglewatch.py <dur_s> <last_jiggle_hex> <gap_hex> <accx_hex> <accy_hex> <rng_hex>
"""
import socket, subprocess, sys, time, signal

DUR   = float(sys.argv[1])
A_LJ  = int(sys.argv[2], 0)
A_GAP = int(sys.argv[3], 0)
A_AX  = int(sys.argv[4], 0)
A_AY  = int(sys.argv[5], 0)
A_RNG = int(sys.argv[6], 0)

oo = subprocess.Popen(
    ["openocd", "-f", "interface/cmsis-dap.cfg", "-f", "target/rp2350.cfg",
     "-c", "adapter speed 5000", "-c", "init"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    s = None
    for _ in range(100):
        try:
            s = socket.create_connection(("127.0.0.1", 6666), timeout=1.0); break
        except OSError:
            time.sleep(0.1)
    if s is None:
        print("could not connect to openocd tcl (probe wedged? replug)", file=sys.stderr); sys.exit(1)
    sock = s

    def cmd(c):
        sock.sendall(c.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            buf += sock.recv(4096)
        return buf[:-1].decode(errors="replace")

    def u64(addr):
        lo, hi = (int(x, 0) for x in cmd(f"read_memory 0x{addr:08x} 32 2").split())
        return lo | (hi << 32)

    def u32(addr):
        return int(cmd(f"read_memory 0x{addr:08x} 32 1").split()[0], 0)

    def s16(addr):
        v = int(cmd(f"read_memory 0x{addr:08x} 16 1").split()[0], 0)
        return v - (1 << 16) if v >= (1 << 15) else v

    prev_lj = u64(A_LJ)
    prev_ax, prev_ay = s16(A_AX), s16(A_AY)
    print(f"start: last_jiggle_us={prev_lj} gap_us={u64(A_GAP)} "
          f"accum=({prev_ax},{prev_ay}) rng=0x{u32(A_RNG):08x}")
    print("watching for jiggles (interval 60s) ... fires print below:\n")
    gaps = []
    t0 = time.time()
    while time.time() - t0 < DUR:
        lj = u64(A_LJ)
        if lj != prev_lj:
            gap = u64(A_GAP); ax, ay = s16(A_AX), s16(A_AY); rng = u32(A_RNG)
            dx, dy = ax - prev_ax, ay - prev_ay
            print(f"[{time.time()-t0:6.1f}s] JIGGLE  step=({dx:+d},{dy:+d})  "
                  f"accum=({ax:+d},{ay:+d})  next_gap={gap/1e6:.2f}s  rng=0x{rng:08x}")
            gaps.append(gap/1e6)
            prev_lj, prev_ax, prev_ay = lj, ax, ay
        time.sleep(0.3)
    if gaps:
        print(f"\n{len(gaps)} jiggle(s). next_gap range: {min(gaps):.2f}..{max(gaps):.2f}s "
              f"(allowed 51.00..60.00 for interval 60)")
    else:
        print("\nno jiggles seen - target output may not have been idle 60s, "
              "or you're driving the inactive side")
finally:
    oo.send_signal(signal.SIGTERM)
    try: oo.wait(timeout=3)
    except Exception: oo.kill()
