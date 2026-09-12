#!/usr/bin/env python3
"""The virtual front panel: the emulated unit with a clickable UI.

Serves a browser page that shows the firmware's REAL screen -- the LCD
framebuffer the draw primitives paint at FB (found 10 Sep 2026 by hooking
memory writes from the draw-primitive region during a menu draw: 16
bytes/row, MSB left, 128x64) -- and injects key presses through the
firmware's own per-key jump table (KEY_TABLE, RTOS_FORK.md section 9), the
same path `press_key_live` proved against the M6c fidelity gate.

Two emulator backends (12 Sep 2026), the same Panel code over either:

  * `port`  -- the C++ ColdFire port, `out/emu/ot_emu --interactive`, over
    pipes (PortProc/PortRt below; protocol in the PortProc docstring). The
    default when the binary exists: boot + the fixture project in 21 s wall
    (route A ~95 s), 350 emulated ms per wall s while playing (route A
    60-100; measured 12 Sep 2026), and it loads the project itself during
    its boot. Missing binary: built here; a build
    that fails, or a binary without --interactive, falls back to route A
    with the reason in /status "backend_note".
  * `routea` -- emu_rtos (Unicorn): the real scheduler, so the UI task
    consumes what a key handler posts and repaints -- a handler called under
    route B changes state but nothing redraws (measured: 15 handlers, zero
    framebuffer changes). --backend routea, or the fallback.

    .venv/bin/python3 tools/panel/panel_server.py                  # built image, empty card
    .venv/bin/python3 tools/panel/panel_server.py --image out/raw/section_3_MAIN_OS.bin
    .venv/bin/python3 tools/panel/panel_server.py --project <dir> [--set S] [--name N]
    .venv/bin/python3 tools/panel/panel_server.py --backend routea   # the Python oracle

Then open http://localhost:8563/. Unmapped keys: the MAP drawer lists every
table entry; click one, watch the screen, name it. The mapping lives in the
browser (localStorage) and exports as JSON -- send a completed map back as
a PR to key_map.json.
"""
import argparse
import collections
import json
import os
import pathlib
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools")); import toolpath  # noqa: E402,F401

import emu_card as ec  # noqa: E402
import emu_rtos as er  # noqa: E402

FB = 0x460d1f80          # LCD framebuffer, COLUMN-major: 128 columns x 8 bytes,
                         # bit 7 of a page byte is the LOWEST of its 8 rows --
                         # pixel(x,y) = buf[x*8 + (63-y)//8] bit (7 - (63-y)%8)
                         # (verified: renders the SET DATE/TIME dialog legibly)
                         # -- a FALLBACK only: it is not a copy of what the panel
                         # shows (no page rotation, row/column or bit order maps
                         # it onto the decoded stream; best 669/1024 bytes wrong
                         # on AMP SETUP, 12 Sep 2026, KEYMAP.md)
UI_WINDOW = 0x460d175c      # the UI's popup slot. NOT "a SETUP window or 0" (the first
                            # version annotated /knob on nonzero and so on every page
                            # after a YES/NO, verifier 12 Sep 2026): it is POPUP while
                            # ANY popup is drawn from that record -- the SET DATE/TIME
                            # dialog, MIXER, TEMPO, PATTERN SETTINGS, the PROJECT menu,
                            # ARM ALL / DISARM ALL and the page SETUP windows -- and
                            # 0x46c7d384 (the record after it, flags closed) on the
                            # main screen and page 1 after YES closed the dialog; 0
                            # after a popup closes (fix_lab.log, fix_lab2.log).
POPUP = 0x46c7d34c          # the popup record: +0x08 x0, +0x0c y0, +0x18 x1, +0x20 flags
                            # (0x21 open, 0x01 closed), +0x28 rows. What tells the page
                            # SETUP windows apart is their geometry: every one of them
                            # (PLAYBACK/AMP/LFO/FX1/FX2, the second press of the page
                            # key) is x 7..0xf4, y 0, 0x40 rows, and nothing else is
                            # (MIXER 10/0/0xec/0x40, PROJECT menu 5/0/0xf6/0x40, TEMPO
                            # 0x1c/8/0xca/0x30, PATTERN SETTINGS 8/3/0xf2/0x3a, ARM ALL
                            # 0x25/0x17/0xb8/0x12, DISARM ALL 0x1e/0x17/0xc6/0x12, the
                            # clock dialog 0xf/7/0xe6/0x32; measured 12 Sep 2026,
                            # KEYMAP.md). The record keeps its last geometry after a
                            # close, hence the slot check as well.
SETUP_GEOMETRY = (7, 0, 0xf4, 0x40)


def setup_window_open(uc):
    """True while a page's SETUP window is the popup on screen (see POPUP)."""
    if int.from_bytes(uc.mem_read(UI_WINDOW, 4), "big") != POPUP:
        return False
    rec = bytes(uc.mem_read(POPUP, 0x2c))
    word = lambda off: int.from_bytes(rec[off:off + 4], "big")  # noqa: E731
    return ((word(0x08), word(0x0c), word(0x18), word(0x28)) == SETUP_GEOMETRY
            and bool(word(0x20) & 0x20))


KEY_TABLE = 0x400d2954   # per-key jump table: 66 longword handlers
KEY_COUNT = 66
IDX_REC, IDX_PLAY, IDX_STOP = 27, 28, 29   # measured: 0x4000a274/0x4000a200/0x4000a1e0


