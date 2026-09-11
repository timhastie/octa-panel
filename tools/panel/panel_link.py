#!/usr/bin/env python3
"""The CPU -> front-panel protocol (UART@fc064000), decoded to what the LCD
and LEDs show. Pure stdlib. See PANEL_LINK.md for the evidence.

    from panel_link import PanelLink
    link = PanelLink()
    link.feed(bytes(rt.uart64.tx[pos:]))     # incremental, any chunking
    rows = link.lcd_rows()                   # 64 lists of 128 bools, top row first
    link.leds, link.led_rows, link.stats

Framing (read 11 Sep 2026 from every caller of the ring writer 0x40010b1c /
the byte pusher 0x40010aa4 in section_3_MAIN_OS.bin, image base 0x40000400;
each message is built under one mutex so it is never interleaved):

    first byte   len  meaning
    0x10-0x17    10   LCD block: page = op & 7, then column start (0,8,..,120;
                      any other column byte means this is not an LCD block),
                      then 8 column bytes (0x40013abc diff flush, 0x40013a24 clear)
    0x20-0x2f     2   LED bitmap row (op & 0xf), 8 on/off bits      (0x40013634)
    0xa0-0xaf     2   LED bitmap row 16 + (op & 0xf)                 (0x40013634)
    0x30-0x3f     2   LED level: nibble = op & 0xf, then the LED id (0x400135b0)
    0x40-0x4f     1   one-byte command; 0x43 is the boot hello      (0x400926d8)
    0x60, 0x74    2   two-byte commands, second byte 0 (alt panel boot path)
    0xb5          6   LED brightness/palette entry                   (0x40013368)
    0xb7          2   LCD backlight level 0-255                      (0x400926a8)

LCD geometry (measured on the MIXER and SET DATE/TIME screens): the panel
puts message page p at screen rows 8*(7-p) .. 8*(7-p)+7, bit 0 of a column
byte at the TOP of that band. Page 7 is the top of the screen.

    .venv/bin/python3 tools/panel/panel_link.py --selftest        # boots, opens MIXER, checks, writes PNGs
    .venv/bin/python3 tools/panel/panel_link.py --decode tx.bin --png out.png
"""
import struct
import sys
import zlib

LCD_W, LCD_H = 128, 64
_MSG_LEN = {}
for _op in range(0x10, 0x18):
    _MSG_LEN[_op] = 10
for _op in list(range(0x20, 0x30)) + list(range(0x30, 0x40)) + list(range(0xa0, 0xb0)):
    _MSG_LEN[_op] = 2
for _op in range(0x40, 0x50):
    _MSG_LEN[_op] = 1
_MSG_LEN.update({0x60: 2, 0x74: 2, 0xb5: 6, 0xb7: 2})


def opcode_family(op):
    """A short name per first byte, for stats and logs."""
    if 0x10 <= op < 0x18:
        return "lcd"
    if 0x20 <= op < 0x30 or 0xa0 <= op < 0xb0:
        return "led_row"
    if 0x30 <= op < 0x40:
        return "led_level"
    if 0x40 <= op < 0x50:
        return "cmd1"
    return {0x60: "cmd60", 0x74: "cmd74", 0xb5: "palette", 0xb7: "backlight"}.get(op, "unknown")


