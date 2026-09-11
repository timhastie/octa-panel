# The virtual front panel

A clickable Octatrack, driven by the real firmware under the route-A
emulator (`tools/emu/emu_rtos.py`). The screen is what the firmware sends
its LCD; the keys, knobs and LEDs go over the panel's own wire. It is a way
to *operate* a built image before flashing — walk menus, open pages, turn
knobs, start the sequencer — not just boot it.

**As an app (macOS):** `bash tools/panel/app/build.sh` once, then open
`out/Virtual Panel.app` — a native window that starts the server itself,
loads the default project (`out/_projects/otlive/…`) and offers
File ▸ Open Project… (`tools/panel/app/README.md`).

**In a browser:**

```sh
.venv/bin/python3 tools/panel/panel_server.py                 # stock OS image
.venv/bin/python3 tools/panel/panel_server.py --image out/mainos_bus.bin   # a built remix
.venv/bin/python3 tools/panel/panel_server.py --project ~/octa/backups/<snap>/<project>
```

Open <http://localhost:8563/>. Boot takes ~5 s; a project load ~1½ min
(the page says so). The SET DATE/TIME dialog the firmware opens on every
boot is closed for you with YES (which stores the clock in RAM).

Almost every control is wired (`tools/panel/key_map.json`, 47 keys, 7
encoders and 40 LEDs measured — `KEYMAP.md` has the evidence): keys press,
encoders turn with the mouse wheel or a vertical drag, LEDs follow the
firmware. Unwired: REC AB/CD and SCALE SETUP are inferred from their FUNC
layers; the crossfader is decorative.

## What is real, and what it took to find

**The screen** is decoded from what the firmware sends the panel processor
over UART@`0xfc064000` (`tools/panel/panel_link.py`, protocol in
`PANEL_LINK.md`): `0x10`–`0x17 <column> <8 bytes>` LCD blocks (page = opcode
& 7, page 7 on top, bit 0 the top pixel of a band), `0x2r <mask>` LED bitmap
rows, `0x3n <id>` LED levels, a few one-byte commands. Rendering the RAM
framebuffer at `0x460d1f80` instead comes out with lines rotated (the
firmware's page order on screen is not fixed), which is what the first
version did — kept only as a fallback.

**The keys** are matrix reports on the same UART: `<row> <column-bitmask>`,
a set bit held, a cleared bit released — rows `0x20`/`0x21` the trig keys,
`0x22` the track keys, `0x23`–`0x26` everything else. The server keeps
per-row state so held chords (FUNC+…) work. **Encoders** are rows
`0x30`–`0x36` with a signed detent delta as the second byte.

**The LEDs** are the `0x2r <mask>` rows: `key_map.json` names each LED as
`[row, bit]`; `/leds` returns the 17 row bytes.

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

## No unit to hand? Real projects from public test fixtures

Two open-source Octatrack tools ship projects saved on real units (OS
1.40B, VERSION=19) as test data — GPL-licensed, fine to use locally:

| source | what | where it lands |
|---|---|---|
| [ot-tools](https://gitlab.com/ot-tools/ot-tools) `ot-tools-operations/test-data/copy-redo-live` | a full project (16 banks, `.work` + `.strd`, arrangements, markers) **with its AUDIO pool** (4 samples in slots) | `out/_projects/otlive/OTLIVE/{PROJECT,AUDIO}` |
| ot-tools `ot-tools-io/test-data/blank-project` | a blank project, all files | `out/_projects/blank/BLANK` |
| [octatrack-manager](https://github.com/davidferlay/octatrack-manager) `src-tauri/tests/fixtures/real_device` | one bank + project/markers/arr01 | `out/_projects/real_device` |

```sh
.venv/bin/python3 tools/panel/panel_server.py --project out/_projects/otlive/OTLIVE/PROJECT --set OTLIVE --name PROJECT
```

The full project loads under route A (M6b gate passes: mount, LOAD
PROJECT, bank A parsed) and the panel stages its AUDIO pool automatically.

## Limits

Everything route A cannot see is still invisible here — audio (use the DSP
harness for that), cross-core timing, the recorder arm path. And a project
must be one **saved on a real unit** (`--project`) for the sequencer to
promote its tracks; the empty default card boots to SET DATE/TIME.
