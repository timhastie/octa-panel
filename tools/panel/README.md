# The virtual front panel

A clickable Octatrack, driven by the real firmware under an emulator — the
C++ ColdFire port (`out/emu/ot_emu --interactive`, the default since 12 Sep
2026) or the route-A oracle (`tools/emu/emu_rtos.py`). The screen is what
the firmware sends its LCD; the keys, knobs and LEDs go over the panel's own
wire. It is a way to *operate* a built image before flashing — walk menus,
open pages, turn knobs, start the sequencer — not just boot it.

**As an app (macOS):** `bash tools/panel/app/build.sh` once, then open
`out/Virtual Panel.app` — a native window that starts the server itself,
loads the default project (`out/_projects/otlive/…`) and offers
File ▸ Open Project… (`tools/panel/app/README.md`).

**In a browser:**

```sh
.venv/bin/python3 tools/panel/panel_server.py                 # stock OS image
.venv/bin/python3 tools/panel/panel_server.py --image out/mainos_bus.bin   # a built remix
.venv/bin/python3 tools/panel/panel_server.py --project ~/octa/backups/<snap>/<project>
.venv/bin/python3 tools/panel/panel_server.py --project <dir> --audio ~/samples   # more WAV/AIFF on the card
```

Open <http://localhost:8563/>. Under the port, boot is ~3 s and boot + the
fixture project ~21 s wall (measured 12 Sep 2026); under route A ~5 s and
~1½ min (the page says so). The SET DATE/TIME dialog the firmware opens on
every boot is closed for you with YES (which stores the clock in RAM and
leaves a `DATE/TIME STORED` box on the track screen until the first key,
on either backend).

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

## Two backends, one panel

`--backend port|routea|auto` (default `auto`: the port when `out/emu/ot_emu`
exists and carries `--interactive`, built here when missing; otherwise
route A, with the reason in `/status` `backend_note`). The same `Panel`
code drives both — the port is wrapped in `PortRt`, an object with the
handful of things the panel uses of `emu_rtos.Rtos` (`run(ms=)`,
`uart64.rx/tx`, `uc.mem_read/mem_write`, `sample`, `frame`,
`pattern_base()`, `poke_trig()`).

