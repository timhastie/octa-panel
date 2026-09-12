#!/usr/bin/env python3
"""The oracle's --interactive battery: drive ONE ot_emu binary through a fixed
key/knob sequence and write everything it answered into an output directory,
so two directories (reference vs candidate) can be diffed byte for byte.

    drive.py --emu BIN --out DIR [--dsp] [--timeout S] [--image BIN] [--card IMG]
             [--set NAME] [--project NAME] [--rtc EPOCH]

Sequence (emulated time, see oracle.sh for the rationale):
  boot on the card (--mount --set <set> --project <project> --internal-clock
  --rtc <fixed epoch>), `status`, `frame on`, then
  YES, MIXER, NO, T1 double tap, DOWN, RIGHT, NO, NO, [audio start main], PLAY,
  run 2000 (20 x 100 ms, `audio read` after each with --dsp), STOP, run 500
  (5 x 100 ms), `audio status`, `status`, quit.
`tx` is captured after every step, plus a `peek` of the screen-relevant state.

Files written (all deterministic given the binary; wall-clock fields stripped):
  boot.log    stdout before `ready` (the boot log; output paths normalised)
  ready.txt   the `ready sample= frames=` line
  steps.txt   every command and its reply, with `run` stamps and `wall=` cut out
  stamps.txt  one line per `run`: <step> <sample> <frames> <stop>
  tx.bin      UART A's transmit bytes, concatenated over the whole session
  txlen.txt   the byte count of each `tx` answer, per step (where the blocks landed)
  peeks.txt   the peeks per step
  audio.pcm   (--dsp) every `audio read` concatenated: LE s16 interleaved L,R
  wall.txt    boot wall, run wall, `status` idle=/wall= (NOT compared -- the speed measurement)
  DONE        written last: the battery completed (exit code inside)
"""
import argparse
import os
import pathlib
import signal
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[4]   # tools/emu/ot_emu/oracle/drive.py

YES, NO, MIXER, PLAY, STOP = (0x26, 0x02), (0x26, 0x04), (0x26, 0x01), (0x25, 0x01), (0x24, 0x80)
T1, DOWN, RIGHT = (0x22, 0x01), (0x24, 0x01), (0x24, 0x02)

# What a screen decodes from, beside the UART stream: the sequencer's STEP /
# TICK / (pad) / TRANSPORT bytes, the clock record YES stores, the UI popup
# slot and the popup record it points at, the UI's current track, the page
# kind, PART_PTR and CUR_PATTERN (CONTEXT.md, tools/panel/panel_server.py).
PEEKS = [
    ("seq", 0x800065b4, 8),
    ("clock_record", 0x80000080, 8),
    ("ui_window", 0x460d175c, 4),
    ("popup", 0x46c7d34c, 0x2c),
    ("cur_track", 0x100b14cc, 1),
    ("page_kind", 0x460d1684, 1),
    ("part_ptr", 0x46c82456, 4),
    ("cur_pattern", 0x80000004, 1),
    ("gain_table", 0x80003c60, 4),
]