class PanelLink:
    """Incremental decoder of the firmware -> panel byte stream.

    Unknown first bytes are skipped one at a time and counted (`stats`
    ["unknown"], last few in `unknown_tail`); nothing is guessed. An LCD op
    whose column byte is not one of 0, 8, .., 120 (the only values the two
    builders ever push) is treated the same way. A message split across
    feed() calls waits in `pending` until the rest arrives.

    There is no checksum: junk that happens to start with a valid opcode and
    (for 0x1n) a valid column byte is framed as a message, and a misframed
    LCD block stays on the decoded screen until the firmware's diff flush
    redraws that block."""

    def __init__(self):
        self.frame = bytearray(LCD_W * 8)     # page-major: frame[page*128 + x]
        self.leds = {}                         # LED id -> level nibble (0x3n <id>)
        self.led_rows = {}                     # bitmap row -> mask byte (0x2r / 0xa0+r)
        self.backlight = None                  # last 0xb7 value
        self.commands = []                     # (op, args) for 0x4n / 0x60 / 0x74 / 0xb5
        self.pending = bytearray()
        self.unknown_tail = []                 # (stream offset, byte), last 16
        self.stats = {"bytes": 0, "messages": 0, "unknown": 0, "lcd_blocks": 0,
                      "lcd_badcol": 0, "ops": {}}
        self._offset = 0                       # stream offset of pending[0]
        self.dirty = False                     # an LCD block landed since last clear

    # -- input ---------------------------------------------------------------
    def feed(self, data):
        self.stats["bytes"] += len(data)
        buf = self.pending
        buf.extend(data)
        i = 0
        n = len(buf)
        while i < n:
            op = buf[i]
            need = _MSG_LEN.get(op)
            if need is not None and 0x10 <= op < 0x18:
                if i + 1 >= n:
                    break                      # column byte not here yet
                col = buf[i + 1]
                if col & 7 or col > LCD_W - 8:
                    self.stats["lcd_badcol"] += 1
                    need = None                # not an LCD block: skip the byte
            if need is None:
                self.stats["unknown"] += 1
                if len(self.unknown_tail) >= 16:
                    del self.unknown_tail[0]
                self.unknown_tail.append((self._offset + i, op))
                i += 1
                continue
            if i + need > n:
                break                          # wait for the rest
            self._message(buf[i:i + need])
            i += need
        del buf[:i]
        self._offset += i

    def _message(self, m):
        op = m[0]
        fam = opcode_family(op)
        self.stats["messages"] += 1
        self.stats["ops"][fam] = self.stats["ops"].get(fam, 0) + 1
        if fam == "lcd":
            page, col = op & 7, m[1]
            base = page * LCD_W + col
            self.frame[base:base + 8] = m[2:10]
            self.stats["lcd_blocks"] += 1
            self.dirty = True
        elif fam == "led_row":
            row = (op & 0xf) + (16 if op >= 0xa0 else 0)
            self.led_rows[row] = m[1]
        elif fam == "led_level":
            self.leds[m[1]] = op & 0xf
        elif fam == "backlight":
            self.backlight = m[1]
        else:
            self.commands.append((op, bytes(m[1:])))

    # -- output --------------------------------------------------------------
    def pixel(self, x, y):
        """True = dark. Page 7 is the top band; bit 0 is the top of a band."""
        return bool((self.frame[(7 - (y >> 3)) * LCD_W + x] >> (y & 7)) & 1)

    def lcd_rows(self):
        f = self.frame
        rows = []
        for y in range(LCD_H):
            base = (7 - (y >> 3)) * LCD_W
            bit = y & 7
            rows.append([bool((f[base + x] >> bit) & 1) for x in range(LCD_W)])
        return rows

    def led_bits(self):
        """Bitmap LED index (row*8 + bit) -> on, for every row seen."""
        out = {}
        for row, mask in self.led_rows.items():
            for b in range(8):
                out[row * 8 + b] = bool(mask & (1 << b))
        return out

    def render_png(self, scale=1, dark=0x1a, light=0xc9):
        rows = []
        for r in self.lcd_rows():
            line = bytes(dark if v else light for v in r)
            if scale > 1:
                line = bytes(v for v in line for _ in range(scale))
            rows.extend([line] * scale)
        return _png_gray(LCD_W * scale, LCD_H * scale, rows)


def _png_gray(w, h, rows):
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def bars(rows, x0=0, x1=LCD_W, min_run=7, floor=0.3, mean=0.5):
    """The inverted bars: maximal runs of >= min_run rows whose [x0,x1) span
    is at least `floor` dark on every row and `mean` dark on average (white
    text inside a bar takes a row down to ~0.35). Returns [(y0, y1)]."""
    dark = [sum(r[x0:x1]) / (x1 - x0) for r in rows]
    out, y = [], 0
    while y < len(dark):
        if dark[y] < floor:
            y += 1
            continue
        y0 = y
        while y < len(dark) and dark[y] >= floor:
            y += 1
        run = dark[y0:y]
        if len(run) >= min_run and sum(run) / len(run) >= mean:
            out.append((y0, y - 1))
    return out


