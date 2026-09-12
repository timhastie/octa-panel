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
    .venv/bin/python3 tools/panel/panel_server.py --project <dir> --audio ~/samples   # seed the pool

Samples (12 Sep 2026): the card's AUDIO folder is a per-port pool
(SamplePool, out/_panel_pool_<port>/) -- /samples/add, /samples/upload
and /samples/remove change it, /samples/commit rebuilds the card and
reboots the unit on it (there is no hot-plug: Panel.commit_card).

Sound (12 Sep 2026): with `--sound on` (the default) the port child runs
--dsp and the server drains core 0's main L/R over the pipe (O14k) into
AudioRing (the last 180 s, absolute frame numbers) and, from PLAY to STOP,
into out/_panel_takes_<port>/take-NNN.wav -- /audio/status, /audio/pcm,
/audio.wav, /audio/enable (README "Hearing the unit"). Playing costs ~3x
the wall time of a child without the cores.

Then open http://localhost:8563/. Unmapped keys: the MAP drawer lists every
table entry; click one, watch the screen, name it. The mapping lives in the
browser (localStorage) and exports as JSON -- send a completed map back as
a PR to key_map.json.
"""
import argparse
import collections
import filecmp
import io
import json
import os
import pathlib
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
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
CLOCK_GEOMETRY = (0xf, 7, 0xe6, 0x32)     # the boot SET DATE/TIME dialog (KEYMAP.md)


def popup_geometry(uc):
    """(x0, y0, x1, rows) of the popup on screen, or None when the popup
    slot is empty or the record's flags say closed."""
    if int.from_bytes(uc.mem_read(UI_WINDOW, 4), "big") != POPUP:
        return None
    rec = bytes(uc.mem_read(POPUP, 0x2c))
    word = lambda off: int.from_bytes(rec[off:off + 4], "big")  # noqa: E731
    if not (word(0x20) & 0x20):
        return None
    return (word(0x08), word(0x0c), word(0x18), word(0x28))


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
      and with --dsp (O14k, 12 Sep 2026; `err audio needs --dsp` without it):
        audio start [main|cue|all]   ok        (core 0's ESAI frames into a 60 s ring)
        audio read [<maxframes>]     audio <frames> <hex>   (LE int16 stereo, released on read, never blocks)
        audio status                 audio status on= mode= captured= pending= rate=44100 dropped= cap=
        audio stop                   ok

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
        # A buffered view of the pipe for the line reads: the raw FileIO a
        # bufsize=0 Popen hands out reads ONE BYTE PER SYSCALL in readline(),
        # which cost an 8.9 KB `audio read` reply (a 25 ms pump's frames)
        # 2.2 ms wall and a 350 KB one 90 ms (measured 12 Sep 2026); the
        # short `tx`/`ok` replies never showed it. Only this thread reads
        # stdout, so the buffer hides nothing, and a line is delivered as
        # soon as its newline arrives (BufferedReader.readline returns on
        # the read() that carries it, it does not wait to fill the buffer).
        f = io.BufferedReader(self.proc.stdout, buffer_size=1 << 16)
        for raw in iter(f.readline, b""):
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


# -- the DSP main output: the child's audio ring drained into the server ------
#
# With sound on the port child runs --dsp and streams core 0's main L/R over
# the pipe (O14k: `audio start main`, `audio read`, 16-bit, 44100 Hz, a 60 s
# ring in the child that overwrites its oldest frames when nobody reads).
# Panel._drain_audio pulls everything pending into AudioRing (the last
# AUDIO_RING_S seconds, absolute frame numbers) on every pump, after every
# action and between run_ms slices, and into a take file while frame mode
# is on: PLAY opens take-NNN.wav, STOP closes it -- what the unit played
# between the two keys, as it would come out of MAIN OUT. 12 Sep 2026.

AUDIO_RATE = 44100
AUDIO_RING_S = 180           # the server-side ring: 180 s x 44100 x 4 B = 31.8 MB, allocated on the first frame
AUDIO_READ_MAX = 441000      # frames per `audio read` (10 s = 3.5 MB of hex on one line); looped while full
SOUND_ON_NOTE = "sound on -- while it plays the unit runs ~9x slower than real time (~3x slower than without the DSP cores)"
SOUND_OFF_NOTE = "sound off: the port child runs without the DSP cores"


def wav_header(frames, channels=2, rate=AUDIO_RATE, width=2):
    """The 44-byte RIFF/WAVE header for `frames` frames of PCM."""
    data = frames * channels * width
    return (b"RIFF" + struct.pack("<I", 36 + data) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * width,
                                    channels * width, width * 8)
            + b"data" + struct.pack("<I", data))


def pcm_peak(data):
    """(peak L, peak R), 0..32767, of little-endian 16-bit stereo bytes.
    memoryview.cast iterates in C (this Mac is little-endian, as is every
    host the port builds on); no per-sample Python loop."""
    n = len(data) // 4 * 4
    if not n:
        return (0, 0)
    mv = memoryview(data)[:n].cast("h")
    l, r = mv[0::2], mv[1::2]
    return (min(32767, max(max(l), -min(l))), min(32767, max(max(r), -min(r))))


class AudioRing:
    """The last AUDIO_RING_S seconds of the main output, 16-bit stereo,
    addressed by ABSOLUTE frame number since the server's capture started:
    `end` counts every frame ever appended (across child respawns: the
    child's own ring restarts, this one does not), `first` is the oldest
    frame still held. A circular bytearray (4 bytes a frame) allocated on
    the first append; read() copies at most two slices under the lock and
    nothing per sample -- /audio/pcm serves from it on an HTTP thread
    without touching the emu thread."""

    def __init__(self, seconds=AUDIO_RING_S):
        self.cap = int(seconds * AUDIO_RATE)
        self.buf = None
        self.end = 0
        self.lock = threading.Lock()

    @property
    def first(self):
        return max(0, self.end - self.cap)

    def append(self, data):
        total = len(data) // 4
        if total <= 0:
            return
        with self.lock:
            if self.buf is None:
                self.buf = bytearray(self.cap * 4)
            skip = max(0, total - self.cap)         # more than the ring holds: keep the newest
            n = total - skip
            pos = ((self.end + skip) % self.cap) * 4
            room = self.cap * 4 - pos
            k = n * 4
            if k <= room:
                self.buf[pos:pos + k] = data[skip * 4:skip * 4 + k]
            else:
                self.buf[pos:] = data[skip * 4:skip * 4 + room]
                self.buf[:k - room] = data[skip * 4 + room:skip * 4 + k]
            self.end += total

    def read(self, start, max_frames):
        """(from, bytes, end): up to max_frames frames from max(start, first);
        empty bytes when start >= end."""
        with self.lock:
            end = self.end
            start = max(int(start), self.first)
            n = min(int(max_frames), end - start)
            if n <= 0 or self.buf is None:
                return start, b"", end
            pos = (start % self.cap) * 4
            room = self.cap * 4 - pos
            k = n * 4
            if k <= room:
                out = bytes(self.buf[pos:pos + k])
            else:
                out = bytes(self.buf[pos:]) + bytes(self.buf[:k - room])
            return start, out, end