def install_rtc(clock=None):
    """Put a real-time clock on the DSPI (found 10 Sep 2026): the firmware
    reads a DS1390-style SPI RTC on chip-select 2 -- one transaction per
    register (`<reg> 00`, CONT held between the two frames), registers
    0x01 sec, 0x02 min, 0x03 hour, 0x04 weekday, 0x05 date, 0x06 month,
    0x07 year, all BCD; 0x00 hundredths, 0x0e status; one write `9e 07`
    (0x1e := 7) at boot. Stock emu_rtos.Dspi answers 0 to everything, which
    is exactly the 2000-00-00 the SET DATE/TIME dialog shows. Replies must
    be delimited by the CONT bit, not by counting frames (one boot-time
    transaction is three frames long). Writes to time registers are kept,
    so setting the clock through the dialog sticks for the session."""
    import datetime
    regs = {}
    def bcd(n):
        return ((n // 10) << 4) | (n % 10)
    def now_regs():
        t = clock() if clock else datetime.datetime.now()
        return {0x00: 0, 0x01: bcd(t.second), 0x02: bcd(t.minute), 0x03: bcd(t.hour),
                0x04: t.isoweekday(), 0x05: bcd(t.day), 0x06: bcd(t.month),   # 1 = Monday (measured: 4 draws THURSDAY)
                0x07: bcd(t.year % 100), 0x0e: 0}
    def write(self, off, size, val, replay=False):
        if off == self.PUSHR:
            if replay:
                return
            attr, data = val >> 16, val & 0xff
            cont, pcs = bool(attr & 0x8000), attr & 0x3f
            st = getattr(self, "_tx", None)
            rep = 0
            if st is None:
                st = self._tx = [pcs, data, 0]
                if pcs == 2 and not data & 0x80:
                    rep = {**now_regs(), **regs}.get(data & 0x7f, 0)
            else:
                st[2] += 1
                reg = (st[1] & 0x7f) + st[2] - 1
                if st[0] == 2:
                    if st[1] & 0x80:
                        regs[reg] = data          # a write: remember it
                    else:
                        rep = {**now_regs(), **regs}.get(reg, 0)
            self.rx.append(rep)
            self.pushed += 1
            if not cont:
                self._tx = None
        elif off != self.SR:
            self.regs[off] = val
    er.Dspi.write = write


def _png_gray(w, h, rows):
    """Minimal 8-bit grayscale PNG (stdlib only)."""
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# -- the port backend: out/emu/ot_emu --interactive over pipes -----------------

PORT_BIN = ROOT / "out/emu/ot_emu"
PORT_SRC = ROOT / "tools/emu/ot_emu"
PORT_BUILD = ROOT / "out/emu"


class PortDied(Exception):
    """The port process is gone or silent: the loop kills it and respawns."""


class PortError(Exception):
    """The port answered `err <message>` (bad address, bad command)."""


class PortProc:
    """One `ot_emu --interactive` child: a line out, a line back, flushed.

    The protocol (agreed 12 Sep 2026 with the port's side, built against the
    same text): the child boots exactly as the CLI does (project load
    included when --mount/--set/--project are given), prints its usual
    report, then one line `ready sample=<double> frames=<u64>` and serves
    stdin, one command per line, exactly one line per command:

        run <ms>            ok sample=<double> frames=<u64> stop=<word>
        key <row> <mask>    ok        (two bytes into UART A's receive queue)
        knob <row> <delta>  ok        (signed delta; pushes row, delta&0xff)
        tx                  tx <hex>  (UART A bytes since the previous tx)
        peek <addr> <len>   peek <hex> (len <= 4096; unmapped -> err)
        poke <addr> <hex>   ok
        frame on|off        ok        (Rtos::setFrame)
        status              status sample= ms= frames= frame= idle= wall=
        quit                ok, exit 0
        (failure)           err <message>, keeps serving

    A reader thread queues stdout lines so a wait can time out; lines that
    are not the expected reply (the port's own report before `ready`, any
    printf noise) go to `log` (last 200) instead of being mistaken for one.
    stderr goes to `log_path`."""

    BACKSTOP = 120.0        # wall seconds a reply may take before the command is given up
                            # (the watchdog kills a hung child long before: Panel.ACTION_LIMIT)

    def __init__(self, argv, log_path=None):
        self.argv = [str(a) for a in argv]
        self.log = collections.deque(maxlen=200)
        self.lines = queue.Queue()
        self.lock = threading.Lock()
        self.ready = None               # (sample, frames) from the ready line
        self.commands = 0
        self.started = time.perf_counter()
        err = open(log_path, "ab") if log_path else subprocess.DEVNULL
        self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=err, bufsize=0, cwd=str(ROOT))
        if log_path:
            err.close()
        threading.Thread(target=self._reader, daemon=True, name="port-stdout").start()

    def _reader(self):
        for raw in iter(self.proc.stdout.readline, b""):
            self.lines.put(raw.decode("ascii", "replace").rstrip("\r\n"))
        self.lines.put(None)            # EOF: the child exited

    def tail(self, n=6):
        return " | ".join(list(self.log)[-n:])

    def alive(self):
        return self.proc.poll() is None

    def wait_ready(self, timeout):
        """Consume the boot report until the ready line; PortDied on exit or silence."""
        deadline = time.perf_counter() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.0, deadline - time.perf_counter()))
            except queue.Empty:
                self.kill()
                raise PortDied(f"no ready line within {timeout:.0f} s; last: {self.tail()}")
            if line is None:
                raise PortDied(f"exited (rc {self.proc.poll()}) before ready; last: {self.tail()}")
            if line.startswith("ready "):
                f = dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv)
                self.ready = (float(f.get("sample", 0)), int(f.get("frames", 0)))
                return self.ready
            self.log.append(line)

    def command(self, line, expect, timeout=None):
        """Send one line, return the reply that starts with `expect`.
        `err ...` raises PortError; a dead or silent child raises PortDied."""
        timeout = self.BACKSTOP if timeout is None else timeout
        with self.lock:
            try:
                self.proc.stdin.write(line.encode("ascii") + b"\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise PortDied(f"write failed on {line!r}: {e}")
            self.commands += 1
            deadline = time.perf_counter() + timeout
            while True:
                try:
                    reply = self.lines.get(timeout=max(0.0, deadline - time.perf_counter()))
                except queue.Empty:
                    self.kill()
                    raise PortDied(f"no reply to {line!r} within {timeout:.0f} s")
                if reply is None:
                    raise PortDied(f"exited (rc {self.proc.poll()}) on {line!r}; last: {self.tail()}")
                head = reply.split(" ", 1)[0]
                if head == expect:
                    return reply
                if head == "err":
                    raise PortError(reply[4:])
                self.log.append(reply)      # noise, not a reply: keep waiting

    def quit(self):
        try:
            if self.alive():
                self.command("quit", "ok", timeout=2.0)
        except (PortDied, PortError):
            pass
        self.kill()

    def kill(self):
        if self.alive():
            try:
                self.proc.kill()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=2.0)
        except Exception:
            pass


class SpeedMeter:
    """Emulated ms per wall second, averaged over the last WINDOW wall
    seconds of run() calls (idle runs return at once, so idle reads high)."""
    WINDOW = 5.0

    def __init__(self):
        self.samples = collections.deque()      # (t_end, ms, wall)
        self.lock = threading.Lock()

    def add(self, ms, wall):
        now = time.perf_counter()
        with self.lock:
            self.samples.append((now, ms, wall))
            while self.samples and self.samples[0][0] < now - self.WINDOW:
                self.samples.popleft()

    @property
    def value(self):
        with self.lock:
            ms = sum(s[1] for s in self.samples)
            wall = sum(s[2] for s in self.samples)
        return round(ms / wall, 1) if wall > 1e-3 else None


class PortRx:
    """`rt.uart64.rx` for the port: extend([row, byte]) sends key/knob."""

    def __init__(self, rt):
        self.rt = rt

    def extend(self, seq):
        seq = list(seq)
        for i in range(0, len(seq) - 1, 2):
            row, val = int(seq[i]) & 0xff, int(seq[i + 1]) & 0xff
            if 0x30 <= row < 0x40:
                delta = val - 256 if val > 127 else val
                self.rt.proc.command(f"knob {row:#04x} {delta}", "ok")
            else:
                self.rt.proc.command(f"key {row:#04x} {val:#04x}", "ok")

    def append(self, b):
        raise TypeError("send whole [row, byte] reports with extend()")


class PortUart:
    def __init__(self, rt):
        self.name = "UART@fc064000 (port)"
        self.rx = PortRx(rt)
        self.tx = bytearray()           # grows as PortRt.poll_tx() drains the child


class PortMem:
    """`rt.uc` for the port: mem_read/mem_write over peek/poke (4 KB chunks)."""
    CHUNK = 4096

    def __init__(self, rt):
        self.rt = rt

    def mem_read(self, addr, n):
        out = bytearray()
        while n > 0:
            k = min(n, self.CHUNK)
            rep = self.rt.proc.command(f"peek {addr:#x} {k}", "peek")
            data = bytes.fromhex(rep[5:].strip())
            if len(data) != k:
                raise PortError(f"peek {addr:#x} {k}: {len(data)} bytes back")
            out += data
            addr += k; n -= k
        return out

    def mem_write(self, addr, data):
        data = bytes(data)
        for i in range(0, len(data), self.CHUNK):
            self.rt.proc.command(f"poke {addr + i:#x} {data[i:i + self.CHUNK].hex()}", "ok")