# -- self-test ---------------------------------------------------------------
def selftest(out_dir):
    """Boot the stock image on an empty card, open MIXER, decode the UART
    stream, write PNGs, check the title-bar geometry and that decoding is
    chunking-independent. ~15-20 s wall (15.4 s and 17.1 s measured 11 Sep
    2026; the boot itself is most of it)."""
    import pathlib
    import time
    t0 = time.monotonic()
    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "tools")); import toolpath  # noqa: E402,F401
    import emu_card as ec  # noqa: E402
    import emu_rtos as er  # noqa: E402
    from panel_server import install_rtc  # noqa: E402

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tree = root / "out/_panel_tree"
    (tree / "OCTABAM" / "AUDIO").mkdir(parents=True, exist_ok=True)
    card = ec.build_image(str(tree), size_mb=64)
    install_rtc()
    r, rt = er.attach(None, card)
    link = PanelLink()
    pos = 0

    def pump(ms):
        nonlocal pos
        rt.run(ms=ms)
        tx = rt.uart64.tx
        # feed in odd-sized pieces on purpose: framing must not care
        while pos < len(tx):
            step = min(7, len(tx) - pos)
            link.feed(bytes(tx[pos:pos + step]))
            pos += step

    pump(400)
    boot = link.lcd_rows()
    (out / "selftest_boot.png").write_bytes(link.render_png(3))
    boot_stats = dict(link.stats, ops=dict(link.stats["ops"]))
    rt.uart64.rx.extend([0x26, 0x01]); pump(60)     # MIXER down
    rt.uart64.rx.extend([0x26, 0x00]); pump(200)    # MIXER up
    mixer = link.lcd_rows()
    (out / "selftest_mixer.png").write_bytes(link.render_png(3))

    fails = []
    if link.stats["unknown"]:
        fails.append(f"unknown bytes: {link.unknown_tail}")
    if link.pending:
        fails.append(f"{len(link.pending)} bytes left unframed")
    if link.stats["lcd_blocks"] < 128:
        fails.append(f"only {link.stats['lcd_blocks']} LCD blocks")
    # the boot hello and the LED init (levels for ids 0..0x57)
    if (0x43, b"") not in link.commands:
        fails.append("no 0x43 hello")
    if len(link.leds) < 0x58:
        fails.append(f"only {len(link.leds)} LED ids levelled at init")
    # MIXER: the inverted title bar fills the top band (rows 0-8 measured
    # 11 Sep 2026); a page-0-top decode (or the RAM render) puts it at the
    # bottom instead, so both ends are checked.
    title = bars(mixer, 40, 88)
    if not any(y0 <= 1 and y1 >= 7 for y0, y1 in title):
        fails.append(f"MIXER title bar not in the top band: bars {title}")
    if any(y0 >= 48 for y0, y1 in title):
        fails.append(f"MIXER inverted bar in the bottom band: {title}")
    # boot: the page-name bar ("PLAYBACK>STATIC") along the bottom edge and
    # the SET DATE/TIME bar in the second band (rows 8-16 measured)
    foot = bars(boot, 0, 48)
    if not any(y0 >= 56 and y1 == 63 for y0, y1 in foot):
        fails.append(f"boot footer bar not along the bottom edge: bars {foot}")
    if not any(4 <= y0 <= 10 and 14 <= y1 <= 20 for y0, y1 in foot):
        fails.append(f"SET DATE/TIME bar not in rows 8-16: bars {foot}")
    # framing must not depend on how the bytes arrived: the whole stream in
    # one feed() must decode to the same state as the 7-byte pieces
    whole = PanelLink()
    whole.feed(bytes(rt.uart64.tx))
    if ((whole.frame, whole.leds, whole.led_rows, whole.commands, whole.backlight, whole.stats)
            != (link.frame, link.leds, link.led_rows, link.commands, link.backlight, link.stats)):
        fails.append("whole-stream feed decodes differently from 7-byte pieces")
    print(f"stream {link.stats['bytes']} B, {link.stats['messages']} messages, ops {link.stats['ops']}")
    print(f"boot: {boot_stats['lcd_blocks']} LCD blocks, hello {[c for c in link.commands if c[0] == 0x43]}")
    print(f"MIXER bars (x40-88) {title}; boot bars (x0-48) {foot}")
    print(f"LED rows {dict(sorted(link.led_rows.items()))}")
    print(f"PNGs: {out / 'selftest_boot.png'}, {out / 'selftest_mixer.png'}")
    print(f"wall {time.monotonic() - t0:.1f} s")
    for f in fails:
        print("FAIL:", f)
    return not fails


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default="out/_agents/lcd", help="where --selftest writes PNGs")
    ap.add_argument("--decode", help="a captured firmware->panel byte stream to decode")
    ap.add_argument("--png", help="write the decoded LCD here (with --decode)")
    ap.add_argument("--scale", type=int, default=3)
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest(a.out) else 1)
    if a.decode:
        link = PanelLink()
        link.feed(open(a.decode, "rb").read())
        print(link.stats, "pending", len(link.pending), "backlight", link.backlight)
        print("commands", link.commands[:8])
        print("led rows", dict(sorted(link.led_rows.items())))
        if a.png:
            open(a.png, "wb").write(link.render_png(a.scale))
        else:
            for r in link.lcd_rows():
                print("".join("#" if v else "." for v in r))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
