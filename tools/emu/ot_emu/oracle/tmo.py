#!/usr/bin/env python3
"""Run one command with a wall-clock limit in its own process group, stdout to
a file; on timeout kill the whole group (only what we started). macOS has no
`timeout(1)` by default.

    tmo.py SECONDS STDOUT_FILE -- cmd args...
exit: the command's own code, 124 on timeout."""
import os
import signal
import subprocess
import sys
import time

secs = float(sys.argv[1])
outp = sys.argv[2]
assert sys.argv[3] == "--"
cmd = sys.argv[4:]
t0 = time.perf_counter()
with open(outp, "w") as o, open(outp + ".stderr", "w") as e:
    p = subprocess.Popen(cmd, stdout=o, stderr=e, stdin=subprocess.DEVNULL, start_new_session=True)

    def on_term(*_):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        sys.exit(143)
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    try:
        rc = p.wait(timeout=secs)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()
        rc = 124
        e.write(f"tmo.py: TIMEOUT after {secs} s\n")
with open(outp + ".wall", "w") as w:
    w.write(f"{time.perf_counter() - t0:.2f} {rc}\n")
sys.exit(rc)