class PortRt:
    """What the Panel uses of emu_rtos.Rtos, over the interactive protocol:
    run(ms=), uart64.rx/tx, uc.mem_read/mem_write, sample, frame,
    pattern_base(), poke_trig(), internal_clock(). The jump-table paths
    (press_key_live, call_as_main, load_project_live, gate_m6a) are route
    A's own and are not here; Panel.press()/transport() say so."""

    SLICE_MS = 20.0         # run_ms() slice: a round trip each, ~0.1 s wall while playing

    def __init__(self, proc, meter=None):
        self.proc = proc
        self.sample, self.frames = proc.ready
        self.stop_reason = None
        self.meter = meter
        self._frame = False
        self.next_frame = None          # set by Panel._before_play; the port keeps its own
        self.uart64 = PortUart(self)
        self.uc = PortMem(self)

    def run(self, ms=None, until=None, max_bursts=None):
        if ms is None or until is not None or max_bursts is not None:
            raise TypeError("the port backend runs by ms only")
        t0 = time.perf_counter()
        rep = self.proc.command(f"run {float(ms):g}", "ok")
        wall = time.perf_counter() - t0
        f = dict(kv.split("=", 1) for kv in rep.split()[1:] if "=" in kv)
        self.sample = float(f.get("sample", self.sample))
        self.frames = int(f.get("frames", self.frames))
        self.stop_reason = f.get("stop", "?")
        if self.meter is not None:
            self.meter.add(float(ms), wall)
        return self.stop_reason

    def poll_tx(self):
        """Drain UART A's transmit bytes into uart64.tx; returns how many."""
        rep = self.proc.command("tx", "tx")
        data = bytes.fromhex(rep[3:].strip())
        self.uart64.tx += data
        return len(data)

    @property
    def frame(self):
        return self._frame

    @frame.setter
    def frame(self, on):
        self.proc.command(f"frame {'on' if on else 'off'}", "ok")
        self._frame = bool(on)

    def status(self):
        rep = self.proc.command("status", "status")
        return dict(kv.split("=", 1) for kv in rep.split()[1:] if "=" in kv)

    def pattern_base(self):
        """emu_rtos.Rtos.pattern_base, via peek: PART_PTR's blob + pattern * stride."""
        blob = int.from_bytes(self.uc.mem_read(ec.PART_PTR, 4), "big")
        return blob + self.uc.mem_read(er.CUR_PATTERN, 1)[0] * er.PATTERN_STRIDE

    def poke_trig(self, step):
        """emu_rtos.Rtos.poke_trig, via peek/poke (track 1, step 1-64)."""
        blob = int.from_bytes(self.uc.mem_read(ec.PART_PTR, 4), "big")
        at = blob + 7 - (step - 1) // 8
        v = self.uc.mem_read(at, 1)[0] | (1 << ((step - 1) % 8))
        self.uc.mem_write(at, bytes([v]))
        return v

    def internal_clock(self):
        """emu_rtos.Rtos.internal_clock: clear CLOCK RECEIVE (0x80000028 bit 0)."""
        midi = self.uc.mem_read(er.FW_MIDI_SETTINGS, 1)[0]
        self.uc.mem_write(er.FW_MIDI_SETTINGS, bytes([midi & ~1]))
        return midi


def port_available(port_bin=PORT_BIN, build=True, log=print):
    """(usable, note): the port binary exists and carries --interactive;
    built here when missing (cmake --fresh + build, ~1-2 min). The note says
    what happened; it lands in /status backend_note on a fallback."""
    port_bin = pathlib.Path(port_bin)
    note = ""
    if not port_bin.exists():
        if not build:
            return False, f"{port_bin} missing"
        log(f"port: {port_bin} missing, building (cmake --fresh -B {PORT_BUILD} -S {PORT_SRC}) ...")
        env = dict(os.environ, PATH="/opt/homebrew/bin:" + os.environ.get("PATH", ""))
        try:
            for cmd in (["cmake", "--fresh", "-B", str(PORT_BUILD), "-S", str(PORT_SRC)],
                        ["cmake", "--build", str(PORT_BUILD), "-j8"]):
                r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=900)
                if r.returncode:
                    tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
                    return False, f"port build failed ({' '.join(cmd[:2])} rc {r.returncode}): {' | '.join(tail)}"
        except Exception as e:
            return False, f"port build failed: {type(e).__name__}: {e}"
        note = "port built here; "
        if not port_bin.exists():
            return False, note + f"{port_bin} still missing after the build"
    if port_bin.suffix != ".py" and b"--interactive" not in port_bin.read_bytes():
        return False, note + f"{port_bin} has no --interactive (built before the protocol landed)"
    return True, note.rstrip("; ")


