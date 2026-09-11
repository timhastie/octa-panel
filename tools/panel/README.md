# The virtual front panel

A clickable Octatrack in the browser, driven by the real firmware under the
route-A emulator (`tools/emu/emu_rtos.py`). The screen is the firmware's own
LCD; the keys go in through the panel scanner's own wire. It is a way to
*operate* a built image before flashing — walk menus, open pages, start the
sequencer — not just boot it.

```sh
.venv/bin/python3 tools/panel/panel_server.py                 # built image (out/mainos_bus.bin) if present, else stock
.venv/bin/python3 tools/panel/panel_server.py --image out/raw/section_3_MAIN_OS.bin
.venv/bin/python3 tools/panel/panel_server.py --project ~/octa/backups/<snap>/<project>
```

Open <http://localhost:8563/>.

## What is real, and what it took to find

**The screen** is the firmware's LCD framebuffer at `0x460d1f80`, found by
hooking the memory writes the draw primitives make during a menu draw. It is
**column-major**: 128 columns of 8 page-bytes, and within a page byte **bit 7
is the topmost pixel** of its eight rows (`pixel(x,y) = buf[x*8 + (63-y)//8]`
bit `7-(63-y)%8`). The server rasterizes it to a PNG each pump and the page
re-fetches on change. This is pixel-exact — it draws icons, dials and the
selection the text-capture oracle (`tools/remix/`) never could.

**The keys** are matrix reports on the panel UART at `0xfc064000` — the same
line the firmware sends its LED traffic out on, receive-interrupt already
armed. A key event is two bytes: `<row> <column-bitmask>`, a set bit held, a
cleared bit released (`0x20`–`0x2f` seen so far). The server keeps per-row
state so held-modifier chords (FUNC-style) work; verified by opening MIXER
(`0x26` bit 0) and PATTERN SETTINGS (`0x25` bit 6) and by ticking the SET
DATE/TIME field with a held chord.

**The LEDs** come back on the same UART and are parsed for the panel
(`0x10 <offset> <8 bytes>` bitmap blocks; `<id> <value>` pairs).

The older jump-table path (`press_key_live`, `RTOS_FORK.md` §9) is kept in
the server as `press()` — it calls a key's handler directly, which changes
state but does not redraw under route A, so the UART path is the real one.

## Mapping the rest of the panel

Only a handful of the matrix cells are identified. **MAP KEYS** opens the
matrix: click a cell to tap that key and watch the screen; to bind a panel
control, click it (it highlights), then click the cell that drives it. Your
bindings persist in the browser and **EXPORT MAP** copies them as JSON.
A completed map, dropped into `BUILTIN` in `panel.html` (or a `key_map.json`
beside it), is a good PR — it is pure discovery, no firmware bytes.

## Endpoints (for scripting)

| route | does |
|---|---|
| `GET /screen.png` | current LCD as a 128×64 PNG |
| `GET /status` | `{booted, seq, ran_ms, fault, image}` — `seq` bumps on any screen change |
| `GET /key?row=0x26&bit=0&down=1` | one matrix key edge |
| `GET /keys` | the jump-table handlers (the `press()` fallback) |
| `GET /press?idx=28&edge=0` | call a jump-table handler directly |
| `GET /leds` | parsed LED bitmap + per-id values |
| `GET /run?ms=1000` | advance emulated time (the sequencer runs here) |

## Limits

Everything route A cannot see is still invisible here — audio (use the DSP
harness for that), cross-core timing, the recorder arm path. And a project
must be one **saved on a real unit** (`--project`) for the sequencer to
promote its tracks; the empty default card boots to SET DATE/TIME.
