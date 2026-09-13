# The CPU -> front-panel link (UART@fc064000)

What the MAIN OS sends to the panel microcontroller, decoded far enough to
draw the LCD and the LEDs from the byte stream alone. Decoder:
`tools/panel/panel_link.py` (`PanelLink`, pure stdlib). Read 11 Sep 2026
from `out/raw/section_3_MAIN_OS.bin`; measured against route-A captures
(`rt.uart64.tx`) of a boot on an empty card, MIXER open/close, an encoder
turn, PATTERN SETTINGS, cursor-right, a trig key and a track key.

## Image base, first

The file loads at **0x40000400** (`emu_bringup.BASE`, a 0x400 header), so
`m68k-elf-objdump ... --adjust-vma=0x40000400` is the listing whose
addresses match the firmware's own `jsr` operands; every address in this
file is from that listing. With `--adjust-vma=0x40000000` the same code
sits 0x400 lower and every routine below lands mid-instruction (0x40010b1c
is then inside a `clrl`), which is how the earlier "0x10 <chunk>" reading
went wrong. (Until 11 Sep 2026 the producers table below quoted the ring
writer / byte pusher / drain as 0x4001071c / 0x400106a4 / 0x4001064c: those
are the 0x40000000-listing addresses of the same three routines, 0x400 low;
in the 0x40000400 listing the first two are mid-instruction and the third
is an unreferenced `bgtw`.)

## Producers: every path onto the wire

All traffic on the panel UART comes from six places (reference counts are
`grep -cE '0x4001....\b'` over the 0x40000400 listing):

| routine | what |
|---|---|
| `0x40010b1c` ring writer `(len, ptr)` | `moveal %sp@(16),%a2`, spins while the ring count `[0x400b96cc]` > 2046, pushes `len` bytes into the 2 KB ring at `[0x400b96bc]`, arms the TX interrupt (`UIMR := 3`). 5 references: `0x40013a6e` (clear), `0x40013d20` (flush), `0x4001f99e`, `0x4001fa1c`, `0x400926c6` |
| `0x40010aa4` byte pusher `(byte)` | one byte, same ring. 12 references; every builder loads it with `lea 0x40010aa4,%aN` and `jsr %aN@` |
| `0x40010a4c` polled drain | spins on TXRDY (`0xfc064004`) and empties the ring. 9 references: boot LED animation `0x4006308a`/`0x40063102`/`0x40063186`/`0x400633bc`, `0x4000fa86`, `0x4007fcfc`-`0x40080152` |
| `0x400109bc` UART1 ISR | drains the ring on TX-ready, hands RX bytes to `[0x460ba980]` |
| `0x4001f40c`, `0x4001f4dc` | boot-time polled handshake, direct `UTB` writes; only on the alternate panel path (flag `0x46c8d18c`), never taken under emulation |

Every message builder takes the mutex `0x400b96f4` (`0x40010db0` lock,
`0x40010d90` unlock) around its pushes, so messages never interleave: the
first byte of a message decides its length and nothing else is needed to
frame the stream.

## Opcode table (first byte -> length)

| first byte | len | builder | message |
|---|---|---|---|
| `0x10`-`0x17` | 10 | `0x40013abc` diff flush, `0x40013a24` clear | **LCD block**: `page = op & 7`, then the column start (only 0, 8, ..., 120: both builders step it by 8 to 128, `0x40013a92`/`0x40013d38`), then 8 column bytes (columns `col..col+7` of that page) |
| `0x20`-`0x2f` | 2 | `0x40013634`, `0x40062fec`, `0x400631fc` init | **LED bitmap row** `op & 0xf`, one byte of on/off bits (bit n = LED `row*8+n`) |
| `0xa0`-`0xaf` | 2 | `0x40013634` | LED bitmap row `16 + (op & 0xf)` (17 rows = 136 LEDs; the state array is `0x460ba9ae` XOR the blink phase `0x460ba98c`) |
| `0x30`-`0x3f` | 2 | `0x400135b0` (cache `0x400b9714`), `0x4006322c` init | **LED level**: nibble `op & 0xf`, then the LED id. Init sends `3f 00 .. 3f 57` (88 ids at 15) |
| `0x40`-`0x4f` | 1 | `0x400926d8` | one-byte command `0x40 | (n & 0xf)`; boot sends `0x43` (`0x4001f9ac`) |
| `0x60`, `0x74` | 2 | `0x4001fa08`, `0x4001f98a` | `60 00` / `74 00`, alternate panel boot path only (flag `0x46c8d18c`) |
| `0xb5` | 6 | `0x40013368` via `0x400133cc` | 16-bit index (`2*a+b`), then three bytes; sent when the LED brightness setting changes (`0x4003f430`). Palette/brightness table entry -- inferred from the caller, not measured |
| `0xb7` | 2 | `0x400926a8` | LCD backlight 0-255 (`0x4003f430` from the table at `0x400a7632`; the screen saver at `0x400523da` sends 0 and clears the LCD after 216000 ticks) |

