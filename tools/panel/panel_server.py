#!/usr/bin/env python3
"""The virtual front panel: the emulated unit with a clickable UI.

Serves a browser page that shows the firmware's REAL screen -- the LCD
framebuffer the draw primitives paint at FB (found 10 Sep 2026 by hooking
memory writes from the draw-primitive region during a menu draw: 16
bytes/row, MSB left, 128x64) -- and injects key presses through the
firmware's own per-key jump table (KEY_TABLE, RTOS_FORK.md section 9), the
same path `press_key_live` proved against the M6c fidelity gate.

Runs route A (emu_rtos): the real scheduler, so the UI task consumes what a
key handler posts and repaints -- a handler called under route B changes
state but nothing redraws (measured: 15 handlers, zero framebuffer changes).

    .venv/bin/python3 tools/panel/panel_server.py                  # built image, empty card
    .venv/bin/python3 tools/panel/panel_server.py --image out/raw/section_3_MAIN_OS.bin
    .venv/bin/python3 tools/panel/panel_server.py --project <dir> [--set S] [--name N]

Then open http://localhost:8563/. Unmapped keys: the MAP drawer lists every
table entry; click one, watch the screen, name it. The mapping lives in the
browser (localStorage) and exports as JSON -- send a completed map back as
a PR to key_map.json.
"""
import argparse
import json
import pathlib
import queue
import struct
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