class Panel:
    """Owns the emulator thread; everything Unicorn happens on it."""

    def __init__(self, image, card, pump_ms=25.0, project=None, internal_clock=True,
                 play_pump_ms=10.0, backend="routea", port_bin=PORT_BIN, port_args=(),
                 card_file=None, backend_note="", auto=False):
        self.image = image
        self.card = card                  # the FAT16 card image, bytes (route A takes it as is)
        self.card_file = card_file        # ... and the port reads it from this file
        self.pump_ms = pump_ms
        self.play_pump_ms = play_pump_ms   # the pump while frame mode is on (see _loop)
        self.project = project            # (set_name, project_name) to load at boot
        self.internal_clock = internal_clock
        self.backend = backend            # "port" | "routea"
        self.auto = auto                  # backend was chosen by default: a port boot that fails falls back
        self.backend_note = backend_note  # why the backend is what it is, when it is not the default
        self.port_bin = str(port_bin)
        self.port_args = list(port_args)  # extra flags for the child (e.g. --dsp)
        self.proc = None                  # PortProc while the port backend runs
        self.restarts = 0                 # port respawns (watchdog kills, crashes)
        self.meter = SpeedMeter()         # emulated ms per wall s, both backends
        self.loaded = None                # load_project_live's tuple once done
        self.phase = "booting"            # booting -> loading project -> ready
        self.actions = queue.Queue()
        self.lock = threading.Lock()
        self.frame = b""          # latest PNG
        self.screen_txt = ""      # the same frame as 64 lines of '#'/'.' (/screen.txt)
        self.seq = 0
        self.fault = None
        self.ran_ms = 0.0
        self.booted = False
        self.led_bits = bytearray(64)
        self.led_ids = {}
        self.handlers = self._read_table(image)
        self._new_link()
        threading.Thread(target=self._loop, daemon=True, name="emu").start()

    def _new_link(self):
        """A fresh decoder and stream positions: at start and after a port respawn."""
        try:
            from panel_link import PanelLink     # tools/panel/panel_link.py, when present
            self.link = PanelLink()
        except Exception:
            self.link = None
        self._link_pos = 0
        self._led_pos = 0
        self.row_state = None

    @staticmethod
    def _read_table(image):
        data = pathlib.Path(image).read_bytes()
        off = KEY_TABLE - 0x40000000
        return [struct.unpack(">I", data[off + i * 4:][:4])[0] for i in range(KEY_COUNT)]

    link = None          # PanelLink: the decoded CPU->panel UART stream, when available
    _link_pos = 0

    def _lcd_rows(self, uc):
        """64 rows x 128 bools, top row first -- what the LCD shows.

        Preferred source: the panel UART stream decoded by
        tools/panel/panel_link.py (the firmware sends the LCD its data
        over UART@fc064000 in 0x10 <chunk> <8 bytes> blocks and scrolls
        with its own commands, so the stream is the ground truth). Fallback:
        the RAM framebuffer at FB, whose page ORDER on screen is not fixed
        (renders come out with lines rotated when the firmware scrolls --
        the 11 Sep 2026 "sample list wrapped" report)."""
        if self._feed_link():
            try:
                return self.link.lcd_rows()
            except Exception as e:      # a decoder fault must not blank the panel
                self.fault = f"panel_link: {type(e).__name__}: {e}"
        buf = bytes(uc.mem_read(FB, 1024))
        rows = []
        for y in range(64):
            yy = 63 - y
            page, bit = yy >> 3, 7 - (yy & 7)
            rows.append([(buf[x * 8 + page] >> bit) & 1 for x in range(128)])
        return rows

    def _feed_link(self):
        """Hand the decoder the panel UART bytes sent since the last call.
        False when there is no decoder (the RAM framebuffer is the source)."""
        if self.link is None or not hasattr(self, "rt"):
            return False
        tx = self.rt.uart64.tx
        if len(tx) > self._link_pos:
            self.link.feed(bytes(tx[self._link_pos:]))
            self._link_pos = len(tx)
        return True

    def _snapshot(self, uc):
        # Render only when an LCD block landed since the last render
        # (PanelLink.dirty): the pump calls this every 25 ms of firmware and
        # a render is 0.7 ms (lcd_rows 0.30 + PNG 0.40, measured 12 Sep
        # 2026) -- at idle it was the loop's only work. The RAM fallback
        # has no such flag and renders every call, as before.
        if self._feed_link() and self.frame and not self.link.dirty:
            return
        on, off = b"\x1a", b"\xc9"   # dark pixels on a pale LCD
        lcd = self._lcd_rows(uc)
        rows = [b"".join(on if px else off for px in row) for row in lcd]
        png = _png_gray(128, 64, rows)
        if self.link is not None:
            self.link.dirty = False
        with self.lock:
            if png != self.frame:
                self.frame = png
                self.screen_txt = "\n".join("".join("#" if px else "." for px in row) for row in lcd) + "\n"
                self.seq += 1

    BOOT_TIMEOUT = 900.0     # wall seconds the port may take to print `ready` (boot + load)

    def _uc(self):
        """The memory the RAM-framebuffer fallback reads: Unicorn under route A, peek under the port."""
        return self.r.uc if self.backend == "routea" else self.rt.uc

    def _dismiss_clock(self, rt):
        # Every boot opens SET DATE/TIME (the "last set" record lives in
        # RAM at 0x80000080 and is zero on a fresh boot). YES (matrix
        # 0x26 bit 1) stores the clock and closes it (measured 11 Sep
        # 2026); on the bare main screen YES would instead open the
        # ARM ALL popup, so press it exactly once, here, while the
        # dialog is certainly up. Same under the port (its DSPI has no
        # RTC model: the dialog shows 2000-00-00, and YES still closes it).
        self.phase = "closing the clock dialog"
        rt.run(ms=400)
        rt.uart64.rx.extend([0x26, 0x02]); rt.run(ms=60)
        rt.uart64.rx.extend([0x26, 0x00]); rt.run(ms=300)
        self._poll(rt)
        self._snapshot(self._uc())

    def _boot_routea(self):
        r, rt = er.attach(self.image if self.image != "raw" else None, self.card)
        self.r, self.rt = r, rt
        self._instrument(rt)
        self.booted = True
        self._snapshot(r.uc)
        self._dismiss_clock(rt)
        if self.project:
            self.phase = "loading project (about a minute)"
            self._load_project(rt)
            # Settle before replaying clicks queued during the load: a
            # key event injected as the very first thing after the load
            # wedged emulated time (run(ms=50) never returned, 11 Sep 2026).
            rt.run(ms=300)
            self._snapshot(r.uc)

    def _port_argv(self):
        argv = [self.port_bin]
        if self.port_bin.endswith(".py"):
            argv = [sys.executable, self.port_bin]        # the fake, or any Python stand-in
        argv += ["--image", str(self.image), "--card", str(self.card_file), "--interactive"]
        if self.project:
            set_name, name = self.project
            # The port loads the project in its own boot (--mount posts the
            # card mount, loadProjectLive does the LOAD PROJECT; O7), so no
            # load_project_live here. --internal-clock is the CLI's own flag
            # for the CLOCK RECEIVE clear; internal_clock() below does the
            # same byte through poke in case the child only applies it with
            # --sequencer (main.cpp, 12 Sep 2026).
            argv += ["--mount", "--set", set_name, "--project", name]
            if self.internal_clock:
                argv.append("--internal-clock")
        return argv + self.port_args

    def _boot_port(self):
        """Spawn the child, wait for `ready`, close the clock dialog."""
        self.phase = ("booting the port" + (" (boot + project load, ~1 min)" if self.project else ""))
        log = pathlib.Path(str(self.card_file)).with_suffix(".port.log") if self.card_file else None
        proc = PortProc(self._port_argv(), log_path=log)
        self.proc = proc
        try:
            proc.wait_ready(self.BOOT_TIMEOUT)
            rt = PortRt(proc, meter=self.meter)
            self.rt = rt
            self.booted = True
            if self.project:
                rep = "\n".join(proc.log)
                posted = re.search(r"LOAD PROJECT posted: (\w+)", rep)
                banks = re.search(r"saved_bank: (-?\d+), final bank: (\d+)", rep)
                ready = re.search(r"card ready: (0x[0-9a-f]+)", rep)
                self.loaded = {"mounted": ready.group(1) if ready else None,
                               "posted": (posted.group(1) == "yes") if posted else None,
                               "saved_bank": int(banks.group(1)) if banks else None,
                               "final_bank": int(banks.group(2)) if banks else None,
                               "elapsed_ms": rt.sample / er.SAMPLE_HZ * 1000.0,
                               "port_report": list(proc.log)[-12:]}
                if self.internal_clock:
                    rt.internal_clock()
            self._poll(rt)
            self._snapshot(rt.uc)
            self._dismiss_clock(rt)
            rt.run(ms=300)
            self._poll(rt)
            self._snapshot(rt.uc)
        except BaseException:
            proc.kill()
            raise

    def _boot(self):
        if self.backend == "port":
            try:
                self._boot_port()
            except PortDied as e:
                if not self.auto or self.booted:
                    raise
                # The default choice did not come up (a binary without
                # --interactive exits 2 with its usage; a build that links
                # but faults at boot): route A instead, and say so.
                self.backend_note = f"port did not boot ({e}); running route A"
                print(f"panel: {self.backend_note}")
                self.backend = "routea"
                self.play_pump_ms = min(self.play_pump_ms, 10.0)
                self._boot_routea()
        else:
            self._boot_routea()
        self.phase = "ready"

    def _respawn(self, why):
        """The port child is gone (watchdog kill, crash, silence): boot a
        fresh one. Emulated state starts over -- boot, project load, the
        clock dialog -- and the decoder with it; clicks queued meanwhile
        run against the new child once it is ready."""
        self.restarts += 1
        self.fault = f"port: {why}; respawned ({self.restarts})"
        self.phase = f"restarting the port ({why})"
        if self.proc is not None:
            self.proc.kill()
        self._new_link()
        self.led_bits = bytearray(64); self.led_ids = {}
        with self.lock:
            self.frame = b""; self.screen_txt = ""
        for attempt in range(3):
            try:
                self._boot_port()
                self.phase = "ready"
                return True
            except PortDied as e:
                self.fault = f"port: respawn {attempt + 1} failed: {e}"
                time.sleep(2.0)
        self.phase = "failed"
        return False

    def _instrument(self, rt):
        """Route A: time every rt.run(ms=...) for the speed meter (the port's
        PortRt.run reports to it itself)."""
        orig = rt.run
        def run(ms=None, until=None, max_bursts=None):
            t0 = time.perf_counter()
            try:
                return orig(ms=ms, until=until, max_bursts=max_bursts)
            finally:
                if ms is not None and until is None:
                    self.meter.add(float(ms), time.perf_counter() - t0)
        rt.run = run

    def _poll(self, rt):
        """Port: drain the child's UART A bytes into rt.uart64.tx (one round
        trip). Route A's Uart.tx grows on its own."""
        if isinstance(rt, PortRt):
            rt.poll_tx()

    def _loop(self):
        try:
            self._boot()
        except Exception as e:  # boot is all-or-nothing
            self.fault = f"boot: {type(e).__name__}: {e}"
            self.phase = "failed"
            return
        threading.Thread(target=self._watchdog, daemon=True, name="watchdog").start()
        while True:
            rt = self.rt
            try:
                while True:
                    try:
                        act = self.actions.get_nowait()
                    except queue.Empty:
                        break
                    self.busy_since = time.perf_counter()
                    try:
                        act()
                    finally:
                        self.busy_since = None
                t = time.perf_counter()
                self.busy_since = t
                # Shorter pumps while the sequencer runs: in frame mode 25 ms
                # of firmware is ~0.46 s wall (54 emulated ms per wall s,
                # measured 12 Sep 2026) and a click first waits for the pump
                # in progress, then runs its own 50 ms. 10 ms pumps cut the
                # wait to ~0.18 s; the per-pump work (snapshot 0.7 ms, LED
                # parse) stays under 1 %, and where run() stops does not
                # move any firmware event, so the emulated timing is the
                # same. The emulation itself is the cost: one emulated
                # second is 1.9M emu_start bursts, 2.9M Intc.pending calls,
                # 515k EMAC + 564k ISA-C shims (cProfile, 12 Sep 2026) --
                # all in tools/emu, nothing of it in this file. Under the
                # port the pump stays 25 ms (its own play_pump_ms).
                rt.run(ms=self.pump_ms if not rt.frame else min(self.pump_ms, self.play_pump_ms))
                self._poll(rt)
                self.busy_since = None
                self.ran_ms = rt.sample / er.SAMPLE_HZ * 1000.0
                if isinstance(rt, PortRt) and rt.stop_reason not in ("time", "gate", None, "?") \
                        and not (self.fault or "").startswith("port: run stopped"):
                    self.fault = f"port: run stopped: {rt.stop_reason}"
                self._snapshot(self._uc())
                self._parse_leds(rt)
                # idle bursts return instantly; don't spin the host
                if time.perf_counter() - t < 0.01:
                    time.sleep(0.03)
            except PortDied as e:
                self.busy_since = None
                if not self._respawn(str(e)):
                    return
            except Exception as e:
                self.busy_since = None
                self.fault = f"{type(e).__name__}: {e}"
                time.sleep(0.5)

    def _load_project(self, rt):
        """The CLI's --sequencer preamble (emu_rtos.main), verbatim in
        spirit: wait for the M6a gate, mount + LOAD PROJECT through the real
        sys/engine tasks, then the two compensations RTOS_FORK.md section 7
        documents (the load ends on bank A here where the unit comes up on
        the saved bank; the sequencer's own bank/pattern byte is re-issued),
        and the internal clock so a project saved with CLOCK RECEIVE does not
        wait for MIDI clock that never arrives in emulation."""
        set_name, name = self.project
        if not rt.gate_m6a()[0]:
            rt.run(ms=1000, until=lambda x: x.gate_m6a()[0])
        mounted, posted, saved_bank, final_bank, elapsed = rt.load_project_live(set_name, name)
        if saved_bank is not None and final_bank != saved_bank:
            final_bank = rt.select_bank_live(saved_bank)
        pattern = rt.uc.mem_read(er.CUR_PATTERN, 1)[0]
        rt.seq_select_live(final_bank, pattern)
        if self.internal_clock:
            rt.internal_clock()
        # NOT here: rt.frame / rt.exact_clock(). Both are the sequencer
        # research's fidelity settings and they cost ~100x wall time (50 ms
        # of firmware = 5.5 s, measured 11 Sep 2026) -- every click took 11 s
        # and the panel looked hung. Frame mode goes on with PLAY, off with
        # STOP (_before_play / _after_stop, shared by the matrix path key()
        # and the handler path transport()); the exact clock is never
        # needed for the UI.
        self.loaded = {"mounted": mounted, "posted": posted, "saved_bank": saved_bank,
                       "final_bank": final_bank, "elapsed_ms": elapsed}
        self._snapshot(rt.uc if hasattr(rt, "uc") else self.r.uc)

    def knob(self, row, delta):
        """An encoder turn: matrix rows 0x30-0x36 are the seven encoders and
        the second byte is a SIGNED detent delta (measured 10 Sep 2026: row
        0x30 with +2 moved the MIXER's MAIN field +2, with +16 clamped at
        +15). No release event -- a turn is an event, not a state."""
        if not (0x30 <= row < 0x37):
            return False, "encoder rows are 0x30-0x36"
        delta = max(-127, min(127, int(delta)))
        def act(rt):
            on_setup = row < 0x36 and setup_window_open(rt.uc)
            if on_setup and abs(delta) > 7:
                # The SETUP-page enum editor accumulates and, past about +-9
                # per report, steps the wrong way (PMTR 15 -> 6 on +12,
                # measured 12 Sep 2026); a big coalesced wheel turn from the
                # page would otherwise scramble the field. Deliver it as
                # +-7 reports instead.
                left = delta
                while left:
                    part = max(-7, min(7, left))
                    rt.uart64.rx.extend([row, part & 0xff])
                    rt.run(ms=30)
                    left -= part
            else:
                rt.uart64.rx.extend([row, delta & 0xff])
                rt.run(ms=30)
            note = ""
            if on_setup:
                # A-F on a SETUP page (AMP/LFO/PLAYBACK/FX SETUP, the second
                # press of a page key): the Part byte changes and the box is
                # drawn into RAM, but the firmware sends no LCD block for the
                # edit under the emulator (none within 3 s idle; a LEVEL +1
                # or a held FUNC flushed the box once each after a long run
                # of edits, never reliably) -- the page redraws when it is
                # closed (page key) and reopened (page key twice: the press
                # after a close only re-selects the page). KEYMAP.md, 12 Sep
                # 2026. Single detents only accumulate there (3 per step
                # clockwise, 4 back); +2..+7 is about one step. Page 1
                # flushes every report and gets no note -- also under the
                # ARM/DISARM ALL popup a YES/NO leaves on it (that popup is
                # drawn from the same record with its own geometry).
                note = (" (SETUP page: the value changed; the box redraws after the page key"
                        " closes the page and, pressed twice more, reopens it)")
            return f"row {row:#04x} delta {delta:+d}{note}"
        return self.do(act, timeout=120)

    # PLAY and STOP as the panel scanner reports them (key_map.json, measured
    # 11 Sep 2026): row 0x25 bit 0 and row 0x24 bit 7. FUNC is 0x25 bit 5.
    PLAY_KEY = (0x25, 0)
    STOP_KEY = (0x24, 7)
    FUNC_KEY = (0x25, 5)

    def _before_play(self, rt):
        """What every PLAY needs BEFORE the key lands, whichever path
        delivers it (the matrix report in key(), the jump-table handler in
        transport()). The DSP frame interrupt is what steps the sequencer,
        so frame mode goes on (~17x wall time while it is on; off again in
        _after_stop). Without this the matrix PLAY only flipped the
        transport word and lit the PLAY LED: the position bar never moved
        (12 Sep 2026).

        Nothing is written into the pattern any more: the per-track byte
        activate_tracks used to set is PLAYS FREE, and FW_TRANSPORT(0)
        skips every track whose byte is set -- setting all eight was what
        kept the running light and the fired-trig LEDs off (KEYMAP.md
        "the trig-row running light", 12 Sep 2026). The fixture's bytes are
        clear and it plays as saved."""
        if not rt.frame:
            rt.frame = True
            rt.next_frame = rt.sample + er.FRAME_PERIOD
        return [rt.uc.mem_read(rt.pattern_base() + 84 + 2330 * t, 1)[0] for t in range(8)]

    def _after_stop(self, rt):
        rt.frame = False

    def transport(self, what):
        """PLAY / REC / STOP through the firmware's own key handlers
        (press_key_live, RTOS_FORK.md section 9) -- the fallback the page
        used for these keys before their matrix cells were measured; kept
        for scripting. Same helpers around the key as key() uses."""
        if what not in ("play", "rec", "stop"):
            return False, "play|rec|stop"
        if self.backend == "port":
            # No jump table over the pipe: the same key through the matrix
            # (PLAY 0x25.0, REC 0x25.1, STOP 0x24.7), down then up, with
            # the helpers key() wraps around PLAY/STOP.
            row, bit = {"play": self.PLAY_KEY, "rec": (0x25, 1), "stop": self.STOP_KEY}[what]
            def tap(rt):
                down = self._key_act(rt, row, bit, True)
                up = self._key_act(rt, row, bit, False)
                return f"matrix tap: {down}; {up}"
            return self.do(tap, timeout=120)
        if what == "play":
            # PLAY's own handler + FW_START_TRACK per track, as
            # rt.press_play_live does but WITHOUT its exact_clock(): that
            # instruction-count hook is the sequencer research's timing
            # setting, costs ~100x wall time and never comes off again
            # (every click took 11 s, 11 Sep 2026). The matrix path never
            # needed it and the position bar advances without it.
            def play(rt):
                flags = self._before_play(rt)
                d0 = rt.press_key_live(er.KEY_PLAY)
                for t in range(8):
                    rt.run(until=lambda r: r.pc == er.MAIN_SPIN)
                    rt.call_as_main(er.FW_START_TRACK, args=(t,))
                return f"d0={d0} active={flags} (frame mode on: slower while playing)"
            return self.do(play, timeout=120)
        handler = {"rec": er.KEY_REC, "stop": er.KEY_STOP}[what]
        def press(rt):
            d0 = rt.press_key_live(handler)
            if what == "stop":
                self._after_stop(rt)
            return f"d0={d0}" + (" (frame mode off)" if what == "stop" else "")
        return self.do(press, timeout=120)

    def activate_tracks(self, rt, tracks=range(8)):
        """Set the per-track PLAYS FREE byte in the current pattern record
        (+84 + 2330*t). No longer called by PLAY or poke_trig (12 Sep 2026):
        the 10 Sep reading of it as an "active" flag was wrong -- the two
        firmware consumers say the opposite. FW_TRANSPORT(0) (0x4009b964,
        the PLAY key's path) sets up a track only if the byte is ZERO
        (0x4009bc76), and FW_START_TRACK (0x4009b5c8, the trig-key start)
        acts only if it is SET (0x4009b630); with the byte set, PLAY calls no
        step handler for that track, so it fires nothing. Kept for scripts
        that want a plays-free track on purpose."""
        pb = rt.pattern_base()
        for t in tracks:
            rt.uc.mem_write(pb + 84 + 2330 * t, b"\x01")
        return [rt.uc.mem_read(pb + 84 + 2330 * t, 1)[0] for t in range(8)]

    def poke_trig(self, step, track=0):
        def act(rt):
            pb = rt.pattern_base()
            flags = [rt.uc.mem_read(pb + 84 + 2330 * t, 1)[0] for t in range(8)]
            return f"mask {rt.poke_trig(int(step)):#04x}, plays-free bytes {flags}"
        return self.do(act, timeout=60)

    _led_pos = 0

    def _parse_leds(self, rt):
        """Parse the firmware->panel stream for LED state. Frames seen in a
        real boot: a bare 0x43 hello; `<id> <value>` pairs (brightness init,
        value 0x3f); `0x10 <offset> <8 bytes>` bitmap blocks. The bitmap is
        what the trig/track LEDs live in; ids carry per-entity values."""
        tx = rt.uart64.tx
        i = self._led_pos
        n = len(tx)
        while i < n:
            b = tx[i]
            if b == 0x43:
                i += 1
            elif b == 0x10:
                if i + 10 > n:
                    break
                off = tx[i + 1]
                if off + 8 <= len(self.led_bits):
                    self.led_bits[off:off + 8] = tx[i + 2:i + 10]
                i += 10
            else:
                if i + 2 > n:
                    break
                self.led_ids[b] = tx[i + 1]
                i += 2
        with self.lock:
            self._led_pos = i

    busy_since = None
    ACTION_LIMIT = 20.0      # wall seconds an action may hold the emulator
    RUN_SLICE_MS = 5.0       # emulated ms per slice of run_ms() (5 ms wall idle, ~0.1 s playing)

    def _watchdog(self):
        """Flag an action that holds the emulator too long (self.abort);
        run_ms() checks the flag between its slices and gives up.

        It cannot stop the action itself: Unicorn's emu_stop() from another
        thread ends one BURST, and Rtos.run() then simply starts the next
        (it loops step() until its ms elapse or its until() holds) -- the
        first version called emu_stop() here and /run?ms=5000 while frame
        mode was on still held the emulator for 356 s wall (verifier,
        12 Sep 2026). An early-ended burst is also charged its whole
        quantum (Rtos.step: instrs += n), a timing glitch for nothing, so
        emu_stop() is gone."""
        while True:
            time.sleep(1.0)
            t0 = self.busy_since
            if t0 is not None and time.perf_counter() - t0 > self.ACTION_LIMIT:
                self.aborted = (self.aborted or 0) + 1
                self.abort = True
                self.busy_since = time.perf_counter()   # re-arm; flag once per limit
                # The port CAN be stopped: a child that has not answered in
                # ACTION_LIMIT is killed here; the command blocked on it
                # raises PortDied and the loop respawns (_respawn).
                if self.backend == "port" and self.proc is not None and self.proc.alive():
                    self.fault = f"watchdog: the port answered nothing for {self.ACTION_LIMIT:.0f} s; killed"
                    self.proc.kill()

    aborted = 0
    abort = False

    def run_ms(self, rt, ms, wall=None):
        """rt.run(ms=ms) in RUN_SLICE_MS slices, stopping early after `wall`
        seconds (one under ACTION_LIMIT by default, so the budget and not
        the watchdog is what ends it) or on the watchdog's flag; /status
        ran_ms follows the slices. Where a slice ends moves no firmware
        event (timers, frames and the panel UART advance by sample count),
        so the emulated timing is the plain run's. Needed because a long
        rt.run() is uninterruptible (see _watchdog) and in frame mode 5000
        ms of firmware is minutes of wall time: the page's RUN 1s / RUN 5s
        buttons froze it for the whole of that. Measured 12 Sep 2026
        (fix_srv.log): while playing /run?ms=1000 returned in 17.0 s at 59
        emulated ms per wall s (the pump's rate) and /run?ms=5000 stopped
        at 1202 ms after 20.1 s; idle, 1000 ms is instant (idle time is
        skipped to the next timer). Returns a one-line report."""
        wall = self.ACTION_LIMIT - 1.0 if wall is None else wall
        t0 = time.perf_counter()
        s0 = rt.sample
        end = s0 + ms * er.SAMPLE_HZ / 1000.0
        self.abort = False
        why = "done"
        slice_ms = getattr(rt, "SLICE_MS", self.RUN_SLICE_MS)   # the port: a round trip per slice
        while rt.sample < end:
            left = (end - rt.sample) / er.SAMPLE_HZ * 1000.0
            rt.run(ms=min(left, slice_ms))
            self.ran_ms = rt.sample / er.SAMPLE_HZ * 1000.0
            if self.abort:
                why = "watchdog"; break
            if time.perf_counter() - t0 > wall:
                why = f"{wall:.0f} s wall budget"; break
        ran = (rt.sample - s0) / er.SAMPLE_HZ * 1000.0
        dt = time.perf_counter() - t0
        rate = f" ({ran / dt:.0f} emulated ms per wall s{', frame mode on' if rt.frame else ''})" if dt > 0.2 else ""
        if why == "done":
            return f"ran {ran:.0f} ms in {dt:.1f} s{rate}"
        return f"ran {ran:.0f} of {ms:.0f} ms in {dt:.1f} s: {why}{rate}"

    def do(self, fn, timeout=30.0):
        """Run fn(rt) on the emu thread, return (ok, result-or-error)."""
        done = threading.Event()
        box = {}
        def act():
            try:
                box["r"] = fn(self.rt)
                box["ok"] = True
            except PortDied as e:
                # the child is gone: answer this caller, then let the loop respawn
                box["r"] = f"port: {e}"
                box["ok"] = False
                raise
            except Exception as e:
                box["r"] = f"{type(e).__name__}: {e}"
                box["ok"] = False
            finally:
                done.set()
        self.actions.put(act)
        if not done.wait(timeout):
            return False, "timeout (emulator busy)"
        return box["ok"], box["r"]

    def press(self, idx, edge):
        if not (0 <= idx < KEY_COUNT):
            return False, "bad index"
        if self.backend == "port":
            return False, "jump-table handlers need route A (--backend routea); use /key"
        h = self.handlers[idx]
        return self.do(lambda rt: rt.press_key_live(h, edge))

    row_state = None   # per-row pressed-bit bytes, lazily created

    def key(self, row, bit, down):
        """A key through the real input path: the panel scanner's UART.

        The front panel talks to the CPU over UART@fc064000 (found 10 Sep
        2026: the firmware sends its LED traffic out on it and its receive
        interrupt is armed). Key events are TWO-byte matrix reports:
        `<row> <column-bitmask>` -- a bit set is a key held down, cleared is
        released (verified: row 0x25 bit 6 alone opens PATTERN SETTINGS
        over the SET DATE/TIME dialog). Per-row state is kept here so
        FUNC-style holds across rows work exactly as fingers do.
        """
        if not (0x20 <= row < 0x30 and 0 <= bit < 8):
            return False, "bad row/bit"
        return self.do(lambda rt: self._key_act(rt, row, bit, down), timeout=120)

    def _key_act(self, rt, row, bit, down):
        """One key edge on the emu thread (key() queues it; transport() on
        the port taps down+up through it)."""
        if self.row_state is None:
            self.row_state = {}
        if down:
            self.row_state[row] = self.row_state.get(row, 0) | (1 << bit)
        else:
            self.row_state[row] = self.row_state.get(row, 0) & ~(1 << bit)
        state = self.row_state[row]
        # PLAY going down (not under FUNC: FUNC+PLAY is CLEAR PATTERN) gets
        # the same preparation the /transport path always had; STOP going
        # down takes frame mode off again after the key. The page has sent
        # PLAY as its matrix cell since the map was measured, and a bare
        # matrix PLAY started the transport but never stepped it (12 Sep
        # 2026 report: play icon and the first bar, no LEDs, no progress).
        func_held = bool(self.row_state.get(self.FUNC_KEY[0], 0) & (1 << self.FUNC_KEY[1]))
        play = down and (row, bit) == self.PLAY_KEY and not func_held
        stop = down and (row, bit) == self.STOP_KEY
        note = ""
        if play:
            note = f" (play: active={self._before_play(rt)}, frame mode on)"
        rt.uart64.rx.extend([row, state])
        rt.run(ms=50)
        if stop:
            self._after_stop(rt)
            note = " (stop: frame mode off)"
        return f"row {row:#04x} = {state:#04x}{note}"