class TakeWriter:
    """One take file, out/_panel_takes_<port>/take-NNN.wav: 16-bit stereo
    44.1 kHz, the RIFF/data sizes re-patched after every append so the file
    is a valid WAV at every moment (a reader that lands mid-take gets what
    is there so far)."""

    def __init__(self, path, n, start):
        self.path, self.n, self.start = pathlib.Path(path), n, start    # start: the ring frame it opened at
        self.frames = 0
        self.f = open(self.path, "wb")
        self.f.write(wav_header(0))
        self.f.flush()

    def _patch(self):
        self.f.seek(4); self.f.write(struct.pack("<I", 36 + self.frames * 4))
        self.f.seek(40); self.f.write(struct.pack("<I", self.frames * 4))
        self.f.flush()

    def append(self, data):
        if not data:
            return
        self.f.seek(0, 2)
        self.f.write(data)
        self.frames += len(data) // 4
        self._patch()

    def close(self):
        try:
            self._patch()
        finally:
            self.f.close()

    def info(self, recording=False):
        return {"n": self.n, "file": str(self.path), "frames": self.frames,
                "seconds": round(self.frames / AUDIO_RATE, 3), "recording": recording,
                "start": self.start}


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


# -- the sample pool: the set's AUDIO folder, one per server port --------------
#
# On the unit samples live on the CF card in the set's AUDIO folder; the
# user copies files there (USB disk mode) and loads them into slots from the
# firmware's own file browser. Here the card is a FAT image built from a
# staged tree at boot and there is no hot-plug, so the pool below is the
# AUDIO folder the card is built FROM: /samples/add and /upload put files in
# it, /samples/commit rebuilds the image with every file in it and reboots
# the child on the new image -- re-inserting the card. One pool per server
# port (out/_panel_pool_<port>/), wiped and re-seeded at start from the
# project's sibling AUDIO and --audio, so nothing a running server adds
# lands in the fixture folder. 12 Sep 2026.

AFCONVERT = "/usr/bin/afconvert"
SAMPLE_EXTS = (".wav", ".aif", ".aiff")
_NAME_BAD = re.compile(r"[^A-Za-z0-9._ -]")      # the card is VFAT with long names: keep the
                                                 # name, replace what is outside this set


def _ext80(b):
    """The 80-bit extended float an AIFF COMM chunk keeps its sample rate in."""
    se = int.from_bytes(b[:2], "big")
    mant = int.from_bytes(b[2:10], "big")
    if (se & 0x7fff) == 0 and mant == 0:
        return 0.0
    val = mant * 2.0 ** ((se & 0x7fff) - 16383 - 63)
    return -val if se & 0x8000 else val