class Panel:
    """Owns the emulator thread; everything Unicorn happens on it."""

    def __init__(self, image, card, pump_ms=25.0, project=None, internal_clock=True,
                 play_pump_ms=10.0):
        self.image = image
        self.card = card
        self.pump_ms = pump_ms
        self.play_pump_ms = play_pump_ms   # the pump while frame mode is on (see _loop)
        self.project = project            # (set_name, project_name) to load at boot
        self.internal_clock = internal_clock
        self.loaded = None                # load_project_live's tuple once done
        self.phase = "booting"            # booting -> loading project -> ready
        self.actions = queue.Queue()
        self.lock = threading.Lock()
        self.frame = b""          # latest PNG
        self.seq = 0
        self.fault = None
        self.ran_ms = 0.0
        self.booted = False
        self.led_bits = bytearray(64)
        self.led_ids = {}
        self.handlers = self._read_table(image)
        try:
            from panel_link import PanelLink     # tools/panel/panel_link.py, when present
            self.link = PanelLink()
        except Exception:
            self.link = None
        threading.Thread(target=self._loop, daemon=True).start()

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
        rows = [b"".join(on if px else off for px in row) for row in self._lcd_rows(uc)]
        png = _png_gray(128, 64, rows)
        if self.link is not None:
            self.link.dirty = False
        with self.lock:
            if png != self.frame:
                self.frame = png
                self.seq += 1

    def _loop(self):
        try:
            r, rt = er.attach(self.image if self.image != "raw" else None, self.card)
            self.r, self.rt = r, rt
            self.booted = True
            self._snapshot(r.uc)
            # Every boot opens SET DATE/TIME (the "last set" record lives in
            # RAM at 0x80000080 and is zero on a fresh boot). YES (matrix
            # 0x26 bit 1) stores the clock and closes it (measured 11 Sep
            # 2026); on the bare main screen YES would instead open the
            # ARM ALL popup, so press it exactly once, here, while the
            # dialog is certainly up.
            self.phase = "closing the clock dialog"
            rt.run(ms=400)
            rt.uart64.rx.extend([0x26, 0x02]); rt.run(ms=60)
            rt.uart64.rx.extend([0x26, 0x00]); rt.run(ms=300)
            self._snapshot(r.uc)
            if self.project:
                self.phase = "loading project (about a minute)"
                self._load_project(rt)
                # Settle before replaying clicks queued during the load: a
                # key event injected as the very first thing after the load
                # wedged emulated time (run(ms=50) never returned, 11 Sep 2026).
                rt.run(ms=300)
                self._snapshot(r.uc)
            self.phase = "ready"
        except Exception as e:  # boot is all-or-nothing
            self.fault = f"boot: {type(e).__name__}: {e}"
            self.phase = "failed"
            return
        threading.Thread(target=self._watchdog, daemon=True).start()
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
                # all in tools/emu, nothing of it in this file.
                rt.run(ms=self.pump_ms if not rt.frame else min(self.pump_ms, self.play_pump_ms))
                self.busy_since = None
                self.ran_ms = rt.sample / er.SAMPLE_HZ * 1000.0
                self._snapshot(r.uc)
                self._parse_leds(rt)
                # idle bursts return instantly; don't spin the host
                if time.perf_counter() - t < 0.01:
                    time.sleep(0.03)
            except Exception as e:
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
            rt.uart64.rx.extend([row, delta & 0xff])
            rt.run(ms=30)
            note = ""
            if row < 0x36 and setup_window_open(rt.uc):
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
        transport()). Tracks only start if the pattern flags them active
        and the fixture projects have every flag clear (activate_tracks);
        the DSP frame interrupt is what steps the sequencer, so frame mode
        goes on (~17x wall time while it is on; off again in _after_stop).
        Without this the matrix PLAY only flipped the transport word and
        lit the PLAY LED: the position bar never moved (12 Sep 2026)."""
        flags = self.activate_tracks(rt)
        if not rt.frame:
            rt.frame = True
            rt.next_frame = rt.sample + er.FRAME_PERIOD
        return flags

    def _after_stop(self, rt):
        rt.frame = False

    def transport(self, what):
        """PLAY / REC / STOP through the firmware's own key handlers
        (press_key_live, RTOS_FORK.md section 9) -- the fallback the page
        used for these keys before their matrix cells were measured; kept
        for scripting. Same helpers around the key as key() uses."""
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
        handler = {"rec": er.KEY_REC, "stop": er.KEY_STOP}.get(what)
        if handler is None:
            return False, "play|rec|stop"
        def press(rt):
            d0 = rt.press_key_live(handler)
            if what == "stop":
                self._after_stop(rt)
            return f"d0={d0}" + (" (frame mode off)" if what == "stop" else "")
        return self.do(press, timeout=120)

    def activate_tracks(self, rt, tracks=range(8)):
        """Mark tracks ACTIVE in the current pattern record (+84 + 2330*t):
        FW_START_TRACK only promotes an active track to running, and a
        pattern with no saved trigs (the public fixture projects) has every
        flag clear -- PLAY then starts nothing and no trig ever lands
        (measured 10 Sep 2026: flags [0]*8, states [0]*8, zero nibble
        writes; with track 1's flag set: state 1 and the poked trig fires).
        A project saved on a unit with trigs carries the flags itself."""
        pb = rt.pattern_base()
        for t in tracks:
            rt.uc.mem_write(pb + 84 + 2330 * t, b"\x01")
        return [rt.uc.mem_read(pb + 84 + 2330 * t, 1)[0] for t in range(8)]

    def poke_trig(self, step, track=0):
        def act(rt):
            flags = self.activate_tracks(rt, [track])
            return f"mask {rt.poke_trig(int(step)):#04x}, active flags {flags}"
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
        while rt.sample < end:
            left = (end - rt.sample) / er.SAMPLE_HZ * 1000.0
            rt.run(ms=min(left, self.RUN_SLICE_MS))
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
            except Exception as e:
                box["r"] = f"{type(e).__name__}: {e}"
                box["ok"] = False
            done.set()
        self.actions.put(act)
        if not done.wait(timeout):
            return False, "timeout (emulator busy)"
        return box["ok"], box["r"]

    def press(self, idx, edge):
        if not (0 <= idx < KEY_COUNT):
            return False, "bad index"
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
        def act(rt):
            note = ""
            if play:
                note = f" (play: active={self._before_play(rt)}, frame mode on)"
            rt.uart64.rx.extend([row, state])
            rt.run(ms=50)
            if stop:
                self._after_stop(rt)
                note = " (stop: frame mode off)"
            return f"row {row:#04x} = {state:#04x}{note}"
        return self.do(act, timeout=120)


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
        elif path == "/status":
            with p.lock:
                self._json({"booted": p.booted, "seq": p.seq, "ran_ms": p.ran_ms,
                            "fault": p.fault, "image": str(p.image), "phase": p.phase})
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
        card, staged = er.stage_project(a.project, a.set, a.name, audio=audio,
                                        image_mb=max(64, 16 + sum(w.stat().st_size for w in pool.glob("*.wav")) // 2**20 if pool.is_dir() else 64))
        project = (a.set, staged)
        print(f"staged {pdir.name} as {a.set}/{staged} with {len(audio)} samples")
    else:
        tree = ROOT / "out/_panel_tree"
        (tree / a.set / "AUDIO").mkdir(parents=True, exist_ok=True)
        card = ec.build_image(str(tree), size_mb=64)

    if not a.no_rtc:
        install_rtc()
    Handler.panel = Panel(image, card, project=project, internal_clock=not a.midi_clock)
    Handler.html = (pathlib.Path(__file__).parent / "panel.html").read_bytes()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"panel: http://localhost:{a.port}/   image={image}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