class Handler(BaseHTTPRequestHandler):
    panel: Panel = None
    html: bytes = b""

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        p = self.panel
        path, _, q = self.path.partition("?")
        args = dict(kv.split("=", 1) for kv in q.split("&") if "=" in kv)
        if path == "/":
            self._send(200, self.html, "text/html; charset=utf-8")
        elif path == "/screen.png":
            with p.lock:
                self._send(200, p.frame or _png_gray(128, 64, [b"\x40" * 128] * 64), "image/png")
        elif path == "/screen.txt":
            # the frame as 64 lines of '#' (dark) / '.' -- for agents that grep, not view
            with p.lock:
                txt = p.screen_txt or ("." * 128 + "\n") * 64
            self._send(200, txt.encode(), "text/plain; charset=utf-8")
        elif path == "/status":
            with p.lock:
                self._json({"booted": p.booted, "seq": p.seq, "ran_ms": p.ran_ms,
                            "fault": p.fault, "image": str(p.image), "phase": p.phase,
                            "backend": p.backend, "backend_note": p.backend_note or None,
                            # emulated ms per wall s over the last 5 s of run() calls;
                            # idle runs skip to the next timer, so idle reads high
                            "speed": p.meter.value,
                            "restarts": p.restarts})
        elif path == "/peek":
            # read-only memory, either backend (Unicorn / the port's peek): what a
            # record holds right now, e.g. the popup slot 0x460d175c
            addr, n = int(args.get("addr", "0"), 0), min(int(args.get("len", "4"), 0), 4096)
            ok, res = p.do(lambda rt: bytes(rt.uc.mem_read(addr, n)).hex(), timeout=30)
            self._json({"ok": ok, "addr": f"{addr:#x}", "hex": res if ok else None, "result": None if ok else res})
        elif path == "/port":
            # the port child itself: argv, pid, its own status line, the tail of its report
            if p.backend != "port" or p.proc is None:
                self._json({"ok": False, "result": f"backend is {p.backend}"})
            else:
                ok, st = p.do(lambda rt: rt.status(), timeout=30)
                self._json({"ok": ok, "status": st if ok else None, "result": None if ok else st,
                            "pid": p.proc.proc.pid, "alive": p.proc.alive(), "argv": p.proc.argv,
                            "commands": p.proc.commands, "restarts": p.restarts,
                            "log_tail": list(p.proc.log)[-12:]})
        elif path == "/keys":
            self._json({"table": f"0x{KEY_TABLE:08x}",
                        "handlers": [f"0x{h:08x}" for h in p.handlers],
                        "rec": IDX_REC, "play": IDX_PLAY, "stop": IDX_STOP})
        elif path == "/press":
            ok, res = p.press(int(args.get("idx", -1)), int(args.get("edge", 0)))
            self._json({"ok": ok, "result": res if not ok else f"d0={res}"})
        elif path == "/key":
            ok, res = p.key(int(args.get("row", "-1"), 0), int(args.get("bit", "-1")),
                            args.get("down", "1") == "1")
            self._json({"ok": ok, "result": str(res)})
        elif path == "/knob":
            ok, res = p.knob(int(args.get("row", "-1"), 0), int(args.get("delta", "0")))
            self._json({"ok": ok, "result": str(res)})
        elif path == "/map":
            # the identified panel map (keys, knobs, leds): tools/panel/key_map.json
            mp = pathlib.Path(__file__).parent / "key_map.json"
            self._send(200, mp.read_bytes() if mp.exists() else b"{}", "application/json")
        elif path == "/transport":
            ok, res = p.transport(args.get("k", ""))
            self._json({"ok": ok, "result": str(res)})
        elif path == "/poke_trig":
            ok, res = p.poke_trig(args.get("step", "1"))
            self._json({"ok": ok, "result": str(res)})
        elif path == "/project":
            self._json({"project": p.project, "loaded": p.loaded})
        elif path == "/stack":
            # where is the emulator thread right now? (a hung action shows here)
            import traceback
            frames = sys._current_frames()
            dump = {}
            for th in threading.enumerate():
                f = frames.get(th.ident)
                if f is not None and th is not threading.current_thread():
                    dump[th.name] = "".join(traceback.format_stack(f)[-12:])
            self._send(200, "\n\n".join(f"== {k}\n{v}" for k, v in dump.items()).encode(), "text/plain")
        elif path == "/leds":
            # "bits": one byte per LED bitmap row (rows 0..16 of the 0x2r /
            # 0xa0+r messages, decoded by panel_link) -- key_map.json's leds
            # are [row, bit] into exactly this. "ids": the 0x3n <id> level
            # nibbles. Without panel_link the old naive parser's bytes remain.
            with p.lock:
                ids = dict(p.led_ids)
                bits = bytes(p.led_bits)
                if p.link is not None:
                    try:
                        rows = p.link.led_rows
                        bits = bytes(rows.get(i, 0) for i in range(17))
                        ids = dict(p.link.leds)
                    except Exception:
                        pass
                self._json({"bits": bits.hex(),
                            "ids": {f"{k:#04x}": v for k, v in ids.items()}})
        elif path == "/run":
            # sliced, wall-bounded (run_ms): a plain rt.run(ms=5000) in frame
            # mode held the emulator for minutes (12 Sep 2026)
            ms = float(args.get("ms", 100))
            ok, res = p.do(lambda rt: p.run_ms(rt, min(ms, 5000)), timeout=600)
            self._json({"ok": ok, "result": str(res)})
        else:
            self._send(404, b"?", "text/plain")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", default=None,
                    help="MAIN OS image (default: out/mainos_bus.bin if built, else the raw stock image)")
    ap.add_argument("--project", default=None, help="project dir to stage onto the card")
    ap.add_argument("--set", default="OCTABAM")
    ap.add_argument("--name", default=None)
    ap.add_argument("--port", type=int, default=8563)
    ap.add_argument("--no-rtc", action="store_true",
                    help="leave the DSPI RTC unmodelled (the firmware then reads 2000-00-00 00:00:00)")
    ap.add_argument("--midi-clock", action="store_true",
                    help="keep the project's CLOCK RECEIVE setting (default: clear it so the "
                         "sequencer runs on its own clock -- no MIDI clock ever arrives here)")
    ap.add_argument("--backend", choices=("auto", "port", "routea"), default="auto",
                    help="port = out/emu/ot_emu --interactive (default when it exists; built if "
                         "missing), routea = emu_rtos; auto falls back to route A with a note in /status")
    ap.add_argument("--port-bin", default=str(PORT_BIN),
                    help="the port binary (a .py stand-in runs under this Python)")
    ap.add_argument("--port-arg", action="append", default=[],
                    help="an extra flag for the port child, repeatable (e.g. --port-arg=--dsp)")
    a = ap.parse_args()

    # Default to the STOCK image: out/mainos_bus.bin is whatever the last
    # build or gate left there (verify_burn leaves a probe build that never
    # reaches the UI -- a blank screen, 11 Sep 2026). Pass --image to test
    # a built remix deliberately.
    image = a.image or str(ROOT / "out/raw/section_3_MAIN_OS.bin")

    project = None
    if a.project:
        # Stage the project's own samples too: project.work references them
        # as ../AUDIO/<file>, so a sibling AUDIO/ next to the project dir is
        # the set's pool. Without them every sample slot stays invalid
        # (RTOS_FORK section 10.12) -- the UI still works, the audio does not.
        pdir = pathlib.Path(a.project).resolve()
        pool = pdir.parent / "AUDIO"
        audio = [f"{w}:AUDIO/{w.name}" for w in sorted(pool.glob("*.wav"))] if pool.is_dir() else []
        # One staging tree per server port: stage_project wipes and remakes
        # its tree, and two panels started together on the shared default
        # raced on it (FileExistsError in mkdir, 12 Sep 2026).
        card, staged = er.stage_project(a.project, a.set, a.name, audio=audio,
                                        tree=str(ROOT / "out" / f"_panel_stage_{a.port}"),
                                        image_mb=max(64, 16 + sum(w.stat().st_size for w in pool.glob("*.wav")) // 2**20 if pool.is_dir() else 64))
        project = (a.set, staged)
        print(f"staged {pdir.name} as {a.set}/{staged} with {len(audio)} samples")
    else:
        tree = ROOT / "out/_panel_tree"
        (tree / a.set / "AUDIO").mkdir(parents=True, exist_ok=True)
        card = ec.build_image(str(tree), size_mb=64)

    if not a.no_rtc:
        install_rtc()           # route A only; the port's DSPI answers 0 (the 2000-00-00 dialog)

    # The backend. The port is the default when its binary exists and knows
    # --interactive (port_available builds it when missing); anything short
    # of that runs route A and says why in /status "backend_note".
    backend, note, card_file = a.backend, "", None
    if backend != "routea":
        ok, note = port_available(a.port_bin, build=True)
        if ok:
            backend = "port"
        elif a.backend == "port":
            sys.exit(f"panel: --backend port: {note}")
        else:
            backend = "routea"
            note = f"{note}; running route A"
            print(f"panel: {note}")
    if backend == "port":
        # The port reads the card from a file: the same bytes route A would
        # attach, one file per server port so two panels do not share it.
        card_file = ROOT / "out" / f"_panel_card_{a.port}.img"
        card_file.write_bytes(card)
    Handler.panel = Panel(image, card, project=project, internal_clock=not a.midi_clock,
                          backend=backend, port_bin=a.port_bin, port_args=a.port_arg,
                          card_file=card_file, backend_note=note, auto=a.backend == "auto",
                          play_pump_ms=25.0 if backend == "port" else 10.0)
    Handler.html = (pathlib.Path(__file__).parent / "panel.html").read_bytes()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"panel: http://localhost:{a.port}/   image={image}   backend={backend}")
    try:
        srv.serve_forever()
    finally:
        if Handler.panel.proc is not None:
            Handler.panel.proc.quit()


if __name__ == "__main__":
    main()