def sample_header(path):
    """What the file's own header says, stdlib only: {"kind": "WAV" | "AIFF" |
    "AIFC", "channels", "rate", "bits", "pcm", "tag"} from the RIFF/WAVE fmt
    chunk or the FORM/AIFF COMM chunk; None for anything else (mp3, flac,
    aac, a truncated file). "pcm": integer PCM (WAV format tag 1, or 0xfffe
    with a PCM SubFormat; AIFF, or AIFC with NONE/twos); "tag": the WAV format
    tag as written (0xfffe = WAVE_FORMAT_EXTENSIBLE, which the unit reads
    when its SubFormat is PCM -- measured 12 Sep 2026: a 16-bit 44.1 kHz
    stereo extensible WAV listed with footer `44.1k 16b 2Ch` and loaded
    into STATIC 6; afconvert keeps such a header anyway, so converting it
    changed nothing), the AIFC compression code for an AIFC, None for AIFF."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12:
                return None
            if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
                while True:
                    ch = f.read(8)
                    if len(ch) < 8:
                        return None
                    tag, size = ch[:4], struct.unpack("<I", ch[4:])[0]
                    if tag == b"fmt ":
                        fmt = f.read(min(size, 40))
                        if len(fmt) < 16:
                            return None
                        code, chans, rate, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
                        tag = code
                        if code == 0xfffe and len(fmt) >= 26:
                            code = struct.unpack("<H", fmt[24:26])[0]   # SubFormat GUID, leading word
                        return {"kind": "WAV", "channels": chans, "rate": rate, "bits": bits,
                                "pcm": code == 1, "tag": tag}
                    f.seek(size + (size & 1), 1)
            if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
                kind = head[8:12].decode()
                while True:
                    ch = f.read(8)
                    if len(ch) < 8:
                        return None
                    tag, size = ch[:4], struct.unpack(">I", ch[4:])[0]
                    if tag == b"COMM":
                        comm = f.read(min(size, 22))
                        if len(comm) < 18:
                            return None
                        chans, _, bits = struct.unpack(">hIh", comm[:8])
                        comp = comm[18:22] if kind == "AIFC" else b"NONE"
                        # "twos" is what afconvert -f AIFC -d BEI16 writes: big-endian
                        # integer PCM, the same bytes as NONE (12 Sep 2026)
                        return {"kind": kind, "channels": chans, "rate": int(round(_ext80(comm[8:18]))),
                                "bits": bits, "pcm": comp in (b"NONE", b"twos"),
                                "tag": comp.decode("latin-1") if kind == "AIFC" else None}
                    f.seek(size + (size & 1), 1)
    except OSError:
        return None
    return None


def sample_format(info, path=None):
    """'16-bit 44.1 kHz stereo WAV' from sample_header's dict (or the suffix
    of an unparsed file: 'MP3 (not WAV/AIFF)')."""
    if not info:
        ext = pathlib.Path(path).suffix.lstrip(".").upper() if path else ""
        return f"{ext or '?'} (not WAV/AIFF)"
    chans = {1: "mono", 2: "stereo"}.get(info["channels"], f"{info['channels']}-channel")
    kind = info["kind"]
    if info["kind"] == "WAV" and info["tag"] == 0xfffe:
        kind += " (extensible)"                     # only a 0xfffe tag, whatever its SubFormat
    if not info["pcm"]:
        kind += " (not integer PCM)"
    return f"{info['bits']}-bit {info['rate'] / 1000:g} kHz {chans} {kind}"


def sample_ok_as_is(info):
    """The unit's own rule: WAV or AIFF, integer PCM, 16 or 24 bit, 44.1 kHz,
    mono or stereo. An extensible WAV with a PCM SubFormat passes (the unit
    reads it, sample_header); AIFC does not (afconvert makes a WAV of it)."""
    return bool(info) and info["kind"] in ("WAV", "AIFF") and info["pcm"] \
        and info["bits"] in (16, 24) and info["rate"] == 44100 and info["channels"] in (1, 2)


class SamplePool:
    """out/_panel_pool_<port>/: the files the card's AUDIO folder is built from.

    add() is the one path for /samples/add, /samples/upload and --audio:
    read the header, convert with afconvert when the unit would not read
    the file as it is (`afconvert -f WAVE -d LEI16@44100 in out`, channels
    kept; the exact command goes into the reply note), write into the
    folder under the original basename with the extension normalised and
    characters outside [A-Za-z0-9._ -] replaced. A name already in the pool
    (case-insensitively) is refused unless the bytes are identical."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        if self.path.exists():
            shutil.rmtree(self.path)
        self.path.mkdir(parents=True)
        self.lock = threading.Lock()

    def names(self):
        return sorted((p.name for p in self.path.iterdir() if p.is_file() and not p.name.startswith(".")),
                      key=str.lower)

    def find(self, name):
        """The pool file called `name`, case-insensitively, or None."""
        low = name.lower()
        for n in self.names():
            if n.lower() == low:
                return self.path / n
        return None

    def files(self):
        out = []
        for n in self.names():
            p = self.path / n
            out.append({"name": n, "bytes": p.stat().st_size, "format": sample_format(sample_header(p), p)})
        return out

    def manifest(self):
        """{name: (size, mtime_ns)}: what the card was (or would be) built from."""
        return {n: (st.st_size, st.st_mtime_ns) for n in self.names() for st in (os.stat(self.path / n),)}

    def total_bytes(self):
        return sum(os.stat(self.path / n).st_size for n in self.names())

    def audio_specs(self):
        """stage_project's audio list: every pool file as AUDIO/<name>."""
        return [f"{self.path / n}:AUDIO/{n}" for n in self.names()]

    def seed(self, src_dir, convert=True, log=print):
        """Every WAV/AIFF in src_dir through add() (convert=False copies the
        project's own pool as it was saved). Returns the names added."""
        src_dir = pathlib.Path(src_dir).expanduser()
        added = []
        if not src_dir.is_dir():
            log(f"samples: {src_dir}: not a directory, nothing seeded")
            return added
        for p in sorted(src_dir.iterdir(), key=lambda q: q.name.lower()):
            if p.is_file() and p.suffix.lower() in SAMPLE_EXTS and not p.name.startswith("."):
                r = self.add(p) if convert else self._copy_verbatim(p)
                if r["ok"]:
                    added.append(r["name"])
                else:
                    log(f"samples: {p.name}: {r['error']}")
        return added

    def _copy_verbatim(self, src):
        dst = self.path / src.name
        if dst.exists() and not filecmp.cmp(src, dst, shallow=False):
            return {"ok": False, "error": f"{src.name}: a different file of that name is in the pool"}
        shutil.copyfile(src, dst)
        return {"ok": True, "name": src.name, "converted": False, "note": "the project's own pool, copied as is"}

    @staticmethod
    def card_name(raw, info, convert):
        """The name on the card: basename, extension normalised (.wav for a
        WAV or anything converted, .aif/.aiff kept for an AIFF), the rest
        VFAT-safe ASCII."""
        raw = pathlib.Path(raw).name.strip().lstrip(".")
        stem, dot, ext = raw.rpartition(".")
        if not dot:
            stem, ext = raw, ""
        ext = "." + ext.lower() if ext else ""
        if convert or (info and info["kind"] == "WAV"):
            ext = ".wav"
        elif info and info["kind"] == "AIFF" and ext not in (".aif", ".aiff"):
            ext = ".aif"
        stem = _NAME_BAD.sub("_", stem).strip() or "sample"
        return stem + ext

    def add(self, src, name=None, timeout=600, shown=None):
        """Validate -> convert if needed -> write into the pool. Returns the
        /samples/add reply dict. The note names the source as the caller
        knows it (`shown`: an upload's own name, not the dot-temporary its
        bytes landed in) and the pool file it became; "cmd" is afconvert's
        argv exactly as run, temporaries included."""
        src = pathlib.Path(src).expanduser()
        shown = shown or str(src)
        if not src.is_file():
            return {"ok": False, "error": f"{shown}: not a file"}
        if src.stat().st_size == 0:
            return {"ok": False, "error": f"{pathlib.Path(shown).name}: empty file"}
        info = sample_header(src)
        convert = not sample_ok_as_is(info)
        name = self.card_name(name or src.name, info, convert)
        was = sample_format(info, src)
        cmd = None
        with self.lock:
            tmp = self.path / f".incoming-{os.getpid()}-{name}"
            try:
                if convert:
                    cmd = [AFCONVERT, "-f", "WAVE", "-d", "LEI16@44100"]
                    if info and info["channels"] > 2:
                        cmd += ["-c", "2"]              # the unit reads mono or stereo only
                    said = " ".join(cmd + [shown, str(self.path / name)])
                    cmd += [str(src), str(tmp)]
                    try:
                        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                    except (OSError, subprocess.TimeoutExpired) as e:
                        return {"ok": False, "error": f"afconvert: {type(e).__name__}: {e}", "cmd": " ".join(cmd)}
                    if r.returncode or not tmp.exists():
                        tail = " | ".join((r.stderr or r.stdout).strip().splitlines()[-4:]) or "(no output)"
                        return {"ok": False, "error": f"afconvert failed (rc {r.returncode}): {tail}",
                                "cmd": " ".join(cmd), "was": was}
                    note = f"converted from {was}: {said}"
                else:
                    shutil.copyfile(src, tmp)
                    note = f"{was}, copied as is"
                have = self.find(name)
                if have is not None:
                    if filecmp.cmp(have, tmp, shallow=False):
                        return self._added(have, convert, note + f"; already in the pool as {have.name}"
                                           " (identical bytes)", cmd)
                    return {"ok": False, "error": f"{have.name} is already in the pool with different"
                                                  " content: remove it first, or rename the file",
                            "was": was}
                tmp.replace(self.path / name)
            finally:
                if tmp.exists():
                    tmp.unlink()
        return self._added(self.path / name, convert, note, cmd)

    @staticmethod
    def _added(out, convert, note, cmd):
        r = {"ok": True, "name": out.name, "converted": convert, "note": note,
             "bytes": out.stat().st_size, "format": sample_format(sample_header(out), out)}
        if cmd:
            r["cmd"] = " ".join(cmd)
        return r

    def remove(self, name):
        have = self.find(name)
        if have is None:
            return {"ok": False, "error": f"{name}: not in the pool"}
        have.unlink()
        return {"ok": True, "name": have.name}