Anything else is unknown: `PanelLink` skips one byte, counts it in
`stats["unknown"]`, keeps the last 16 in `unknown_tail`, and carries on; a
`0x1n` whose column byte is not one of 0, 8, .., 120 is skipped the same
way (also counted in `stats["lcd_badcol"]`). None occurred in 9429
captured bytes (1367 messages, 837 LCD blocks) or in the self-test's boot +
MIXER stream (6861 B).

Resync is by first byte only -- no length field, no checksum -- so what
junk does depends on whether it looks like an opcode (measured 11 Sep 2026
with `out/_agents/lcd/verify_fix.py`, junk inserted at the last message
boundary of the 9429-byte capture, offset 9419):

- not an opcode (`ff fe 00`, `99`, `50..5f`): skipped a byte at a time;
  final frame, LEDs and commands identical to the clean stream;
- a `0x1n` with a bad column byte (`12 03`): rejected, identical;
- a `0x1n` with a valid column byte (`10 00`, `17 78`): framed as an LCD
  block and eats the next 8 bytes (here the head of the last real block: 23
  / 19 pixels wrong, the 2 leftover bytes skipped as unknown). Because the
  firmware's flush is a diff, a misframed block stays on the decoded screen
  until the firmware next redraws it; the same junk inserted mid-stream
  (offset 3413, before the post-boot full draw) left no trace;
- a bare `3f`: takes the next byte as an LED id, then the block is misread
  (7 pixels wrong the same way).

What the naive pair parser saw: `00 ff`, `00 00`, `80 be`, `8a be` were LCD
column bytes; `0x11 08` is page 1, column 8; the "chunks 247/248" were LCD
bytes read as an opcode; the "id 0x00-0x5b, value 0x3f" pairs are really
`3f <id>` (level 15, ids 0-0x57) read one byte late after the bare `0x43`.

## LCD geometry (measured)