class Timeout(Exception):
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--image", default=str(ROOT / "out/raw/section_3_MAIN_OS.bin"))
    ap.add_argument("--card", default=str(ROOT / "out/_agents/port/otlive.img"))
    ap.add_argument("--set", default="OTLIVE")
    ap.add_argument("--project", default="PROJECT")
    ap.add_argument("--rtc", default="1000000000")
    ap.add_argument("--dsp", action="store_true")
    ap.add_argument("--timeout", type=float, default=900.0, help="whole-battery wall limit (s)")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in ("DONE",):
        try:
            (out / f).unlink()
        except FileNotFoundError:
            pass

    argv = [a.emu, "--image", a.image, "--interactive", "--card", a.card, "--mount",
            "--set", a.set, "--project", a.project, "--internal-clock", "--rtc", a.rtc]
    if a.dsp:
        argv.append("--dsp")

    def on_alarm(*_):
        raise Timeout()
    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(int(a.timeout))

    steps = open(out / "steps.txt", "w")
    stamps = open(out / "stamps.txt", "w")
    txbin = open(out / "tx.bin", "wb")
    txlen = open(out / "txlen.txt", "w")
    peeks = open(out / "peeks.txt", "w")
    pcm = open(out / "audio.pcm", "wb") if a.dsp else None
    wall = open(out / "wall.txt", "w")
    stderr = open(out / "stderr.txt", "w")

    t0 = time.perf_counter()
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                         text=True, bufsize=1, start_new_session=True)
    rc = 1
    step = "boot"

    def on_term(*_):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        sys.exit(143)
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    run_wall = 0.0
    audio_on = False
    try:
        boot = []
        while True:
            line = p.stdout.readline()
            if not line:
                raise SystemExit("emulator exited before ready:\n" + "".join(boot[-20:]))
            if line.startswith("ready "):
                (out / "ready.txt").write_text(line)
                break
            boot.append(line.replace(str(out), "<OUT>").replace(a.emu, "<EMU>"))
        (out / "boot.log").write_text("".join(boot))
        wall.write(f"boot {time.perf_counter() - t0:.2f}\n")

        def cmd(line):
            nonlocal run_wall
            p.stdin.write(line + "\n")
            p.stdin.flush()
            t = time.perf_counter()
            reply = p.stdout.readline()
            if not reply:
                raise SystemExit(f"emulator died on {line!r} (step {step})")
            reply = reply.rstrip("\n")
            if line.startswith("run "):
                run_wall += time.perf_counter() - t
            return reply

        def log(line, reply):
            steps.write(f"[{step}] > {line}\n[{step}] < {reply}\n")

        def run(ms):
            r = cmd(f"run {ms}")
            f = dict(kv.split("=") for kv in r.split()[1:])
            stamps.write(f"{step} {ms} {f['sample']} {f['frames']} {f['stop']}\n")
            log(f"run {ms}", f"ok stop={f['stop']}")
            if f["stop"] != "time":
                raise SystemExit(f"run stopped with {f['stop']} at step {step}")

        def key(row, mask):
            r = cmd(f"key {row:#x} {mask:#x}")
            log(f"key {row:#x} {mask:#x}", r)

        def tap(k, hold=60, after=100):
            key(*k)
            run(hold)
            key(k[0], 0)
            run(after)

        def status():
            # `wall=` is the wall clock; `idle=` counts the emulator's own idle
            # skips (a mechanism, not firmware behaviour): both reported, not compared.
            r = cmd("status")
            log("status", " ".join(w for w in r.split() if not w.startswith(("wall=", "idle="))))
            wall.write(f"status {step} {' '.join(w for w in r.split() if w.startswith(('idle=', 'wall=')))}\n")

        def tx():
            r = cmd("tx")
            if not r.startswith("tx "):
                raise SystemExit(f"tx -> {r[:80]!r}")
            b = bytes.fromhex(r[3:])
            txbin.write(b)
            txlen.write(f"{step} {len(b)}\n")
            log("tx", f"tx <{len(b)} bytes>")

        def peek():
            for name, addr, n in PEEKS:
                r = cmd(f"peek {addr:#x} {n}")
                peeks.write(f"{step} {name} {r}\n")

        def audio_read():
            if not audio_on:
                return
            r = cmd("audio read")
            if not r.startswith("audio "):
                raise SystemExit(f"audio read -> {r[:80]!r}")
            _, n, hexs = r.split(" ", 2)
            b = bytes.fromhex(hexs)
            pcm.write(b)
            log("audio read", f"audio {n} <{len(b)} bytes>")

        def checkpoint():
            tx()
            peek()
            audio_read()

        step = "status0"; status()
        step = "frame"; log("frame on", cmd("frame on"))
        step = "yes"; tap(YES, 60, 300); checkpoint()
        step = "mixer"; tap(MIXER); checkpoint()
        step = "no1"; tap(NO); checkpoint()
        step = "t1x2"; key(*T1); run(50); key(T1[0], 0); run(150); key(*T1); run(50); key(T1[0], 0); run(100); checkpoint()
        step = "down"; tap(DOWN); checkpoint()
        step = "right"; tap(RIGHT); checkpoint()
        step = "no2"; tap(NO); checkpoint()
        step = "no3"; tap(NO); checkpoint()
        if a.dsp:
            step = "audio"; log("audio start main", cmd("audio start main")); audio_on = True
        step = "play"; tap(PLAY); checkpoint()
        for i in range(20):
            step = f"play{i:02d}"; run(100); audio_read()
        step = "played"; checkpoint()
        step = "stop"; tap(STOP); checkpoint()
        for i in range(5):
            step = f"stop{i:02d}"; run(100); audio_read()
        step = "stopped"; checkpoint()
        if a.dsp:
            step = "audiostatus"; log("audio status", cmd("audio status"))
        step = "status1"; status()
        step = "quit"; log("quit", cmd("quit"))
        rc = p.wait(timeout=60)
        wall.write(f"run {run_wall:.2f}\ntotal {time.perf_counter() - t0:.2f}\nexit {rc}\n")
    except Timeout:
        wall.write(f"TIMEOUT at step {step} after {time.perf_counter() - t0:.1f} s\n")
        print(f"drive.py: TIMEOUT at step {step}", file=sys.stderr)
        rc = 124
    except SystemExit as e:
        wall.write(f"FAILED at step {step}: {e}\n")
        print(f"drive.py: {e}", file=sys.stderr)
        rc = 2
    finally:
        signal.alarm(0)
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
        for f in (steps, stamps, txbin, txlen, peeks, pcm, wall, stderr):
            if f:
                f.close()
        (out / "DONE").write_text(f"{rc}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