def build_card(project, set_name, name, tree, pool):
    """The card image: the project dir (when given) plus EVERY pool file as
    AUDIO/<name>, staged under `tree` (wiped and remade) and sized to fit.
    Returns (image bytes, staged project name or None, staged AUDIO dir).
    Measured 12 Sep 2026: the OTLIVE fixture with its 37 files, 64 MB image,
    in about a second."""
    tree = pathlib.Path(tree)
    audio = pool.audio_specs()
    size_mb = max(64, 16 + pool.total_bytes() // 2**20)
    if project:
        card, staged = er.stage_project(project, set_name, name, audio=audio, tree=str(tree), image_mb=size_mb)
    else:
        if tree.exists():
            shutil.rmtree(tree)
        (tree / set_name / "AUDIO").mkdir(parents=True)
        for spec in audio:
            f, rel = spec.split(":", 1)
            shutil.copy2(f, tree / set_name / rel)
        card, staged = ec.build_image(str(tree), size_mb=size_mb), None
    return card, staged, tree / set_name / "AUDIO"


class Panel:
    """Owns the emulator thread; everything Unicorn happens on it."""

    def __init__(self, image, card, pump_ms=25.0, project=None, internal_clock=True,
                 play_pump_ms=10.0, backend="routea", port_bin=PORT_BIN, port_args=(),
                 card_file=None, backend_note="", auto=False, pool=None, card_builder=None,
                 staged_audio=None, sound=False, takes_dir=None):
        self.image = image
        # Sound (12 Sep 2026): with `sound` the port child is spawned with
        # --dsp and its main output is drained here (see AudioRing above,
        # _audio_start, _drain_audio). sound_wanted is what the NEXT child
        # boots with (/audio/enable flips it and reboots); sound is what the
        # current one delivers; audio_on says its capture is running.
        self.sound_wanted = bool(sound) and backend == "port"
        self.sound = False
        if backend != "port":
            self.sound_note = "no sound under route A (the DSP cores are the port's)" if sound else SOUND_OFF_NOTE
        else:
            self.sound_note = SOUND_ON_NOTE if sound else SOUND_OFF_NOTE
        self.sound_busy = False           # an /audio/enable reboot is in progress
        self.audio_on = False             # the current child accepted `audio start main`
        self.audio_note = None            # what went wrong with the capture, if anything
        self.ring = AudioRing()
        self.audio_captured = 0           # frames drained from every child so far
        self.audio_dropped = 0            # frames the children overwrote unread (their counters, summed)
        self._dropped_base = 0            # audio_dropped when the current child started
        self._dropped_at = 0.0            # wall time of the last `audio status` poll (one per second)
        self.audio_peak = (0, 0)          # |peak| L/R of the last non-empty read
        self.drain = {"reads": 0, "frames": 0, "wall_ms": 0.0, "max_ms": 0.0}   # the drain's own cost
        self.takes_dir = pathlib.Path(takes_dir) if takes_dir else None
        self.take = None                  # TakeWriter while a take is open (PLAY .. STOP)
        self.takes = []                   # closed takes, oldest first, as /audio/status lists them
        self.take_lock = threading.Lock()
        self.take_seq = self._scan_takes()
        self.card = card                  # the FAT16 card image, bytes (route A takes it as is)
        self.card_file = card_file        # ... and the port reads it from this file
        self.pool = pool                  # SamplePool the card's AUDIO was built from
        self.card_builder = card_builder  # () -> (card bytes, staged name, staged AUDIO dir): commit_card
        self.staged_audio = staged_audio  # the AUDIO dir inside the staging tree (what is on the card)
        self.card_manifest = pool.manifest() if pool is not None else {}   # the pool as the card has it
        self.card_busy = False            # a re-insert (rebuild + reboot) is in progress
        self.reinserts = 0
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
        self.clock_note = None       # what _dismiss_clock found (in /status)
        # After a track key is released the idle pump slows down for a
        # moment so a second press from the page lands inside the
        # firmware's double-tap window (the sample slot list): see _loop.
        self.slow_until = 0.0

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
        # Wait for the dialog itself: on an empty card (no --project) it
        # came up later than 400 ms after `ready` and a blind YES landed
        # on the main screen instead (ARM ALL, then the dialog on top of
        # it; 12 Sep 2026). Bounded: a firmware that never shows it must
        # not be sent a stray YES.
        uc = self._uc()
        for _ in range(60):                      # up to 6 s of firmware
            if popup_geometry(uc) == CLOCK_GEOMETRY:
                break
            rt.run(ms=100)
        else:
            self.clock_note = "clock dialog never appeared (no YES sent)"
            self._poll(rt)
            self._snapshot(uc)
            return
        rt.uart64.rx.extend([0x26, 0x02]); rt.run(ms=60)
        rt.uart64.rx.extend([0x26, 0x00]); rt.run(ms=300)
        for _ in range(10):                      # it closes within a few frames
            if popup_geometry(uc) != CLOCK_GEOMETRY:
                break
            rt.run(ms=100)
        g = popup_geometry(uc)
        self.clock_note = ("clock dialog closed" if g is None
                           else f"popup still open after YES: {tuple(hex(v) for v in g)}")
        self._poll(rt)
        self._snapshot(uc)

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
        argv += self.port_args
        if self.sound_wanted and "--dsp" not in argv:
            argv.append("--dsp")        # the DSP cores: sound, ~9x slower than real time while playing (~3x the wall time of no cores)
        return argv

    def _boot_port(self, phase=None):
        """Spawn the child, wait for `ready`, close the clock dialog. `phase`
        overrides the boot text (the card re-insert keeps its own)."""
        self.phase = phase or ("booting the port" + (" (boot + project load, ~1 min)" if self.project else ""))
        log = pathlib.Path(str(self.card_file)).with_suffix(".port.log") if self.card_file else None
        proc = PortProc(self._port_argv(), log_path=log)
        self.proc = proc
        try:
            proc.wait_ready(self.BOOT_TIMEOUT)
            rt = PortRt(proc, meter=self.meter)
            self.rt = rt
            self.booted = True
            self._audio_start(rt)       # the ring lives in the child: start it on every fresh one
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
                if self.booted:
                    raise
                if self.sound_wanted:
                    # The --dsp child did not come up (a binary built without
                    # the cores, a DSP boot that faults): once more without
                    # them -- the panel works, the sound does not, and
                    # /status says why (backend_note, sound_note).
                    self.sound_wanted = False
                    self.audio_note = self.sound_note = f"sound off: the --dsp child did not boot ({e})"
                    self.backend_note = ((self.backend_note + "; ") if self.backend_note else "") + \
                        f"the --dsp child did not boot ({e}); booted without the DSP cores (no sound)"
                    print(f"panel: {self.backend_note}")
                    try:
                        self._boot_port()
                    except PortDied as e2:
                        e = e2
                    else:
                        self.phase = "ready"
                        return
                if not self.auto:
                    raise e
                # The default choice did not come up (a binary without
                # --interactive exits 2 with its usage; a build that links
                # but faults at boot): route A instead, and say so.
                self.backend_note = f"port did not boot ({e}); running route A"
                print(f"panel: {self.backend_note}")
                self.backend = "routea"
                self.sound_note = "no sound under route A (the DSP cores are the port's)"
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
        return self._reboot_port(f"restarting the port ({why})")

    def _reboot_port(self, phase):
        """Kill the child and boot a fresh one on self.card_file (the
        respawn above and the card re-insert share this): a new decoder,
        blank LEDs and screen, three boot attempts, `phase` shown until the
        clock dialog is closed; /status "booted" is false meanwhile (it
        read true through a re-insert with the screen blank, 12 Sep 2026).
        True when the port is up again."""
        self._close_take(None)          # a take open across a reboot ends here (the child's ring is gone)
        self.audio_on = False
        if self.proc is not None:
            self.proc.kill()
        self.booted = False
        self._new_link()
        self.led_bits = bytearray(64); self.led_ids = {}
        with self.lock:
            self.frame = b""; self.screen_txt = ""
        for attempt in range(3):
            try:
                self._boot_port(phase)
                self.phase = "ready"
                return True
            except PortDied as e:
                self.fault = f"port: respawn {attempt + 1} failed: {e}"
                time.sleep(2.0)
        self.phase = "failed"
        return False

    REINSERT_PHASE = "re-inserting the card (reboot, ~40 s)"

    def pending(self):
        """(added, removed): pool files not on the card as they are, and card
        files no longer in the pool -- what the next commit changes."""
        if self.pool is None:
            return [], []
        now = self.pool.manifest()
        added = [n for n, st in now.items() if self.card_manifest.get(n) != st]
        removed = [n for n in self.card_manifest if n not in now]
        return added, removed

    def commit_card(self):
        """Re-insert the card: rebuild the image from the pool (every file)
        and reboot the unit on it -- the emulator has no hot-plug, and the
        firmware scans AUDIO/ at mount. Answers at once (ok, phase); the
        work runs as one action on the emu thread, /status "phase" shows
        REINSERT_PHASE, then the clock dialog, then ready. Under the port
        it is the watchdog's respawn path on the new image (_reboot_port);
        under route A a fresh attach on the new bytes and the same boot
        preamble. Adds, removes and a second commit are refused meanwhile
        (card_busy)."""
        if self.pool is None or self.card_builder is None:
            return False, "no sample pool"
        if self.card_busy:
            return False, "a re-insert is already in progress"
        if self.sound_busy:
            return False, "a sound switch (reboot) is in progress"
        if not self.booted:
            return False, f"the unit is still booting ({self.phase})"
        self.card_busy = True
        self.phase = self.REINSERT_PHASE

        def act():
            # Not an action the watchdog may time: the fresh child boots
            # for ~40 s and ACTION_LIMIT is 20 (it would kill it mid-boot).
            self.busy_since = None
            t0 = time.perf_counter()
            try:
                card, staged, audio_dir = self.card_builder()
                self.card, self.staged_audio = card, audio_dir
                if staged is not None and self.project:
                    self.project = (self.project[0], staged)
                self.card_manifest = self.pool.manifest()
                self.reinserts += 1
                if self.backend == "port":
                    self.card_file.write_bytes(card)
                    self._reboot_port(self.REINSERT_PHASE)
                else:
                    self.booted = False
                    self._new_link()
                    self.led_bits = bytearray(64); self.led_ids = {}
                    with self.lock:
                        self.frame = b""; self.screen_txt = ""
                    self._boot_routea()
                    self.phase = "ready"
                self.fault = None
                print(f"panel: card re-inserted with {len(self.card_manifest)} files in"
                      f" {time.perf_counter() - t0:.1f} s ({self.backend})")
            except Exception as e:
                # a build that failed leaves the old child running; a boot
                # that failed has said "failed" itself (_reboot_port)
                self.fault = f"card re-insert: {type(e).__name__}: {e}"
                alive = self.backend != "port" or (self.proc is not None and self.proc.alive())
                self.phase = "ready" if alive else "failed"
            finally:
                self.card_busy = False
        self.actions.put(act)
        return True, self.REINSERT_PHASE

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
            try:
                while True:
                    try:
                        act = self.actions.get_nowait()
                    except queue.Empty:
                        break
                    self.busy_since = time.perf_counter()
                    try:
                        act()
                        self._drain_audio(self.rt)   # what the action's own runs rendered
                    finally:
                        self.busy_since = None
                rt = self.rt        # after the actions: a card re-insert replaces it
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
                # The double-tap window: a track key pressed twice opens its
                # sample slot list, and the firmware measures the gap in ITS
                # time. Two page clicks are two actions with idle pumps
                # between them (25 ms each, ~30 ms wall), so a double-click
                # 0.3 s of wall apart reached the firmware ~350 emulated ms
                # press to press and missed the window (200 ms lands, 375
                # does not; measured 12 Sep 2026). For SLOW_WALL_S after a
                # track key goes up the pump is SLOW_PUMP_MS: the same
                # double-click is then ~170 emulated ms press to press.
                # Nothing else changes -- the firmware still runs, just less
                # of it per wall second, and only for that moment.
                if rt.frame:
                    pump = min(self.pump_ms, self.play_pump_ms)
                elif t < self.slow_until:
                    pump = min(self.pump_ms, self.SLOW_PUMP_MS)
                else:
                    pump = self.pump_ms
                rt.run(ms=pump)
                self._poll(rt)
                burst = time.perf_counter() - t    # the run + tx alone decide the idle sleep below,
                self._drain_audio(rt)              # so the drain (one more round trip, /audio/status
                self.busy_since = None             # "drain" measures it) changes no pump cadence
                self.ran_ms = rt.sample / er.SAMPLE_HZ * 1000.0
                if isinstance(rt, PortRt) and rt.stop_reason not in ("time", "gate", None, "?") \
                        and not (self.fault or "").startswith("port: run stopped"):
                    self.fault = f"port: run stopped: {rt.stop_reason}"
                self._snapshot(self._uc())
                self._parse_leds(rt)
                # idle bursts return instantly; don't spin the host
                if burst < 0.01:
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
            self._open_take(rt)         # sound on: a take file from this PLAY to the next STOP
        return [rt.uc.mem_read(rt.pattern_base() + 84 + 2330 * t, 1)[0] for t in range(8)]

    def _after_stop(self, rt):
        rt.frame = False
        self._close_take(rt)

    # -- sound: the child's audio ring, the server's ring, the takes ----------

    def _scan_takes(self):
        """Takes already in takes_dir (an earlier server on this port): list
        them and number on from the highest."""
        top = 0
        if self.takes_dir is not None and self.takes_dir.is_dir():
            for f in sorted(self.takes_dir.glob("take-*.wav")):
                m = re.fullmatch(r"take-(\d+)\.wav", f.name)
                if not m:
                    continue
                n = int(m.group(1))
                frames = max(0, f.stat().st_size - 44) // 4
                self.takes.append({"n": n, "file": str(f), "frames": frames,
                                   "seconds": round(frames / AUDIO_RATE, 3), "recording": False})
                top = max(top, n)
        return top + 1

    def _audio_start(self, rt):
        """`audio start main` on a fresh child -- at the first boot and after
        every respawn, card re-insert and sound switch (the child's ring
        restarts from zero; the server's AudioRing keeps counting, so the
        absolute frame numbers keep increasing across it). Sets sound /
        audio_on / sound_note; a child that refuses (no --dsp) leaves the
        panel working without sound and says so."""
        self.audio_on = False
        self._dropped_base = self.audio_dropped
        self._dropped_at = 0.0
        if not isinstance(rt, PortRt) or not self.sound_wanted:
            self.sound = False
            self.sound_note = self.audio_note or SOUND_OFF_NOTE
            return
        try:
            rt.proc.command("audio start main", "ok")
        except PortError as e:
            self.sound = False
            self.audio_note = self.sound_note = f"sound off: the child refused `audio start main` ({e})"
            return
        self.audio_on = True
        self.sound = True
        self.audio_note = None
        self.sound_note = SOUND_ON_NOTE

    def _drain_audio(self, rt):
        """Everything the child captured since the previous drain, into the
        ring and the open take. `audio read` answers what is there and
        never blocks (O14k: 0.2 ms wall for a 100 ms slice, 8.8 KB of hex
        for a 25 ms pump); read in AUDIO_READ_MAX pieces while a piece
        comes back full. The child's `dropped` counter (frames it overwrote
        before a read) is polled once a wall second, not per pump. Always
        on the emu thread, like every pipe command. Returns frames drained."""
        if not self.audio_on or not isinstance(rt, PortRt):
            return 0
        got = 0
        t0 = time.perf_counter()
        while True:
            rep = rt.proc.command(f"audio read {AUDIO_READ_MAX}", "audio")
            head, _, hexs = rep[6:].partition(" ")          # "audio <frames> <hex>"
            n = int(head)
            if n:
                data = bytes.fromhex(hexs.strip())
                if len(data) != n * 4:
                    raise PortError(f"audio read: {n} frames announced, {len(data)} bytes of PCM")
                self.ring.append(data)
                self.audio_captured += n
                self.audio_peak = pcm_peak(data)
                if self.take is not None:
                    with self.take_lock:
                        self.take.append(data)
                got += n
            if n < AUDIO_READ_MAX:
                break
        if t0 - self._dropped_at > 1.0:
            st = rt.proc.command("audio status", "audio")
            f = dict(kv.split("=", 1) for kv in st.split()[2:] if "=" in kv)
            self.audio_dropped = self._dropped_base + int(f.get("dropped", 0))
            self._dropped_at = t0
        dt = (time.perf_counter() - t0) * 1000.0
        d = self.drain
        d["reads"] += 1; d["frames"] += got; d["wall_ms"] += dt
        if dt > d["max_ms"]:
            d["max_ms"] = dt
        return got

    def _open_take(self, rt):
        """PLAY: a new take-NNN.wav gets every frame drained from here on.
        What the child holds now was rendered BEFORE the key: drained into
        the ring first, so the file starts at the PLAY press."""
        if self.takes_dir is None or not self.audio_on or self.take is not None:
            return
        self._drain_audio(rt)
        self.takes_dir.mkdir(parents=True, exist_ok=True)
        n, self.take_seq = self.take_seq, self.take_seq + 1
        with self.take_lock:
            self.take = TakeWriter(self.takes_dir / f"take-{n:03d}.wav", n, self.ring.end)

    def _close_take(self, rt):
        """STOP (rt given: the frames up to the key are drained into it
        first) or a reboot (rt None: the child is gone). The take joins the
        list; None when no take was open."""
        if self.take is None:
            return None
        try:
            if rt is not None:
                self._drain_audio(rt)
        finally:
            with self.take_lock:
                t, self.take = self.take, None
                t.close()
                info = t.info()
                self.takes.append(info)
        return info

    def take_file(self, n):
        """(info, path) of take n -- the open one included -- or (None, None)."""
        with self.take_lock:
            if self.take is not None and self.take.n == n:
                return self.take.info(recording=True), self.take.path
            for t in self.takes:
                if t["n"] == n:
                    return dict(t), pathlib.Path(t["file"])
        return None, None

    def audio_status(self):
        with self.take_lock:
            take = self.take.info(recording=True) if self.take is not None else None
        takes = list(self.takes) + ([take] if take else [])
        return {"sound": self.sound, "on": self.audio_on, "rate": AUDIO_RATE,
                "captured": self.audio_captured, "end": self.ring.end, "first": self.ring.first,
                "cap": self.ring.cap, "dropped": self.audio_dropped, "peak": list(self.audio_peak),
                "take": take, "takes": takes, "note": self.audio_note or self.sound_note,
                # additions beyond the contract: the reboot state the page disables its controls on,
                # where the files land, and what the drain itself costs the pump
                "busy": self.sound_busy or self.card_busy, "phase": self.phase,
                "takes_dir": str(self.takes_dir) if self.takes_dir else None,
                "drain": dict(self.drain)}

    SOUND_ON_PHASE = "switching sound on (reboot, ~1 min)"
    SOUND_OFF_PHASE = "switching sound off (reboot, ~40 s)"

    def set_sound(self, on):
        """/audio/enable: reboot the port child with (on) or without --dsp,
        the card re-insert's own mechanics (_reboot_port: a fresh child on
        the same card image, the clock dialog closed, phase shown, booted
        false meanwhile). Takes and the ring survive; the child's ring
        restarts and the server keeps numbering on. Refused while a
        re-insert or another switch runs, while booting, when already in
        that state, and under route A."""
        on = bool(on)
        if self.backend != "port":
            return False, "no sound under route A (the DSP cores are the port's)"
        if self.card_busy:
            return False, "the card is being re-inserted (reboot in progress)"
        if self.sound_busy:
            return False, "a sound switch is already in progress"
        if not self.booted:
            return False, f"the unit is still booting ({self.phase})"
        if on == self.sound_wanted:
            return False, f"sound is already {'on' if on else 'off'}"
        self.sound_busy = True
        phase = self.SOUND_ON_PHASE if on else self.SOUND_OFF_PHASE
        self.phase = phase

        def act():
            self.busy_since = None          # a boot, not an action the watchdog may time
            t0 = time.perf_counter()
            self.sound_wanted = on
            self.audio_note = None
            try:
                ok = self._reboot_port(phase)
                if not ok and on:
                    # the --dsp child did not come up three times: back without the cores
                    why = self.fault
                    self.sound_wanted = False
                    self.audio_note = f"sound off: the --dsp child did not boot ({why})"
                    ok = self._reboot_port(self.SOUND_OFF_PHASE)
                if ok:
                    self.fault = None
                    print(f"panel: sound {'on' if self.sound else 'off'} after a reboot of"
                          f" {time.perf_counter() - t0:.1f} s ({self.sound_note})")
            except Exception as e:
                self.fault = f"sound switch: {type(e).__name__}: {e}"
                alive = self.proc is not None and self.proc.alive()
                self.phase = "ready" if alive else "failed"
            finally:
                self.sound_busy = False
        self.actions.put(act)
        return True, phase

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
    SLOW_PUMP_MS = 6.0       # the idle pump for SLOW_WALL_S after a track key is released
    SLOW_WALL_S = 0.5        # (the double-tap window, see _loop)
    TRACK_ROW = 0x22         # the track keys T1-T8 (KEYMAP.md)

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
            self._drain_audio(rt)       # the ring and the take keep up slice by slice
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

    def tap(self, row, bit, n=1, hold=50, gap=150):
        """`n` presses of one key as ONE action: `hold` ms down, `gap` ms
        up between presses. Needed for the firmware's double-tap chords --
        a track key pressed twice opens the sample slot list (found 12 Sep
        2026 by a PC watch on the list's window store 0x4007920c under a
        pumpless ot_emu: every T1-T8 double-tapped, nothing else in 47
        keys x {alone, held 1.2 s, under 20 modifiers}). Two /key taps
        may or may not qualify: the firmware's window is short in ITS
        time, and the idle pump runs between separate actions -- four
        back-to-back curl /key edges (225 emulated ms over the two
        presses) opened it, the same two taps 0.2 s of wall apart (375
        emulated ms press to press) or 0.5/1/2 s apart did not (measured
        12 Sep 2026, port, OTLIVE). Inside one action the gap is exactly
        `gap` (200 ms press to press by default), so this is the reliable
        way; page clicks are separate actions and may not land."""
        if not (0x20 <= row < 0x30 and 0 <= bit < 8):
            return False, "bad row/bit"
        n = max(1, min(int(n), 8))
        hold = max(10.0, min(float(hold), 2000.0))
        gap = max(10.0, min(float(gap), 2000.0))
        def act(rt):
            out = []
            for i in range(n):
                out.append(self._key_act(rt, row, bit, True, run_ms=hold))
                out.append(self._key_act(rt, row, bit, False, run_ms=gap if i < n - 1 else 50.0))
            return f"{n} x ({hold:g} ms down, {gap:g} ms up): " + "; ".join(out)
        return self.do(act, timeout=120)

    def _key_act(self, rt, row, bit, down, run_ms=50.0):
        """One key edge on the emu thread (key() queues it; transport() on
        the port taps down+up through it; tap() chains them), then
        `run_ms` of firmware."""
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
        rt.run(ms=run_ms)
        if stop:
            self._after_stop(rt)
            note = " (stop: frame mode off)"
        if not down and row == self.TRACK_ROW:
            self.slow_until = time.perf_counter() + self.SLOW_WALL_S
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

    # The sample pool (SamplePool, Panel.commit_card): the set's AUDIO folder
    # the card is built from. Query values are URL-encoded (a Mac path with
    # spaces arrives as %20 or +). Adds and removes change the pool only;
    # the unit sees them after /samples/commit re-inserts the card (~40 s,
    # /status "phase"); while that runs every one of these answers ok:false.
    def _samples(self, path, args, body=None):
        p = self.panel
        arg = lambda k: urllib.parse.unquote_plus(args.get(k, ""))  # noqa: E731
        if p.pool is None:
            self._json({"ok": False, "error": "no sample pool"}, 500)
            return
        if path == "/samples":
            added, removed = p.pending()
            self._json({"pool": str(p.pool.path), "staged": str(p.staged_audio) if p.staged_audio else None,
                        "files": p.pool.files(), "pending": added, "removed": removed,
                        "busy": p.card_busy, "phase": p.phase})
            return
        if p.card_busy:
            self._json({"ok": False, "error": "the card is being re-inserted (reboot in progress)",
                        "note": f"try again when /status phase is ready (now: {p.phase})"})
            return
        if path == "/samples/add":
            src = arg("path")
            if not src.startswith("/") and not src.startswith("~"):
                self._json({"ok": False, "error": "path must be absolute"})
                return
            r = p.pool.add(src)
        elif path == "/samples/upload":
            name = arg("name")
            if not name:
                self._json({"ok": False, "error": "name= is required"})
                return
            if not body:
                self._json({"ok": False, "error": "empty body (POST the raw file bytes)"})
                return
            # afconvert and the header parse want a file: the body lands in
            # the pool folder under a dot name and goes through add() as
            # /samples/add would (the reply names the upload, not the
            # temporary), then the temporary is removed.
            tmp = p.pool.path / f".upload-{os.getpid()}-{threading.get_ident()}-{SamplePool.card_name(name, None, False)}"
            try:
                tmp.write_bytes(body)
                r = p.pool.add(tmp, name=name, shown=name)
            finally:
                if tmp.exists():
                    tmp.unlink()
        elif path == "/samples/remove":
            r = p.pool.remove(arg("name"))
        elif path == "/samples/commit":
            ok, res = p.commit_card()
            self._json({"ok": ok, "phase": res} if ok else {"ok": False, "error": res, "phase": p.phase})
            return
        else:
            self._send(404, b"?", "text/plain")
            return
        if r.get("ok"):
            r["pending"] = p.pending()[0]
        self._json(r)

    # The main output (AudioRing, the takes; sound on = the child runs --dsp).
    # /audio/status and /audio/pcm read the ring on this thread under its own
    # lock -- no emu-thread action, so they answer while the pump runs;
    # /audio/enable queues the reboot. Every answer here is JSON or audio:
    # bad numbers are 400, a missing take 404, never a traceback.
    def _audio(self, path, args):
        p = self.panel

        def num(k, default):
            v = args.get(k)
            if v in (None, ""):
                return int(default)
            return int(v, 0) if v.lower().startswith(("0x", "0o", "0b")) else int(v)

        def send_wav(data, name, extra=()):
            body = wav_header(len(data) // 4) + data
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra:
                self.send_header(k, str(v))
            self.end_headers()
            self.wfile.write(body)

        try:
            if path == "/audio/status":
                self._json(p.audio_status())
            elif path == "/audio/pcm":
                # raw LE int16 stereo frames from the ring: from=<absolute frame>
                # (clamped up to the oldest still held; X-Audio-From says where the
                # body really starts), max= frames (default 2 s, at most 10 s)
                start = max(0, num("from", 0))
                mx = max(1, min(num("max", 88200), 441000))
                frm, data, end = p.ring.read(start, mx)
                if not data:
                    self.send_response(204)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Audio-End", str(end))
                    self.send_header("X-Audio-Rate", str(AUDIO_RATE))
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Audio-From", str(frm))
                self.send_header("X-Audio-Frames", str(len(data) // 4))
                self.send_header("X-Audio-End", str(end))
                self.send_header("X-Audio-Rate", str(AUDIO_RATE))
                self.end_headers()
                self.wfile.write(data)
            elif path == "/audio.wav":
                if args.get("take") not in (None, ""):
                    n = num("take", 0)
                    info, file = p.take_file(n)
                    if info is None:
                        self._json({"ok": False, "error": f"no take {n}", "takes": [t["n"] for t in p.takes]}, 404)
                        return
                    with p.take_lock:               # not mid-append of the open take
                        raw = file.read_bytes() if file.exists() else b""
                    if len(raw) < 44 or raw[:4] != b"RIFF":
                        self._json({"ok": False, "error": f"take {n}: {file} is not a WAV"}, 404)
                        return
                    data = raw[44:len(raw) // 4 * 4]  # the header is rebuilt from what is there now
                    send_wav(data, f"octatrack-take-{n:03d}.wav",
                             [("X-Audio-Take", n), ("X-Audio-Frames", len(data) // 4),
                              ("X-Audio-Recording", int(bool(info.get("recording"))))])
                else:
                    # the ring: from=..&to=.. absolute frames, default everything held
                    first, end = p.ring.first, p.ring.end
                    frm = max(num("from", first), first)
                    to = min(num("to", end), end)
                    if to <= frm:
                        self._json({"ok": False, "error": "nothing captured in that range",
                                    "first": first, "end": end, "sound": p.sound}, 404)
                        return
                    frm, data, end = p.ring.read(frm, to - frm)
                    send_wav(data, "octatrack-main-out.wav",
                             [("X-Audio-From", frm), ("X-Audio-Frames", len(data) // 4), ("X-Audio-End", end)])
            elif path == "/audio/enable":
                v = args.get("on", "1").lower()
                if v not in ("1", "0", "on", "off", "true", "false"):
                    self._json({"ok": False, "error": f"on={v!r}: want 1|0"}, 400)
                    return
                ok, res = p.set_sound(v in ("1", "on", "true"))
                if ok:
                    self._json({"ok": True, "phase": res, "sound": p.sound})
                else:
                    self._json({"ok": False, "note": res, "phase": p.phase, "sound": p.sound,
                                "busy": p.sound_busy or p.card_busy})
            else:
                self._json({"ok": False, "error": "no such audio endpoint",
                            "endpoints": ["/audio/status", "/audio/pcm", "/audio.wav", "/audio/enable"]}, 404)
        except ValueError as e:
            self._json({"ok": False, "error": f"bad number: {e}"}, 400)

    def do_POST(self):
        path, _, q = self.path.partition("?")
        args = dict(kv.split("=", 1) for kv in q.split("&") if "=" in kv)
        if path == "/samples/upload":
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n > 0 else b""
            self._samples(path, args, body)
        else:
            self._send(404, b"?", "text/plain")

    def do_GET(self):
        p = self.panel
        path, _, q = self.path.partition("?")
        args = dict(kv.split("=", 1) for kv in q.split("&") if "=" in kv)
        if path == "/":
            # read per request: a page fix must not need a server (or app) restart
            page = pathlib.Path(__file__).parent / "panel.html"
            self._send(200, page.read_bytes() if page.exists() else self.html, "text/html; charset=utf-8")
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
                            "restarts": p.restarts,
                            "card_busy": p.card_busy,       # a /samples/commit re-insert in progress
                            "clock": p.clock_note,          # what the boot-time YES found
                            "sound": p.sound,               # the child runs --dsp and its main out is captured
                            "sound_note": p.sound_note})
        elif path.startswith("/samples"):
            self._samples(path, args)
        elif path.startswith("/audio"):
            self._audio(path, args)
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
        elif path == "/tap":
            # n presses of one key inside one action (the double-tap chords:
            # /tap?row=0x22&bit=0&n=2 opens track 1's sample slot list)
            ok, res = p.tap(int(args.get("row", "-1"), 0), int(args.get("bit", "-1")),
                            n=int(args.get("n", "1")), hold=float(args.get("hold", "50")),
                            gap=float(args.get("gap", "150")))
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
    ap.add_argument("--audio", action="append", default=[], metavar="DIR",
                    help="seed the sample pool with every WAV/AIFF in DIR (converted when the unit "
                         "would not read it), in addition to the project's sibling AUDIO; repeatable")
    ap.add_argument("--sound", choices=("on", "off"), default="on",
                    help="on (default): the port child runs --dsp and its main output is captured "
                         "(/audio/status, /audio/pcm, /audio.wav, takes on PLAY..STOP; boot ~1 min, the "
                         "sequencer ~9x slower than real time while it plays, ~3x slower than without the cores); off: no DSP cores, no sound. "
                         "--port-arg=--dsp is the same as on; --sound off wins over it")
    a = ap.parse_args()

    # Default to the STOCK image: out/mainos_bus.bin is whatever the last
    # build or gate left there (verify_burn leaves a probe build that never
    # reaches the UI -- a blank screen, 11 Sep 2026). Pass --image to test
    # a built remix deliberately.
    image = a.image or str(ROOT / "out/raw/section_3_MAIN_OS.bin")

    # Bind FIRST, before anything below touches the port's files: the pool
    # wipe, the staging tree and the card image are all keyed by port, and a
    # second `panel_server.py --port <busy>` (the app on 8563 up while the
    # README's default CLI is run, a relaunch over a lingering server) used
    # to wipe the running server's pool back to the fixture, rebuild its
    # staging tree and overwrite its card image, THEN exit on the bind --
    # the next respawn booted without the user's samples (verifier, 12 Sep
    # 2026: 50 pool files -> 37). Nothing is served until serve_forever.
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError as e:
        sys.exit(f"panel: port {a.port}: {e}")

    # The sample pool (SamplePool): the set's AUDIO folder the card is built
    # from, one per server port. The project's own samples seed it as they
    # were saved: project.work references them as ../AUDIO/<file>, so the
    # sibling AUDIO/ next to the project dir is the set's pool, and without
    # them every sample slot stays invalid (RTOS_FORK section 10.12) -- the
    # UI still works, the audio does not. --audio dirs go through add()
    # (converted when the unit would not read them).
    pool = SamplePool(ROOT / "out" / f"_panel_pool_{a.port}")
    project_dir = pathlib.Path(a.project).resolve() if a.project else None
    if project_dir is not None:
        pool.seed(project_dir.parent / "AUDIO", convert=False)
    for d in a.audio:
        pool.seed(d, convert=True)
    # One staging tree per server port: stage_project wipes and remakes its
    # tree, and two panels started together on the shared default raced on
    # it (FileExistsError in mkdir, 12 Sep 2026). The same builder rebuilds
    # the card for /samples/commit.
    tree = ROOT / "out" / f"_panel_stage_{a.port}"
    builder = lambda: build_card(a.project, a.set, a.name, tree, pool)  # noqa: E731
    card, staged, staged_audio = builder()
    project = (a.set, staged) if a.project else None
    if a.project:
        print(f"staged {project_dir.name} as {a.set}/{staged} with {len(pool.names())} samples (pool {pool.path})")
    else:
        print(f"empty project card with {len(pool.names())} samples (pool {pool.path})")

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
    # Sound: --dsp is --sound's flag now. A script's --port-arg=--dsp still
    # boots the cores (and gets the capture with them); an explicit --sound
    # off drops it, and says so.
    sound = a.sound == "on"
    port_args = [x for x in a.port_arg if x != "--dsp"]
    if len(port_args) != len(a.port_arg) and not sound:
        print("panel: --sound off: --port-arg=--dsp dropped (the child runs without the DSP cores)")
    takes_dir = ROOT / "out" / f"_panel_takes_{a.port}"
    Handler.panel = Panel(image, card, project=project, internal_clock=not a.midi_clock,
                          backend=backend, port_bin=a.port_bin, port_args=port_args,
                          card_file=card_file, backend_note=note, auto=a.backend == "auto",
                          play_pump_ms=25.0 if backend == "port" else 10.0,
                          pool=pool, card_builder=builder, staged_audio=staged_audio,
                          sound=sound, takes_dir=takes_dir)
    Handler.html = (pathlib.Path(__file__).parent / "panel.html").read_bytes()
    print(f"panel: http://localhost:{a.port}/   image={image}   backend={backend}"
          f"   sound={'on' if Handler.panel.sound_wanted else 'off'} (takes in {takes_dir})")
    try:
        srv.serve_forever()
    finally:
        Handler.panel._close_take(None)     # a take open at exit stays a valid WAV
        if Handler.panel.proc is not None:
            Handler.panel.proc.quit()


if __name__ == "__main__":
    main()