**The port** (`tools/emu/ot_emu`, `docs/firmware/COLDFIRE_PORT.md`) runs as
a child process speaking a line protocol over pipes (the `PortProc`
docstring in `panel_server.py` is the contract; the port's side was built
against the same text): `run <ms>`, `key <row> <mask>`, `knob <row>
<delta>`, `tx`, `peek`/`poke`, `frame on|off`, `status`, `quit`, one line
back per command. The child boots and loads the project itself
(`--card --mount --set --project`, the card image being `stage_project`'s
own bytes written to `out/_panel_card_<port>.img`); the server then sends
the same YES and **the child paces itself** (`pace on`, O15f in
`COLDFIRE_PORT.md`, 12 Sep 2026): while its stdin is empty it advances
emulated time in 10 ms slices so that it tracks its own wall clock —
sleeping inside `poll()` on stdin when ahead (a command wakes it at
once), flat out when the core is slower than real time, re-anchoring past
250 ms of lag — and the server's emu thread only serves the clicks and,
every 20 ms, drains `tx` and audio, reads `pacestatus` and renders. So
the unit runs at **1.00x real time** whenever the core can (idle always,
and playing without the DSP cores on the M5: 999.7 emulated ms per wall
s over 30 s of play, `rt` 0.998–1.003), and at the core's own rate when
it cannot (playing with the cores: ~0.15x). Measured 12 Sep 2026 on the
OTLIVE fixture: MIXER, T3, knob A (PTCH on the PLAYBACK page) and PLAY
all work through the matrix; a page click reaches the firmware within
one slice (`/key` round trip 4 ms idle); STOP takes the frame clock off.
Before O15f the server pumped `run 25` + a 30 ms sleep (0.69x idle
without the cores, 1.02x in bursts of 0.85–2.0x with them) and slowed
that pump for 0.5 s after a track key so a page double-click could land
(`SLOW_PUMP_MS`, gone: the double-tap window is a wall-time property
now, see "Loading samples"). Route A keeps the pump.
A child that answers nothing for 20 s (`ACTION_LIMIT`) is killed by the
watchdog and respawned — boot, load and YES again, `restarts` counts it in
`/status` — as is one that exits. `--port-bin` names another binary (a
`.py` stand-in runs under the server's Python; `out/_agents/server/
fake_ot_emu.py` speaks the protocol from a route-A capture); `--port-arg`
passes extra flags to the child (`--port-arg=--dsp`). No jump-table path
over the pipe: `/press` answers with a note, `/transport` taps the matrix
keys instead. The port models the RTC on its DSPI too (`--rtc host|off|<epoch>`,
host time under `--interactive`), so the dialog reads the real date; YES
closes it just the same.

**Route A** is unchanged: `--backend routea`, `install_rtc`, the
`load_project_live` preamble, `press_key_live` for `/press` and
`/transport`. Each server port stages into its own
`out/_panel_stage_<port>` (two servers started together raced on the
shared tree).

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
| `GET /screen.txt` | the same frame as 64 lines of 128 `#` (dark) / `.` — for agents that grep |
| `GET /status` | `{booted, seq, ran_ms, fault, image, phase, backend, backend_note, speed, rt, pace, restarts, card_busy}` — `seq` bumps on any screen change; **`rt`** is x real time by the wall clock (emulated ms per wall s over the last second / 1000: 1.0 = the unit's own clock; null under route A and before the pacer is up); `speed` is the older meter, emulated ms per wall second *inside the emulation* over the last 5 s — idle slices are instant, so it reads high (thousands) idle and only approaches `rt × 1000` while playing; kept for scripts; `pace` is the child's last `pacestatus` (`on, rate, ratio, lag_ms, slices, reanchors, slept_s, busy_s, stop`); `card_busy` while a `/samples/commit` reboots (`booted` is false through any reboot, respawn or re-insert) |
| `GET /key?row=0x26&bit=0&down=1` | one matrix key edge |
| `GET /tap?row=0x22&bit=0&n=2&hold=50&gap=150` | `n` presses of one key inside one action (the double-tap chords, see "Loading samples") |
| `GET /knob?row=0x30&delta=2` | one encoder report (rows 0x30–0x36, signed delta) |
| `GET /samples` | the sample pool: `{pool, staged, files: [{name, bytes, format}], pending, removed, busy, phase}` |
| `GET /samples/add?path=<abs>` | copy a Mac file into the pool (converted when needed): `{ok, name, converted, note, format, bytes, pending}` (+ `cmd`, afconvert's argv, when converted) or `{ok: false, error}` |
| `POST /samples/upload?name=<n>` | the same with the raw file bytes as the body (one file per request, no multipart) |
| `GET /samples/remove?name=<n>` | take a file out of the pool |
| `GET /samples/commit` | re-insert the card: `{ok, phase}`, then `/status phase` shows the reboot |
| `GET /keys` | the jump-table handlers (the `press()` fallback) |
| `GET /press?idx=28&edge=0` | call a jump-table handler directly (route A only) |
| `GET /transport?k=play\|rec\|stop` | PLAY/REC/STOP: the handlers under route A, matrix taps under the port |
| `GET /leds` | parsed LED bitmap + per-id values |
| `GET /run?ms=1000` | advance emulated time (the sequencer runs here) |
| `GET /peek?addr=0x460d175c&len=4` | read memory (either backend), hex |
| `GET /port` | the port child: pid, argv, its own `status` line, report tail, `restarts` |
| `GET /project`, `/map`, `/stack`, `/poke_trig?step=1` | the load report, `key_map.json`, thread stacks, a trig on track 1 |

## Loading samples

On the unit, samples live on the CF card in the set's AUDIO folder: copy
files there over USB, then load them into FLEX/STATIC slots with the
firmware's own file browser. The panel does the same in two steps, because
the card is a FAT image built at boot and the emulator has no hot-plug:

1. **Into the pool.** `/samples/add?path=<abs>` (a file on the Mac) or
   `POST /samples/upload?name=<n>` (the bytes) put a file in
   `out/_panel_pool_<port>/`, the AUDIO folder the card is built from. The
   unit reads WAV and AIFF, 16 or 24 bit, 44.1 kHz, mono or stereo (a
   WAVE_FORMAT_EXTENSIBLE header with a PCM SubFormat included — it loaded
   one, footer `44.1k 16b 2Ch`, 12 Sep 2026 — listed as `… WAV
   (extensible)`); anything else (mp3, aac, flac, 48 kHz, 32-bit float,
   AIFC, 8-bit, more than two channels …) is converted with `afconvert -f
   WAVE -d LEI16@44100 in out` (channels kept, `-c 2` above two;
   `converted: true`, the command in `note` with the file as you named it
   and the pool file it became, afconvert's argv exactly as run in `cmd`; a
   failure answers `ok: false` with afconvert's stderr). Names keep their long form (the
   card is VFAT), the extension is normalised (`.wav`, `.aif`/`.aiff`) and
   characters outside `[A-Za-z0-9._ -]` become `_`; a name already in the
   pool is refused unless the bytes are identical. `/samples` lists the
   pool with each file's header (`16-bit 44.1 kHz stereo WAV`) and what is
   `pending` (not on the card yet) or `removed`. The pool is per server
   port and re-seeded at start from the project's sibling `AUDIO/` and
   every `--audio <dir>`; a running server's additions never touch the
   fixture folder.
2. **Re-insert the card.** `/samples/commit` writes the whole pool into the
   staging tree, rebuilds the image and reboots the unit on it, exactly
   the watchdog's respawn: `/status phase` reads `re-inserting the card
   (reboot, ~40 s)`, then the clock dialog closes, then `ready` (41 s on
   the OTLIVE fixture, 12 Sep 2026). Adds, removes and a second commit
   answer `ok: false` meanwhile. Same on route A (a fresh attach).

Then load a slot through the matrix, measured 12 Sep 2026 on the port
with the OTLIVE fixture (`out/_agents/samples/`: `scan.py`, `drive.py`,
shots `r*` `s*` `t*` `u*`; re-run after the fixes in
`out/_agents/samples-fix/`, shots `f*` — as PNG and `/screen.txt`):

| step | keys | screen |
|---|---|---|
| open the slot list of a track | the **track key twice** — a double-click on T1–T8 in the page, or `/tap?row=0x22&bit=<t-1>&n=2` (one action, 200 ms press to press) from a script. Two `/key` taps land by wall time now that the child is paced (O15f): 0.15 s of wall apart = 150–154 emulated ms press to press, the list opens; 0.30 s = 290–304 ms, it does not (12 Sep 2026, with and without the DSP cores) — the unit's own window, between 191 and 242 emulated ms. Before that the server slowed its idle pump for 0.5 s after a track key (`SLOW_PUMP_MS`, gone) so 0.45 s of wall reached the firmware as 191 ms | `<< MACHINE:STATIC` (or `FLEX`, the track's machine — the OTLIVE fixture boots with T5 current, T1–T4 STATIC, T5 FLEX), `SLOT / BPM / SIZE`, the track's slot highlighted |
| pick a slot | DOWN `0x24.0` / UP `0x26.3` | |
| open the file browser on it | RIGHT `0x24.1` (YES `0x26.1` does the same) | `LOAD FILE TO STATIC 5`, folder `OTLIVE>AUDIO`, the files in name order, the cursor where it was last; the footer reads the highlighted file's header (`44.1k 16b 2Ch`) |
| find the file | DOWN / UP (no wrap; the list is in name order, `extra-1` … `extra-32` numerically, and starts at the top after a boot: 35 taps from `extra-1.wav` to the first file after `fourth-0.wav`) | `clap.aiff`, `sine48k24.wav`, `Upload Sine 48k.wav` were all listed with their 0.33 MB; an unconverted extensible WAV reads `44.1k 16b 2Ch` (16-bit) / `44.1k 24b 2Ch` (24-bit) in the footer |
| load it | YES | back in the list: `5>Upload Sine 48  120  0.33`, `6>clap.aiff  120  0.33`; after the fixes `5>fxext16.wav  0.08`, `6>fxext24  0.12`, `7>Upload Fx 48k.  120  0.33` |
| the other list | LEFT `0x26.4` on the `<<` opens `SELECT MACHINE TYPE` (STATIC/FLEX/THRU/NEIGHBOR/PICKUP): the list follows the track's machine | |
| leave | NO `0x26.2` once — a second NO on the main screen draws `DISARM ALL` (KEYMAP.md) | |

How the opener was found: a PC watch on the list's window store
(`0x4007920c`, in the handler at `0x40077b00` that draws `« MACHINE:%s`)
under a pumpless `ot_emu --interactive` on the empty card, then every
key alone, held 1.2 s, twice, and under 20 held modifiers — only the
double tap of T1–T8 hit. KEYMAP.md's "UP held + track key" (E3) was two
T2 taps that happened to fall inside the window.

## Hearing the unit

With the port backend the server starts the child with `--dsp-rt` by
default (`--sound on`; `--sound off` boots without the cores, and so does
`--backend routea`, which has no sound at all): the two DSP56303 cores
render under the vendored JIT on two worker threads driven by the
ColdFire's own schedule (O17 in `COLDFIRE_PORT.md`), and core 0's
**main L/R** — the words the ESAI puts out to the DAC, 16-bit (the 24-bit
word's top two bytes), 44100 Hz — comes over the `--interactive` pipe
(`audio start main` / `audio read`, O14k). Cue and core 1 are not
captured. An rt child that cannot start (a binary without `--dsp-rt`, a
host whose DSP memory is not MMU-backed, a boot that faults before
`ready`) is respawned with the lockstep `--dsp` and `/status sound_note`
says so; `--port-arg=--dsp` asks for the lockstep cores explicitly;
`/status sound_rt` says which the current child runs, `/rtstatus` is the
child's own line (MIPS per core, waits, edges, faults).

**Playback note (13 Sep 2026, O17b).** With `--dsp-rt` the unit plays
at real time on the M5: `bench.py --dsp --dsp-rt` 1042–1058 emulated ms
per wall s flat out with LTO (O17: 664; 1357 without the cores, 208 with
the lockstep `--dsp`), and under the panel's pacer a 3-minute PLAY held
`/status rt` median 0.981 (0.905–1.080; 986 emulated ms per wall s with
the test's own `/leds` and audio polling on the same machine, 1000 and
`rt` 0.999 over a 10 s PLAY without it) with 44,100 audio frames per
emulated second captured, none dropped, and the trig-row running light
sweeping every 1.94–2.03 s of wall (125 ms per 16th at the fixture's
120 BPM, nominal 2.00 s). What made it: the cores run up to a whole
frame ahead of the ColdFire's clock, core 0's bank word is fenced on the
frame handler's own end-of-exchange mark (the unmask of its interrupt
source), the frame interrupt is delivered at the sample the DSP's clock
says, and the host port's rings and DMA move blocks in bursts
(`COLDFIRE_PORT.md` O17b). The sound is the same content as the lockstep
capture — onset within 2 samples, the same loops at the same time — but
1.3 dB quieter in RMS on the clipping OTLIVE fixture (fewer full-scale
samples; the DSP's output limiter runs on its own history under a
different core interleave), not byte-identical. The lockstep fallback is
~0.2x.

**Priority matters more than you would think** (O15f, 12 Sep 2026): a
server started as a zsh background job (`… &`) runs at nice 5 (`BG_NICE`
is on by default) — harmless on an idle machine, slower whenever anything
else wants the CPU; `/status nice` reports it and the server warns at
start. The darwin *background* class (`taskpolicy -b`, what a
background-QoS or napped app hands its children: efficiency cores) is
3.5–3.7x slower; the server spawns the child with a `preexec_fn` that
leaves that class (`setpriority(PRIO_DARWIN_PROCESS, 0, 0)`), measured
back at full speed under `taskpolicy -b`. What the cores cost
(13 Sep 2026): with `--dsp-rt` boot + fixture load ~7 s (the JIT
compiles the payloads' blocks as they run; ~15 s with the lockstep
`--dsp`, ~6 without the cores), and while the sequencer plays the unit
runs at 1.0x real time (`/status rt`; the lockstep child at ~0.2x); idle
it is paced to 1.00x like everything else. The two DSP worker threads
spin while host traffic is flowing (a command, a push or a pull within
the last 2 ms) and park 200 µs after it stops, so at 1.00x the child is
~260 % of a core while the sequencer plays (O17b's panel run) and ~70 %
idle.

The server drains the child's ring after every pump (25 ms of firmware)
and every action into a ring of its own — the last **180 s**, addressed
by absolute frame number since the capture began (the count keeps rising
across respawns, re-inserts and sound switches; the child's own ring
restarts each time) — and, while frame mode is on, into a **take**:
**PLAY opens `out/_panel_takes_<port>/take-NNN.wav`, STOP closes it.**
That is the hardware feeling: press PLAY, the unit plays, press STOP, you
have what it played. The file is 16-bit stereo 44.1 kHz with its RIFF
sizes re-patched after every append, so it is a valid WAV at every
moment (a reader mid-take gets what is there so far); a take open at a
reboot or at exit is closed as it stands. Takes are numbered on from what
the folder already holds and never wiped.

| route | does |
|---|---|
| `GET /status` | adds `sound` (the child runs `--dsp` and its capture is on) and `sound_note` (why not, when not) |
| `GET /audio/status` | `{sound, on, rate: 44100, captured, end, first, cap, dropped, peak: [l, r], take, takes, note}` — `end` = frames ever captured, `first` = the oldest still in the ring, `cap` = 7 938 000, `dropped` = frames the child overwrote unread, `peak` of the last non-empty read, `take` = `{n, recording, frames, seconds, file, start}` while one is open (else null), `takes` = every take as `{n, file, frames, seconds}`; plus `busy`/`phase` (a reboot in progress), `takes_dir`, and `drain` (what the drain itself costs the pump: reads, wall_ms, max_ms) |
| `GET /audio/pcm?from=<frame>&max=<frames>` | raw LE int16 stereo frames from `max(from, first)`, at most `max` (default 88 200, cap 441 000), `application/octet-stream` with `X-Audio-From` (where the body really starts), `X-Audio-Frames`, `X-Audio-End`, `X-Audio-Rate`; **204** with `X-Audio-End` when `from >= end`. Served from the ring on the HTTP thread: 1–2 ms for 2 s of audio |
| `GET /audio.wav?take=N` | that take as `audio/wav`, `Content-Disposition: attachment; filename="octatrack-take-NNN.wav"`; a missing take is a 404 JSON |
| `GET /audio.wav?from=&to=` | ring frames as `octatrack-main-out.wav` (default: everything held); an empty range is a 404 JSON |
| `GET /audio/enable?on=1\|0` | reboot the child with/without `--dsp`, the card re-insert's own mechanics: `/status phase` reads `switching sound on (reboot, ~1 min)` / `switching sound off (reboot, ~40 s)`, `booted` false meanwhile, the clock dialog closed after; `ok: false` + `note` while a re-insert or switch runs, while booting, when already in that state, or under route A. Takes and the ring survive it |

Measured 12 Sep 2026 on the OTLIVE fixture (`out/_agents/monitor-server/`,
`verify.py`, logs beside it): the drain costs the idle pump ~0.3 ms per
25 ms of firmware once the pipe reader was buffered (it was 2.2 ms with
`readline()` on the raw pipe, a syscall per byte); playing, `end` grows
at 44 100 per emulated second (`/status ran_ms` deltas); the take opens
with 0 frames at the PLAY key and the first sound is at frame 81 (1.8 ms
after it, the pattern's saved trig on step 1); a take of 2.65 emulated s
took 25.8 s of wall.

**The honest playback note.** The pacer (O15f) holds the unit at 1.00x
only when the core keeps up; with the DSP cores it plays at ~0.15x real
time (the toolbar badge beside the phase shows the live figure, green at
>= 0.97x, yellow below), so anyone *listening* live is behind, and in
bursts — the page's MONITOR plays what has arrived and waits when it
runs dry; a replayed take plays at real time. Two more things the fixture taught: `/audio/status peak` at
32767 means the DSP itself is clipping (the fixture's slots 1 and 2 loop
`first-0`/`second-0` at GAIN 75/72 from step 1 — the spike's `tree2`
copy, `out/_agents/audio/tree2`, has them at 48 with looping off, which
is where the `third-0.wav x 0.70` figure comes from); and the VOLUME pot
on the panel is the monitor's gain only, as the hardware pot sits after
the DAC.

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

The full project loads under either backend (the port's own boot does the
mount and LOAD PROJECT, `/project` shows its report: `posted`, `saved_bank`
0, `final_bank` 0; under route A the M6b gate passes: mount, LOAD PROJECT,
bank A parsed) and the panel stages its AUDIO pool automatically.

## Limits

Everything the emulator cannot see is still invisible here — audio (the
port's `--dsp` cores are not on by default: `--port-arg=--dsp`; use the DSP
harness for listening), cross-core timing, the recorder arm path. And a
project must be one **saved on a real unit** (`--project`) for the
sequencer to promote its tracks; the empty default card boots to SET
DATE/TIME.