128x64, eight pages of 128 column bytes, exactly what the two builders walk
(`for page in 0..7: for col in 0,8,..,120`, column-major back buffers at
`[0x400b9710]`/`[0x400b970c]`, swapped after each flush). The panel shows
message **page p at screen rows `8*(7-p) .. 8*(7-p)+7`, bit 0 at the top of
the band**: page 7 is the top of the screen.

    pixel(x, y) = frame[(7 - y//8) * 128 + x] >> (y & 7) & 1

Evidence, from the decoded stream (PNGs `out/_agents/lcd/selftest_*.png`,
`mixer-open_B.png`, `boot+400ms_B.png`, `pattern-settings_B.png`):

- MIXER: inverted "MIXER" title bar rows 0-8, then MIX / OUT / MAIN / CUE /
  DIR / GAIN A B, C D, then "MUTE", "AUDIO" / "MIDI" with the 8+8 mute
  boxes along the bottom. With page 0 on top the same bytes put the title
  at rows 55-63 and the section headers under their contents.
- SET DATE/TIME: bar at rows 8-16, "FRIDAY", the RTC date/time, "LAST SET:"
  above its value `0000-00-00 00:00:00`; the main screen's "PLAYBACK>STATIC
  ... 01->09" bar along rows 57-63 and the BPM "120.0" top-left.
- PATTERN SETTINGS: "SELECT PATTERN" window, "A01", same footer.

There is no display-start-line / scroll opcode in the table, so the page
order on screen is fixed; the "rotated" renders from RAM at `0x460d1f80`
are that buffer's problem (it is not one of the two UART back buffers),
not the panel's. The stream is the ground truth.

Bit order within a page byte is the same as `panel_server.FB`'s formula
(`buf[x*8 + (63-y)//8]` bit `7-(63-y)%8` == page `7-y//8`, bit `y&7`).

## Traffic per action (empty card, stock image)

| action | bytes | messages |
|---|---|---|
| boot + 400 ms | 5731 | `43`; 440 LED levels; 470 LCD blocks (full frame plus redraws); 76 LED rows |
| MIXER down | 1130 | 112 LCD blocks; `20 55`, `21 55`, `22 ff`, `23 ff`, `3f 66` |
| encoder row 0x30, +2 | 22 | 2 LCD blocks; `2c 40` |
| MIXER again (close) | 1130 | 112 LCD blocks; rows 0-3 and 0x2c back to 0 |
| PATTERN SETTINGS | 522 | 52 LCD blocks; `20 01` |
| cursor-right | 120 | 12 LCD blocks |
| trig 1 down / up | 520 / 2 | 52 LCD blocks / `20 00` |
| track 2 | 72 | 7 LCD blocks; `25 a6` |

A quiet screen sends nothing (0 bytes over 500 ms idle): the flush is a
diff against the previous frame, block by block.

## Self-test

    .venv/bin/python3 tools/panel/panel_link.py --selftest      # 15-20 s wall, exit 0

Boots the stock image on an empty card (the boot is most of the wall time:
15.4 s and 17.1 s measured 11 Sep 2026), feeds the stream in 7-byte
pieces, opens MIXER, and asserts: no unknown bytes, nothing left unframed,
the `0x43` hello and 88 LED levels at init, the MIXER title bar in rows 0-8
and nothing inverted in the bottom band, the boot footer bar ending on row
63 and the dialog bar in rows 8-16, and that the whole stream fed in one
call decodes to the same frame / LEDs / commands / stats as the 7-byte
pieces. It writes `selftest_boot.png` and `selftest_mixer.png` under
`--out` (default `out/_agents/lcd/`).

    .venv/bin/python3 tools/panel/panel_link.py --decode tx.bin --png lcd.png

## Panel -> CPU: the RX parser (13 Sep 2026)

`0x4009228c` (RAM, 0x40000400 listing) runs on the UART's receive
interrupt over the ring at `0x46100b28` (`0x40092254` fills it). The FIRST
byte of a report decides its class by its high nibble and its payload
length; the low nibble is the row:

| first byte | payload | class |
|---|---|---|
| `0x2r` | 1 byte | **key matrix row r**, bitmask (set = held); changed bits against the last mask (`0x46100b18[r]`) become key events from the descriptor table at `[0x46c901dc] + (r*8+bit)*12` (+770 with the modifier row's key held -- never: the table's modifier row is 0xff), posted to the UI/sys queues. Rows 0-7 are all live entries (key codes 0x00-0x3f); **row 7 (`0x27`) is the encoders' push switches**, bit = the encoder (A-F = 0-5, LEVEL = 6): with a TRIG key held it toggles the step's parameter lock (KEYMAP.md, 13 Sep 2026) |
| `0x3r` | 1 byte | **encoder r**, signed detent delta; if the previous report is still unread it is ADDED into the pending message (`0x4009250c`), else a 6-byte message `{type, sub, delta, stamp}` is posted (`0x40092526`) |
| `0x40` | 1 byte | **the crossfader**: the ADC byte 0..255. Scaled by the calibration record at `0x1ffffe` (magic `0x1234`: min `[0x1ffffc]+1`, span from `[0x1ffffa]`, `0x400925ac`), none under emulation so `pos = (byte >> 1) & 127`; `0x40092fac` drops a repeat of the last value (`0x400d16cc`) and `0x40092f2c` writes it into a 2-byte ping-pong message `04 <pos>` at `0x400d16c8/ca` (coalesced while one is pending) and posts it to the sys queue registered in `0x46104ca4`. Sys kind 4 -> `0x40061e0a`: gated on AUDIO CC OUT having INT (`0x8000004a` bit 0), stores `0x460d16c8`, rebuilds the 10 weight longs `0x80003c60`, echoes CC 48 = 127-pos if EXT, runs the STRT/LEN/RATE morph `0x4003f1b4` and redraws the fader icon (`0x4003577c`, five glyphs from `0x400bcd7c`, LCD x 104-108 / y 59-61). Rows `0x41`-`0x4f` are ignored (`0x4009256a` wants row 0) |
| `0x7r` | 9 bytes | a report copied to `0x46100b48` with its pointer in `0x46100b52` (the panel's handshake/version reply; not seen under emulation) |
| anything else | -- | the parser stays in its header state |

Measured on the port (`out/_agents/panel-ctl/lab_params.py`, OTLIVE):
`0x40 255` -> `0x460d16c8` = 127, `0x40 0` -> 0, `128` -> 64, `64` -> 32,
`192` -> 96, `1` -> 0, `254` -> 127, `127` -> 63, `200` -> 100; each one
redraws the icon and rebuilds the weights (`0x80003c60` = `0x8000_0000` at
127 = scene A fully, `0x0000_8000` at 0 = scene B). Rows `0x27`-`0x2f`,
`0x37`-`0x3f`, `0x41`, `0x42`, `0x4f` with a value byte change nothing
(`0x27`/`0x37` nudge the tempo readout's redraw, as KEYMAP.md found for
`0x27`) -- alone: `0x27` with a TRIG key held is the encoder push, the
lock toggle (KEYMAP.md, 13 Sep 2026). The panel's own scaling of the pot to `0x40 <byte>` on the
hardware is not measured here (no unit); the message and the firmware's
side are.

## Not yet known

- The meaning of the `0xb5` five bytes and of `0x4n` for n != 3; `60 00` /
  `74 00` and the polled handshake (`60 02 70 00`, five-byte reply) on the
  alternate panel path.
- Which physical LED each bitmap bit and each level id is (the boot
  animation at `0x4006307c`-`0x40063178` walks ids 0-15 with levels
  14-30, a starting point for a map).
- The RX side's `0x7r` nine-byte report (its producer on the panel board)
  and the encoder message's type/sub bytes per row (the descriptor table at
  `[0x46c901dc]`); keys, encoders and the crossfader are decoded above.
