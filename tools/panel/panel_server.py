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

    def __init__(self, image, card, pump_ms=25.0, project=None, internal_clock=True):
        self.image = image
        self.card = card
        self.pump_ms = pump_ms
        self.project = project            # (set_name, project_name) to load at boot
        self.internal_clock = internal_clock
        self.loaded = None                # load_project_live's tuple once done
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
        threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def _read_table(image):
        data = pathlib.Path(image).read_bytes()
        off = KEY_TABLE - 0x40000000
        return [struct.unpack(">I", data[off + i * 4:][:4])[0] for i in range(KEY_COUNT)]

    def _snapshot(self, uc):
        buf = bytes(uc.mem_read(FB, 1024))
        on, off = b"\x1a", b"\xc9"   # dark pixels on a pale LCD
        rows = []
        for y in range(64):
            yy = 63 - y
            page, bit = yy >> 3, 7 - (yy & 7)
            rows.append(b"".join(
                on if (buf[x * 8 + page] >> bit) & 1 else off
                for x in range(128)))
        png = _png_gray(128, 64, rows)
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
            if self.project:
                self._load_project(rt)
        except Exception as e:  # boot is all-or-nothing
            self.fault = f"boot: {type(e).__name__}: {e}"
            return
        while True:
            try:
                while True:
                    try:
                        act = self.actions.get_nowait()
                    except queue.Empty:
                        break
                    act()
                t = time.perf_counter()
                rt.run(ms=self.pump_ms)
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
        rt.frame = True
        rt.next_frame = rt.sample + er.FRAME_PERIOD
        rt.exact_clock()
        self.loaded = {"mounted": mounted, "posted": posted, "saved_bank": saved_bank,
                       "final_bank": final_bank, "elapsed_ms": elapsed}
        self._snapshot(rt.uc if hasattr(rt, "uc") else self.r.uc)

    def transport(self, what):
        """PLAY / REC / STOP through the firmware's own key handlers
        (press_key_live, RTOS_FORK.md section 9) -- the proven way to start
        the transport until the matrix cells for these keys are mapped."""
        if what == "play":
            # press_play_live = PLAY's own handler + FW_START_TRACK per track;
            # tracks only start if the pattern flags them active, so make sure.
            def play(rt):
                flags = self.activate_tracks(rt)
                return f"d0={rt.press_play_live()} active={flags}"
            return self.do(play, timeout=120)
        handler = {"rec": er.KEY_REC, "stop": er.KEY_STOP}.get(what)
        if handler is None:
            return False, "play|rec|stop"
        return self.do(lambda rt: rt.press_key_live(handler), timeout=120)

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
        def act(rt):
            rt.uart64.rx.extend([row, state])
            rt.run(ms=50)
            return f"row {row:#04x} = {state:#04x}"
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
                            "fault": p.fault, "image": str(p.image)})
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
        elif path == "/transport":
            ok, res = p.transport(args.get("k", ""))
            self._json({"ok": ok, "result": str(res)})
        elif path == "/poke_trig":
            ok, res = p.poke_trig(args.get("step", "1"))
            self._json({"ok": ok, "result": str(res)})
        elif path == "/project":
            self._json({"project": p.project, "loaded": p.loaded})
        elif path == "/leds":
            with p.lock:
                self._json({"bits": bytes(p.led_bits).hex(),
                            "ids": {f"{k:#04x}": v for k, v in p.led_ids.items()}})
        elif path == "/run":
            ms = float(args.get("ms", 100))
            ok, res = p.do(lambda rt: rt.run(ms=min(ms, 5000)), timeout=600)
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
