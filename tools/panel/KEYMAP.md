# The front-panel map: evidence

What `tools/panel/key_map.json` says and how each entry was measured, so a
verifier can replay it. Everything below is from 11 Sep 2026 runs of the
stock image `out/raw/section_3_MAIN_OS.bin` under route A
(`tools/emu/emu_rtos.py`), keys injected on the panel UART
(`rt.uart64.rx`), screen and LEDs read back from the firmware's own panel
stream (`rt.uart64.tx`) through `tools/panel/panel_link.PanelLink`. The RAM
framebuffer at `0x460d1f80` was only used as a "something changed" hash.

Scratch (gitignored): `out/_agents/keymap/` -- `lab2.py` (harness),
`e1.py`..`e5.py` (the runs), `e1.log`..`e5.log` (every step, one line each:
screen changed?, LCD blocks sent, LED bitmap rows old->new, LED brightness
ids old->new, named RAM diffs, window byte diffs) and `E1_*.png`..`E5_*.png`
(the decoded LCD after the steps that changed it, 3x); `srv_check.py` /
`srv_check.log` (the `/leds` check against a panel_server of its own, port
8577). Replay:

    cd ~/Downloads/octabam && export PATH=/opt/homebrew/bin:$PATH
    .venv/bin/python3 out/_agents/keymap/e1.py     # empty card, ~3 min wall
    .venv/bin/python3 out/_agents/keymap/e3.py     # empty card, arrows + card mount
    .venv/bin/python3 out/_agents/keymap/e4.py     # empty card, modifier probe
    .venv/bin/python3 out/_agents/keymap/e5.py     # empty card, YES/NO alone vs FUNC, YES in menus, 17 s
    .venv/bin/python3 out/_agents/keymap/e6.py     # empty card, NO straight on the dialog, 15 s
    .venv/bin/python3 out/_agents/keymap/e2.py     # OTLIVE/PROJECT loaded, ~3.5 min wall
    .venv/bin/python3 out/_agents/keymap/srv_check.py   # own server on 8577, /map and /leds, ~10 s

A tap is `<row> <1<<bit>` then 60 ms, `<row> 0` then 100 ms (`Lab.tap`);
holds and chords keep per-row state exactly as `panel_server.key` does.
Boot ~15 s wall; the project load ~90 s.

## Three things that were not known before

- **SET DATE/TIME can be dismissed.** YES (0x26.1) stores the clock (writes
  the 7-byte record at `0x80000080`, shows `DATE/TIME STORED`, 91 LCD
  blocks); NO (0x26.2) straight on the dialog closes it without writing the
  record (90 blocks, `0x80000080` stays zero, E6 `dlg_no`,
  `E6_dlg_no.png`). Every run below works on the real main screen after one
  YES. `E1_yes.png`. (E3's `dlg_no`, 34 blocks, came after `dlg_yes` had
  closed the dialog and is the DISARM ALL popup of the next bullet.)
- **YES and NO are keys on the bare main screen: `ARM ALL` / `DISARM ALL`.**
  From a clean main screen (MIXER opened and closed, 112 blocks), YES alone
  draws the `ARM ALL` popup (28 blocks, `0x46c803d4` := ffff,
  `E5_yes_alone.png`) and NO alone draws `DISARM ALL` (35 blocks,
  `0x46c7fe22` := ffff, `E5_no_alone.png`); FUNC held changes nothing but
  the LEV box (`E5_func_yes.png`, `E5_func_no.png`, same block counts).
  The E1 run attributed these to FUNC+YES / FUNC+NO (E1 `func_yes` = the
  same 0x46c803d4 write, 25 blocks because it landed on a DISARM ALL
  popup; E1 `func_no` logged no change because the plain NO two steps
  earlier, `func_yes_no`, had already drawn DISARM ALL -- the first one in
  E1 is `func_mixer_no2`, the NO after the one that closed the menu).
  Under the emulator the popups do not time out (10 s idle,
  `E5_no_idle10000.png`); page keys redraw around them (`E5_yes_pg_amp.png`),
  a track key or a second NO leaves them (E5 `no_t2`, `no_no`: 0 blocks);
  only a full redraw clears them -- MIXER open+close, or any window opened
  and closed. So the NO that closes the last window is one NO too many, and
  every `*_no2: blk 34/35` step in E1 (6 of them) and E4's `main` baseline
  (`E4_main.png`, reached by YES then NO on the dialog) are that popup. It
  sits over the parameter boxes only; no key/LED/RAM measurement depends on
  it.
- **The panel's key rows are 0x20-0x26 only** (13 Sep 2026: plus row 0x27 =
  the encoders' PUSH switches, the last section of this file). Rows 0x27-0x2f were tapped
  on the dialog, the main screen and inside the PROJECT menu (E1 `sw_*`,
  E3 `menu_c27_*`, E4 `27_*`): no RAM, LED or menu-cursor change ever; the
  only effect of 0x27.x is a 2-4 block redraw of the tempo readout (and,
  once, a deferred redraw of the PROJECT menu). Not keys.

## Keys (row.bit -> id), with the measurement

Row 0x20/0x21/0x22 as given (trig 1-8, trig 9-16, T1-T8): re-measured --
T2..T8 taps move `0x80000000` / `0x100b14cc` to 1..7 and LED row 5/6
(`leds.out`, E1 `midi_t3`, E4 `*_T2`); trig taps on the MIXER toggle the
mute masks `0x8000000a` (audio) / `0x8000000e` (MIDI) one bit per key
(`leds.out`, E1 `mixer_trig1/9`).

| cell | id | evidence (log step -> PNG) |
|---|---|---|
| 0x23.0 | **tempo** | opens the `TEMPO 120.0` window; encoder 0x36 in it edits the BPM (`E1_tempo.png`, `E1_tempo_enc36+2.png`); FUNC+it = `TAP TEMPO` (`E1_func_tempo.png`) |
| 0x23.1 | **scene_a** | held: trig LEDs show the scene slots (row 0 bit 0 + row 2 bit 1 = scene A on 1, B on 9, the defaults); + trig 3 writes the Part's scene-A byte `0x40170f70` 0->2 and the next hold lights trig 3 (E1 `sceneA_*`); FUNC+it clears LED row 4 bit 0 and sets `0x80000006` (scene mute) |
| 0x23.2 | **scene_b** | same with the colours swapped; + trig 11 writes `0x40170f71` 8->10 (E1 `sceneB_*`); FUNC+it: row 4 bit 2, `0x80000007` |
| 0x23.3 | **scale** (inferred) | alone: nothing on the dialog, main screen, PROJECT menu, or while playing (E1/E2/E3/E4). FUNC+it opens `PATTERN SCALE 16/16 1x` with all trig LEDs lit (`E1_func_23_3.png`, `E2_func_x23_3.png`, `E4_23_3_func.png`). Named by that FUNC layer only |
| 0x23.4-7 | -- | nothing alone, with FUNC, held with T2, or under T2 (E4) |
| 0x24.0 | **down** | PROJECT menu: `MENU_SELROW` 0x400cbd98 0->1->2 (E3 `menu_d24_0*`); dialog: decrements the field under the cursor (day 12->11, month 09->08, `E3_dlg_c24_0*.png`); FUNC+it opens the TRIG MODE list and moves down it (`E3_tm_d24_0*.png`) |
| 0x24.1 | **right** | dialog cursor year->month->day (`E3_dlg_right*.png`); held on the main screen it blanks the BPM digits (tempo nudge, 6 blocks, E1 `main_24_1_*`) |
| 0x24.2-6 | **pg_playback, pg_amp, pg_lfo, pg_fx1, pg_fx2** | footer reads `PLAYBACK>STATIC`, `AMP`, `LFO`, `FX1>FILTER`, `FX2>DELAY` (`E1_pg_24_*.png`); page kind `0x460d1684` = 0, 2, 1, 3, 4; FUNC+0x24.2/3 open the PLAYBACK / AMP setup menus |
| 0x24.7 | **stop** | transport `0x800065b8` 1->0 while playing (E2 `stop`); FUNC+STOP does nothing visible on a fresh project (paste, nothing copied) |
| 0x25.0 | **play** | transport 0->1, LED row 11 01->08 (E2 `play_down`); again while playing: transport 2 (pause), row 11 09; FUNC+it = `CLEAR PATTERN` (`E1_func_play.png`) |
| 0x25.1 | **rec** | grid recording toggle: LED row 10 bit 4 on/off, the poked trigs (1, 5, 9, 13) appear on the trig LEDs, + trig 3 places one (E2 `rec*`); FUNC+it = `COPY PATTERN` (`E1_func_rec.png`) |
| 0x25.2 | **cue** | held: `0x460d168f` := 1 and a 2-block indicator; + T1 sets the cue mask `0x80000009` bit 0 and LED row 5 a9->a8 (E1 `cue_*`) |
| 0x25.3 | **recab** (inferred) | held, it swallows a T2 press (no track change, E4 `25_3_T2`); FUNC+it = `RECORDING 1 SETUP 1` (`E4_25_3_func.png`) |
| 0x25.4 | **reccd** (inferred) | same swallow (E4 `25_4_T2`); FUNC+it = `RECORDING 1 SETUP 2` (`E4_25_4_func.png`) |
| 0x25.5 | **func** | held: the LEV box reads MAIN (`E1_func_mixer_funcdown.png`); FUNC+MIXER = PROJECT menu (`E1_func_mixer.png`), FUNC+MIDI = PART chooser (`E1_func_midi.png`), FUNC+T1 = mute track 1 (`0x8000000a`), FUNC+BANK = `PATTERN A01 SETTINGS`, FUNC+PATTERN = arranger on (`0x80000010` := 1, LED row 8 bit 0, `E1_func_pattern.png`). FUNC+YES / FUNC+NO are NOT a layer: they draw the same `ARM ALL` / `DISARM ALL` popups YES / NO draw alone (E5 `func_yes` 28 blk, `func_no` 35 blk vs `yes_alone` 28, `no_alone` 35) |
| 0x25.6 | **pattern** | held: `SELECT PATTERN` window, current pattern on the trig LEDs; + trig 3: `0x80000004` and `0x800065be` 0->2 (E2 `pattern_*`) |
| 0x25.7 | **bank** | held: `SELECT BANK` window, 16 banks on the trig LEDs (a9 aa aa aa with OTLIVE) (E2 `bank_*`, `E2_bank_down.png`) |
| 0x26.0 | **mixer** | MIXER page, mute LEDs 55 55 ff ff, LED row 12 bit 6 (`E1_mixer.png`) |
| 0x26.1 | **yes** | dismisses SET DATE/TIME storing the clock (E1 `yes`, E5 `dlg_yes`: 7-byte record at `0x80000080`, 91 blk); bare main screen: `ARM ALL` popup (E5 `yes_alone`, `E5_yes_alone.png`); PROJECT menu: the first YES moves the focus descriptor `MENU_FOCUS` 0x400cbda8 from the root `0x400cbd8c` to `0x400cbcac` (into the PROJECT list, 0 blocks -- the menu does not redraw here), DOWN then YES activates SAVE: `SET / NO SET IS MOUNTED! PLEASE MOUNT ONE [OK]` over `CHOOSE A SET` (65 blk, `E5_menu_yes_yes.png`; the three NOs after it uncover `CHOOSE A SET`, the list with SAVE highlighted, the main screen: `E5_menu_no0..2.png`); TEMPO window: closes it, BPM kept (`E5_tempo_yes.png`, 60 blk); PATTERN SETTINGS checkbox rows: nothing (E5 `ps_yes`, `ps_yes2`) |
| 0x26.2 | **no** | closes windows/menus one level at a time (every `*_no` step: TEMPO, PROJECT menu and its alerts, PART chooser, PATTERN SETTINGS, PATTERN SCALE); on the BARE main screen it draws the `DISARM ALL` popup instead (35 blk, `0x46c7fe22` := ffff, `E5_no_alone.png`), which stays up until a full redraw (see above); a second NO on it: 0 blocks (E5 `no_no`) |
| 0x26.3 | **up** | PROJECT menu `MENU_SELROW` 2->1 (E3 `menu_u26_3`); dialog: increments the field (day 11->12, `E3_dlg_c26_3.png`); FUNC+it moves up the TRIG MODE list; held + track key opens the sample slot list `<< MACHINE:STATIC` (`E3_h26_3_t2.png`) |
| 0x26.4 | **left** | dialog: cursor day->month (then DOWN edits the month, `E3_dlg_c24_0b.png`); held on the main screen blanks the BPM digits like RIGHT (E1 `main_26_4_*`) |
| 0x26.5 | **midi** | MIDI mode `0x80000015` 0/1, LED row 8 04->cc (`E1_midi.png`); in MIDI mode the page LEDs use their second bit (AMP: row 8 f0) |
| 0x26.6-7 | -- | nothing in any combination (E1, E3, E4) |

## Encoders (row -> knob)

Reports are `<row> <signed delta>`; `+2` was sent, then `-2`.

| row | id | main screen, PLAYBACK page (empty card, static machine) | MIXER |
|---|---|---|---|
| 0x30 | a | slot 0 PTCH (`0x40170f8a`) | MAIN (`0x80000035`) |
| 0x31 | b | slot 1 STRT (`0x40170f8b`) | DIR AB (`0x80000031`) |
| 0x32 | c | slot 2 LEN (`0x40170f8c`) | GAIN AB (`0x8000002f`) |
| 0x33 | d | slot 3 RATE (`0x40170f8d`) | CUE (`0x80000036`) |
| 0x34 | e | slot 4 RTRG (`0x40170f8e`) | DIR CD (`0x80000030`) |
| 0x35 | f | slot 5 RTIM (`0x40170f8f`) | GAIN CD (`0x8000002e`) |
| 0x36 | level | track level `0x80000c50 + 2*track` 108->110 (LEV box) | MIX (`0x80000032`) |

E1 `enc_main_*` / `enc_mixer_*`, `E1_enc_mixer_3?+2.png` (the box that
changed). Top row of the MIXER left to right = A B C, bottom row = D E F.

## LEDs (bitmap row.bit; brightness id = row*8+bit)

`row.bit` is the LED bitmap row of the panel stream -- the 2-byte
`0x20+row <mask>` messages (`0xa0+(row-16)` from row 16), what
`panel_link.PanelLink.led_rows` holds and `led_bits()` flattens to
`row*8+bit`. It is NOT the `bits` string of `panel_server`'s `/leds`:
`_parse_leds` fills that from `0x10 <off> <8 bytes>` frames, which are LCD
page-0 blocks (PANEL_LINK.md), so `panel.html`'s `ledOn([byte, bit])`
follows the bottom band of the screen, not an LED. Measured on an own
server (port 8577, `srv_check.log`): at boot `bits[5]` bit 0 (led_t1) = 0
and `bits[8]` bit 2 (led_pg_playback) = 0 while the LED rows say 5=a9 and
8=04 (both lit); after REC through `/key?row=0x25&bit=1` `bits[10]` bit 4
(led_rec) = 0 while bytes 4-63 of `bits` all changed with the redraw; only
`ids["0x48"]` moved, 6 -> 15 -> 6. Until `/leds` carries `link.led_rows`
(17 bytes, row 0 first) and `ledOn` indexes that, the map's LEDs do not
light on the page. Not a keymap file, so not changed here.

Boot state (after `43` and the 88 brightness inits): rows 4=05 5=a9 6=aa
7=ff 8=04 9=01 11=01 16=0f, all else 0; brightness 15 for ids 0-0x37,
0x40-0x47, 0x49-0x5b; 6 for 0x48; 0 for 0x38-0x3f and 0x5c+.

| LED | row.bit | evidence |
|---|---|---|
| trig n | (n-1)//4 . 2*((n-1)%4) | MIXER: mute of trig 1/2/3/4 clears row 0 bit 0/2/4/6, trig 5 row 1 bit 0, trig 9 row 2 bits 0-1 (`leds.out`, E1 `mixer_trig*`); grid rec shows trigs 1/5/9/13 as rows 0-3 = 01 (E2 `rec`); the odd bit is the second colour (MIDI trigs ff in the MIXER, non-current banks aa) |
| track n | 5+(n-1)//4 . 2*((n-1)%4) | selecting T2 turns row 5 a9->a6 (track 1 bit0->bit1, track 2 bit3->bit2); T5..T8 move row 6 the same way (`leds.out`); the selected track holds the even bit, the others the odd bit; CUE+T1 a9->a8, FUNC+T1 (mute) a9->ab |
| play | 11.3 | row 11 01->08 on PLAY, 09 when paused, back to 01 200 ms after STOP (E2) |
| stop | 11.0 | the complement above; lit at boot |
| rec | 10.4 | row 10 00<->10 with the REC key, brightness id 0x48 6<->15 alongside (E2 `rec`, `rec_off`) |
| scene A / B | 4.0 / 4.2 | lit at boot; FUNC+SCENE A clears 4.0, FUNC+SCENE B clears 4.2 (E1 `func_scene*_funcup`) |
| tempo (inferred) | 4.6 | row 4 05->45 50 ms after the first PLAY and never off again, no blink on the wire (E2 `playing+50ms`); the only unexplained bit near the scene LEDs |
| pages | 8.2 PLAYBACK, 8.4 AMP, 10.6 LFO, 10.0 FX1, 10.2 FX2 | E1 `pg_24_*` (row 8 04->10->00, row 10 40->01->04); in MIDI mode bit+1 too (cc, f0) |
| midi | 8.6 | row 8 04->cc with the MIDI key (bits 6-7 = the two colours) |
| mixer | 12.6 | row 12 00->40 while the MIXER is open |
| arranger | 8.0 | row 8 04->05 after FUNC+PATTERN, back with the second |
| PROJECT / PART menu | 15.0 / 15.2 | with brightness ids 0x78 / 0x7a := 15 while open |

Not found: a FUNC LED (holding FUNC changes no row), the card LED (a
`request_card_mount` run, E3 `mount+*`, sends no LED traffic -- the CF LED
is presumably wired to the card slot), whatever rows 7 (ff at brightness
0) and 16 (0f) are, and why brightness id 0x48 (row 9 bit 0, lit dim at
boot) follows the REC key; row 9 bit 1 lights in CHROMATIC trig mode.

## Open ends

- 0x23.3 = SCALE and 0x25.3/0x25.4 = REC AB / REC CD rest on their FUNC
  layers (PATTERN SCALE window; RECORDING 1 SETUP 1 / 2) and on the two REC
  cells swallowing a track press (the `[TRACK]+[REC]` sampling chord); the
  emulator has no audio path, so a manual sampling never shows. A third
  recorder key (SRC3) was not found in rows 0x23-0x26.
- 0x23.3 swallowed a T2 press once (E3 `h23_3_t2`, straight after the slot
  list had been closed) and not in E4; treat that one as state left over.
- The PROJECT menu does not redraw on cursor moves or on the YES that
  enters a sub-list under the emulator (RAM moves, 0 LCD blocks); the
  dialog, the main screen and the alerts it opens do. A tap on row 0x27
  made it redraw once (`E3_menu_c27_0.png`, cursor on SYSTEM).
- REC AB/REC CD held + a trig, or FUNC + trig 1, light that trig's LED and
  nothing else visible (E1 `func_trig1`, E3 `h26_4_trig5`).
- The `ARM ALL` / `DISARM ALL` popups (YES / NO on the bare main screen)
  never clear on their own here; whether that is the emulator (a timer the
  UI task never sees) or the firmware was not checked. A panel user who
  taps NO one time too many keeps `DISARM ALL` on screen until the next
  full redraw (MIXER twice).
- `/leds` does not carry the LED rows the map indexes (see the LEDs
  section): the page's LEDs stay wrong until panel_server/panel.html are
  changed.
- The PROJECT-menu LED (row 15 bit 0) clears one LED refresh late: still
  set after the third NO closed the menu, cleared at the next MIXER open
  (E5 `menu_no2`, `menu_mixer_open`; E3 `menu_no2` the same).

## 12 Sep 2026: PLAY through the matrix; the SETUP pages' encoders

Scratch (gitignored): `out/_agents/panel-server/` -- `srv_play.py` (PLAY/STOP
through `/key` on an own server, port 8581), `lab_pages.py`..`lab_pages6.py`
(scripted, OTLIVE/PROJECT loaded, `lab2.Lab2` from the keymap scratch),
their `.log`s, `profile_frame_on.txt`, PNGs `S_*` (server), `P_*` `Q_*`
`R_*` `T_*` `U_*` `V_*` (labs, 3x). Stock image, route A, as above.

### PLAY 0x25.0 / STOP 0x24.7 through `/key`

- The matrix PLAY starts the transport (`0x800065b8` := 1, LED row 11
  01->08, tempo LED row 4 bit 6 on, the play icon and beat box 1 drawn) but
  the sequencer only steps under the DSP frame interrupt, which
  `panel_server.key()` never switched on -- only the old `/transport` path
  did. Now PLAY going down (not under FUNC) runs `activate_tracks` + frame
  mode before the key and STOP going down turns frame mode off after it;
  `/transport` shares the two helpers (and no longer calls `exact_clock`).
- Measured on the own server (`srv_play.log`, two runs): idle 850-880
  emulated ms per wall second (the pump's 30 ms sleeps); playing 54.4 /
  55.5 emulated ms per wall second (18x). The four beat boxes under the
  BPM: box 1 filled at PLAY (`S_play_001*.png`), box 2 at 3176 / 3183
  emulated ms = 57-59 s wall (`S_play_002*.png`), the play triangle
  toggles with it. Inside the firmware (`lab_pages5.log`, `watch_mem`):
  the 24-PPQN tick byte `0x800065b6` counts 0..6 and wraps every ~840
  emulated ms (ticks 142, 128, 150, 136, 150 ms apart -- six per 16th
  step), the step byte `0x800065b5` once per wrap. So one 16th step is
  ~840 emulated ms = **~15 wall seconds at 120 BPM**, a beat box ~70 s, a
  bar ~4.7 min. Nominal is 20.8 ms per tick / 125 ms per step: the
  firmware's clock runs ~6.7x slow in emulated time because only ~414 of
  the nominal 2756 frame interrupts per emulated second are delivered
  (`lab_pages.log`: 414 frames, 750,980 bursts, 173M charged instructions
  in one emulated second -- the emulated CPU is saturated and the frame
  latch remembers one edge). That is `tools/emu`, not the panel.
- Trig LEDs: rows 0-3 never change while playing, GRID RECORDING off or
  on (3 steps = 24 ticks each, `lab_pages5.log`): the LED state array
  `0x460ba9ae` is written once, at PLAY (row 4 bit 6, the tempo LED, pc
  `0x400137d4`); the blink phase `0x460ba98c` is written 0 every ~20 ms
  (nonzero in grid-rec mode, 64 of 128 writes, and the rows sent do not
  change). The running light is not computed under the emulator. Grid rec
  shows the poked trigs (rows 0-3 = 01) as before; PLAY/STOP/tempo/REC
  LEDs behave as in E2.
- Profile (`profile_frame_on.txt`, cProfile over one emulated second in
  frame mode: 35.4 s profiled, 18.1 s plain): unicorn `emu_start` 1.94M
  calls 5.2 s tottime, `Intc.pending` 2.95M calls 4.5 s, `Intc.asserted`
  3.4 s, `mem_read` 2.96M 1.8 s, `Rtos.step` 735k 1.6 s,
  `_emac_load_shim` 515k 1.5 s, `reg_read` 3.15M 1.2 s, `_isa_c_shim`
  564k. panel_server's own share per pump: `link.lcd_rows` 0.30 ms + PNG
  0.40 ms, `_parse_leds` negligible. Changed in panel_server: the PNG is
  rendered only when an LCD block arrived (`PanelLink.dirty`), and the
  pump is 10 ms instead of 25 while frame mode is on (a click waits at
  most ~0.18 s instead of ~0.46 s for the pump in progress: PLAY down
  through `/key` 1.99 s vs 2.20 s, STOP 1.48 vs 1.58 s). Nothing else in
  the file costs anything; the 18x is the emulation.

### The SETUP pages (second press of a page key)

- Page key once = page 1 (AMP: `ATK HOLD REL VOL BAL`, 47 blocks, LED
  row 8 bit 4); again = the SETUP window (`AMP SETUP` / `LFO SETUP` /
  `PLAYBACK SETUP` / FX1's, 114-120 blocks, window slot `0x460d175c` :=
  `0x46c7d34c`); again = page 1 (slot cleared, 114-120 blocks).
  `P_amp2.png`, `P_lfo2.png`, `P_pb2.png`, `P_amp3.png`. The press after
  a close is swallowed: measured through the own server (`srv_setup.log`,
  `S_setup_*.png`) AMP presses 1..7 give page 1, SETUP, page 1, nothing
  (seq unchanged, the window slot stays 0 -- the press re-selects the
  page), SETUP, page 1, nothing; lab_pages2's "ignored fourth LFO press"
  was the same. So reopening a SETUP page after closing it is the page
  key TWICE, and an encoder turned between those two presses edits page 1
  (`srv_setup.log`: B -8 landed on HOLD).
- Encoders A-F edit the SETUP boxes of the current track (T5, index 4) in
  the bank blob at `PART_PTR` = `0x400e21e0`, mirrored at `0x8000095c..`
  and SRAM `0x100a523e..`: AMP SETUP A..F -> `0x401712d0..d5` = AMP(4)
  SYNC(2) ATCK(2) FX1(4) FX2(4) TRIG(5) (PARAM_PAGES.md p6..p11; ATCK IS
  drawn, TRIG is not, F edits it anyway). LFO SETUP A -> PMTR
  `0x401712ca` (30), B -> WAVE `0x401712cd` (19), C -> MULT `0x401712e2`
  (7), D -> TRIG `0x401712e5` (8), E/F -> SPD/DEP = the page-1 bytes
  `0x401710da` / `0x401710dd`, delta applied as is. PLAYBACK SETUP A ->
  LOOP `0x401711b8`, F -> TSNS `0x401711bd`. Page 1 for comparison: AMP
  A..E -> `0x401710e0..e4` (F = XVOL changes nothing, not drawn), LFO
  SPD1..DEP3 -> `0x401710da..df`, each report applied exactly (+1, +2,
  -3) and flushed in 2-6 blocks.
- The rule (`lab_pages4.log`, PMTR held mid-range, the accumulator
  `0x46c7d246` found by a whole-RAM diff and read before/after every
  report): a SETUP box runs the report through an accelerating enum editor.
  +1 reports only accumulate -- 1, 2, then the third steps the value and
  clears it (three detents per step clockwise); -1 goes -1, -2, -3, the
  fourth steps and leaves -1 (four per step back). +2..+7 are about one
  step each (+2 alternates 0 and +1), +8/+9 two, and +12/+16/+24 come out
  backwards (15->6, 15->7, 15->10; -12/-16/-24 undo them): the enum wraps
  modulo its count. Two +1 reports in one run behave as two reports. So a
  slow wheel is 3-4 detents per value on these boxes and the page's
  coalesced deltas beyond +-9 go the wrong way. No modifier is involved:
  FUNC held, the spare cells 0x23.4-7 / 0x26.6-7 / 0x25.3-4 / 0x23.3 held,
  rows 0x27-0x2f, arrows, YES change nothing about it (`lab_pages.log`);
  no encoder-push cell exists in rows 0x23-0x2f (13 Sep 2026: row 0x27 IS the
  push -- found with a TRIG key held in GRID RECORDING, last section; tapped
  alone on a SETUP page it changed nothing, as measured here).
- The display: the edit IS drawn (the RAM buffer `0x460d1f80` changes,
  e.g. `0x460d2136..0x460d21a7` for the AMP box) but **no LCD block is
  sent** -- not within 3 s idle, not after a tap of the non-key cell
  0x27.0, a zero-delta LEVEL or A report, LEVEL +1/-1, or FUNC held 250 ms
  (`lab_pages3.log`, `lab_pages6.log`). The box shows the new value when
  the page is redrawn: leave and re-enter it (`P_amp3.png` then the next
  opening), or -- seen once each on AMP SETUP after a long run of edits
  -- LEVEL +1 (7 blocks, `P_amp2_level+1.png`: AMP TTRG, SYNC OFF, FX1/FX2
  RTRG) and FUNC held (19 blocks, `P_amp2_func_down.png`). Page 1 flushes
  every report. Whatever flushes the window on hardware does not run
  under route A (the same family as the PROJECT menu's cursor and the
  ARM/DISARM ALL timeouts above). panel_server cannot mirror the RAM
  buffer instead: it is not a copy of the LCD (no page rotation, row/
  column order or bit order maps it onto the decoded stream; best 669 of
  1024 bytes wrong on AMP SETUP, 805+ on the main screen). `/knob` now says
  in its result, while a SETUP window is the popup on screen (the geometry
  test of the next section -- NOT "`0x460d175c` set", which the first
  version used and which holds on the main screen too), that the value
  changed and the box redraws when the page is re-entered.

## 12 Sep 2026, later: the popup slot, `/run` while playing, the LED sender

Verifier follow-up on the section above. Scratch: `out/_agents/panel-server/`
`fix_lab.py` (window-slot survey + LED-driver hooks while playing),
`fix_lab2.py` (the popup record per window), `fix_lab3.py` / `fix_lab4.py`
(who sends the LED rows), `fix_srv.py` (own server, port 8582), their
`.log`s, PNGs `F_*` `G_*` `H_*` `J_*` (labs, 3x) and `Z_*` (server).

### The popup slot `0x460d175c` is not "SETUP or 0"

`fix_lab.log`, `fix_lab2.log` (fresh instance, OTLIVE/PROJECT, every state
read after a 400 ms settle):

| state | slot | popup record `0x46c7d34c`: x0 y0 x1 flags rows |
|---|---|---|
| boot, SET DATE/TIME up (before and after the load) | `0x46c7d34c` | 0f 07 e6 21 32 |
| main after YES; AMP page 1 after it | `0x46c7d384` | (record B, flags 01) |
| AMP / LFO / PLAYBACK / FX1 / FX2 second press (SETUP) | `0x46c7d34c` | **07 00 f4 21 40** (all five) |
| a SETUP window closed (third press); page 1s after that | `0x0` | 07 00 f4 **01** 40 (kept) |
| MIXER | `0x46c7d34c` | 0a 00 ec 21 40 |
| TEMPO | `0x46c7d34c` | 1c 08 ca 21 30 |
| PATTERN SETTINGS (FUNC+BANK) | `0x46c7d34c` | 08 03 f2 21 3a |
| PROJECT menu (FUNC+MIXER) | `0x46c7d34c` | 05 00 f6 21 40 |
| ARM ALL (YES on main) / DISARM ALL (NO on main) | `0x46c7d34c` | 25 17 b8 21 12 / 1e 17 c6 21 12 |
| AMP page 1 with DISARM ALL still drawn | `0x46c7d34c` | 1e 17 c6 21 12 |
| AMP SETUP opened over it | `0x46c7d34c` | 07 00 f4 21 40 |
| MIXER, TEMPO, PATTERN SETTINGS, menu closed | `0x0` | flags 01 |

So the slot names the popup RECORD (`0x46c7d34c` for every popup the panel
opens, `0x46c7d384` for the record after it, left there when YES closed
the clock dialog), and the record is a geometry: +0x08 x0, +0x0c y0,
+0x18 x1, +0x20 flags (0x21 open, 0x01 closed), +0x28 rows. What is
particular to the page SETUP windows is their shape -- x 7..0xf4, y 0,
0x40 rows -- shared by all five and by nothing else measured (the PROJECT
menu is 5..0xf6, the MIXER 10..0xec). `panel_server.setup_window_open`
tests slot == `0x46c7d34c` and that geometry with the open flag; the
DISARM ALL popup a NO leaves on page 1 (never times out under route A,
above) no longer trips the note, and the verifier's zero-delta report on
the main screen does not either (`fix_srv.log`, section 1). RECORDING
SETUP (FUNC+RECAB) was not shape-measured; `fix_srv.log` says whether the
note fires there.

### `/run` while frame mode is on

`Rtos.run(ms=)` loops `step()` until its ms elapse; Unicorn's `emu_stop()`
from another thread ends one burst and `run()` starts the next, so
panel_server's 20 s watchdog never ended anything (its docstring said it
did): `/run?ms=5000` while playing held the emulator 356 s (verifier).
`/run` now runs 5 ms slices (`Panel.run_ms`) and gives up after a 19 s
budget (one under the watchdog, which stays as the backstop), reporting
what ran; `/status ran_ms` moves with the slices. Slice boundaries move no
firmware event (timers, frames and the panel UART advance by sample
count). Measured on the own server (`fix_srv.log`, port 8582, OTLIVE):
while playing `/run?ms=1000` returned in 17.0 s wall, 59 emulated ms per
wall s -- the pump's own rate (58.2 over the 65 s play window), so the
verifier's 14 ms/s during the unsliced 356 s run was not the emulation's
rate; `/run?ms=5000` stopped at 1202 ms after 20.1 s; `/status ran_ms`
advanced in all 36 one-second polls (45-70 ms each). Idle, `/run?ms=1000`
is instant (idle time is skipped to the next timer expiry). Same run:
PLAY down through `/key` 0.82 s, the beat box 2 redraw at 54.8 s wall =
3163 emulated ms (`Z_play_02_0055s.png`), STOP down 1.01 s, idle 862
before / 796 emulated ms per wall s after, NO taps 0.05 s. The `/knob`
note (`fix_srv.log` section 1): off on the main screen, on the DISARM ALL
popup, on AMP page 1 under that popup (A +1 redrew ATK, seq 6->7,
`Z_amp1_popup_A+1.png`), after a SETUP close, on MIXER, TEMPO, RECORDING
SETUP (its own narrower shape, `Z_recording_setup.png`) and PLAYBACK page
1; on for AMP SETUP, AMP SETUP reopened and LFO SETUP.

### Trig LEDs while playing: the sequencer's per-tick message never reaches the UI task

- First, a correction that changes how the section above reads: **the OS
  image loads at `0x40000400`** (`scripts/disasm.sh`'s base is right;
  `fix_lab5.log`: the RAM bytes at `0x40013620` are the file's bytes at
  offset `0x13220`). Absolute operands in the code are RAM addresses, so
  a static scan of the file must add 0x400 to file offsets and nothing to
  operands. With that, the `pc 0x400137d4` of the section above is exactly
  the store `moveb %d0,%a0@(0,%d4:l)` inside set_led (RAM `0x40013784`:
  set_led(index, on)), i.e. the write-hook pc is precise -- and
  `fix_lab.py`'s "the LED driver is never entered" (hooks placed at file
  offsets, 0x400 too low) is void; `fix_lab6.py` re-did it at the RAM
  entries.
- The LED driver (RAM): `0x40013634` send `0x20+row <mask>` through the
  panel-UART ring writer `0x40010aa4`; `0x400136a8` flush the rows whose
  state^blink changed; `0x400136f4` set_led_timed; `0x40013784`
  set_led(index, on); `0x40013810` init (133 LEDs); `0x4001387c` the
  timed-LED countdown; blink-mask setters at `0x400131a0`..`0x40013354`;
  state array `0x460ba9ae` (17 rows), blink `0x460ba98c`, last-sent
  `0x460ba99d`. Its callers, measured (`fix_lab6.log`, entry hooks with
  return addresses): a REC toggle or a track key is 90-137 driver entries
  -- blink-mask housekeeping from `0x40034e4c/e52`, `0x40044030/40`,
  `0x4004d580` (the UI's periodic pass, ~every 37 ms), the page/trig
  redraw (`0x400353xx`, `0x40083fxx`), then `flush` from
  `0x40041aec`/`0x400487xx`/`0x4004e948` and 1-5 `send_row`s -- and **0**
  set_led calls: the trig, REC and track LEDs are drawn by rewriting the
  blink masks, not through set_led.
- PLAY (frame mode on, both grid-rec off and on): exactly **one** set_led
  in 1.8 s of play, at 5.3 ms -- `set_led(38 = row 4 bit 6, 3)`, the tempo
  LED, from `0x40056f2a` in the **ui** task -- and then nothing but the
  periodic housekeeping (16+16+7 blink clears per 600 ms, one flush pair
  from `0x40061efe/f04`) while the tick byte `0x800065b6` runs 2, 4, 0,
  2, 4, 0, 3 per 300 emulated ms and the step byte `0x800065b5` 0, 1, 2
  (~900 emulated ms per 16th here). STOP is a 517-entry full LED redraw.
  Nothing is queued for the panel at all during the play windows
  (`fix_lab3.log` / `fix_lab4.log`: 0 bytes in 1.2-1.5 s).
- `0x40056f2a` is the UI task's message loop (`0x40056c72`: receive from
  the UI queue `0x460d1664` via `0x40000d00`, dispatch on the message's
  type byte): under a 24-count at `0x460d1e10` it flashes the tempo LED
  once per beat -- the per-tick message handler. A running light on the
  trig rows is work of the same kind and would come the same way. The
  queue's posters (enqueue `0x40000c3c`, static scan at the right base):
  `0x4009c506` once at transport start (message `0x400abaca`, right after
  `0x800065b8` := 1 -- the one set_led above), `0x400a4dd2` from the
  sequencer's tick path (message `0x400abacb`, after bumping the tick
  counter `0x80006511`, gated on `0x46107568 == 0`, which the start
  clears), `0x40055cd6` the periodic UI refresh (`0x400a727a`, a
  countdown at `0x400c0cf0`), `0x40040b70` a UI-internal one.
  `fix_lab7.log` (the enqueue hooked on the UI queue, the loop's receive
  hooked at `0x40056c7c`): under route A the tick message IS posted and
  received -- type 7 every ~140 emulated ms (one per 24-PPQN tick; 4-5
  per 600 ms), the type-2 start once, posts and receives matching to the
  0.1 ms -- and its handler only runs the beat counter (a flash every 24
  ticks = ~3.3 s emulated = ~57 s wall here, the same period as the beat
  box). Nothing else is ever posted to the UI queue while playing.
- The trig-row light itself (`fix_lab8.log`, the pass `0x40043fdc`
  hooked): it is a **flash per trig, not a chase**. The pass runs on
  events only (2 per REC toggle or PLAY, 1 in 1.2 s of play, 9 at STOP),
  and for each of the 16 trig LEDs clears the blink and, if the LED's bit
  in the 16-bit mask `0x460d1794` is set, `set_led(led, 0xa)` (a timed
  flash) and clears the bit. The mask's setters: the trig-key handler
  (`0x40044614` / `0x400446d2`, bit = the key, after the sequencer calls
  `0x4009f3a4` / `0x4009b5c8`) and a UI message handler at `0x400622da`
  that ORs the message's trig byte into it -- the "trigs fired" note from
  the sequencer. `0x460d1736` is the GRID RECORDING flag (REC writes 1 at
  `0x400487c0`, 0 at `0x40048798`); with it set the pass takes the
  position branch (`0x40034bd4`, pattern/track/page) and clears the mask
  (`0x40043ffa`) before drawing the placed trigs. Measured while playing
  (grid off and on, poked trigs on steps 1/5/9/13, step byte 0 -> 4): the
  mask is written only by those clears, no bit is ever set, no flash --
  because the only messages the sequencer posts to the UI are the start
  and the tick; the trigs-fired one never comes (the fired trig is the
  DSP side of the frame path, RTOS_FORK section 10). That is the gap: an
  emulator one, in the sequencer-to-UI notification of fired trigs, not
  in the tick and not in the LED driver. The PLAY and tempo LEDs (row 11
  bit 3, row 4 bit 6) do light at PLAY through `/key` now, which is what
  the "no sequencer LEDs" of the report referred to together with the
  never-moving position; the per-trig flashes will need `tools/emu` to
  post that message. panel_server carries what the firmware sends.

## 12 Sep 2026: the trig-row running light -- found on the port, and it is not an emulation gap

Verifier follow-up on "Trig LEDs while playing" above, done on the C++
port (`out/emu/ot_emu --interactive`, OTLIVE/PROJECT, `--rtc 1000000000`,
stock image). Scratch (gitignored): `out/_agents/seqled/` -- `repro.py`
(PLAY through the matrix, the panel stream decoded per 50 ms slice, LED
rows and the trigs-fired mask/ring logged; `--no-activate`, `--dsp`),
`probe.py` (the same run with PC watches over the pipe; `--reselect`,
`--trigkey`), `hits.py` / `dis.py` (the batch's `--watch-pc` report and
the 0x40000400 listing sliced by address), `batch1..6.log` (the batch
`--sequencer` runs), `*.out`, `*.tx.bin` (raw panel streams), `os.lst`.
Boot + load 19-20 s wall; each play window 2-9 s wall.

**The sequencer-to-UI "trigs fired" note exists, it is a SYS command, and
under the port it is posted, dispatched and drawn** -- as long as the
panel has NOT "activated" the tracks first. Everything the section above
attributes to a missing emulator message is one poke in `panel_server`.

### The message, decoded

- `0x400622da` is not a UI-queue handler: it is **case 22 of the sys task's
  78-entry dispatcher** (receive on `0x460d17ae` at `0x40061cd8`, index =
  `msg[0]-1`, 16-bit offset table at `0x40061cfa`; table[21] ->
  `0x400622da`). The handler ORs `msg[1] << 8` into the 16-bit mask
  `0x460d1794` (`movew` at `0x400622ee`), calls `0x4007e998(0)` -- the
  page-0 LED callback from the table at `0x460e762c` (= the trig-LED pass
  `0x40043fdc`), then the flush `0x400136a8` -- and, if the fired byte has
  the current track's bit (`0x100b14cc`), takes `0x40062ab6`.
- **Its poster is the ColdFire frame builder, not the DSP.** In the
  per-frame per-track loop (8 tracks, `cmpl %sp@(114),%d5` at
  `0x4000c824`) a byte at `%sp@(113)` collects `1 << track` (`%sp@(212)`,
  set at `0x4009b812`) for every track whose fired flag `%sp@(196)` is set
  (`0x4000c6f4`; the flag is written at `0x4000bef8` on the trig-match
  path `0x4000bebe`..). After the loop, if the byte is non-zero,
  `0x4000c832` builds `16 <byte>` in the ring at `0x461052a6` (cursor
  `0x46104d42`, +2 per post) and `0x4000c858` posts it to the sys queue.
  `msg[1]` is the TRACK mask; the pass flashes trig LEDs 9-16 for tracks
  1-8 (`set_led(id, 0xa)` at `0x4004405e`, ids from the table
  `0x400a76ae` = 0, 2, .., 30, bits 8-15 of the mask), then clears the bit.
  The `--dsp` cores are irrelevant: the same LED timeline comes out with
  `--frame` and with `--dsp` (`after_frame.out`, `after_dsp.out`).
- The batch path (`--sequencer`, `Rtos::startTransportLive` = FW_TRANSPORT(0)
  + FW_START_TRACK(0..7) through `callAsMain`) had this working all along:
  `batch2.log` (`--poke-trig` and trigs poked on steps 1/5/9/13, 3000
  frames = 209 ticks): `0x4000c858` posts at samples 277,993 (byte 0xff:
  the fixture's own step-1 trigs on all tracks), 300,023 (0x01, the poked
  step 5) and 322,074 (0xff, step 9); each is dispatched at `0x400622da`
  in sys ~40 samples later, the mask goes `0xff00 -> 0xfe00 -> .. -> 0`
  (`0x4004406e`, one clear per flash), `set_led(16..30, 10)` and
  `send_row(2, ..)`/`send_row(3, ..)` follow. The live nibble
  `0x46104d15` is written for all 8 tracks at frame 0 (`flags 0x10`).

### Why the panel never saw it: pattern +0x54 is PLAYS FREE, not "active"

`panel_server.activate_tracks` (10 Sep 2026) writes 1 into the pattern
record at `+84 + 2330*t` for all eight tracks before PLAY, on the reading
that `FW_START_TRACK` "only promotes an active track". The byte's two
consumers say the opposite (objdump prints a plain displacement in
decimal -- `%a0@(84)` -- and an indexed one in hex -- `%a0@(54,%d0:l)`;
both are 0x54):

- **FW_TRANSPORT(0)** (`0x4009b964`, the PLAY key's path from sys at
  `0x40061894`, and the batch's) sets up each track only if the byte is
  ZERO: `0x4009bc76 tstb %a3@ / bnew 0x4009bd2c` with `%a3 = 0x400e2234 +
  2330*t` (`probe_regs.out`), and again at `0x4009bf8c` for the second
  loop. A set byte skips the track: no per-track schedule, so the step
  handler `0x4009d1e8` is never called (0 hits in 400 ms, `probe_key2.out`
  -- vs 8 per step from `0x4009dca2` once selected), no trig path
  `0x4009d422`, no fired flag, no post. The 24-PPQN clock, the step byte,
  the LCD bar and the tempo LED run regardless, which is what made the
  "sequencer runs but the light does not" look like a message gap.
- **FW_START_TRACK(t)** (`0x4009b5c8`) does something only if the byte is
  SET (`0x4009b630 tstb %a0@(84) / beqw 0x4009b95a`): it is the trig-key
  start (callers `0x4004460c` / `0x400446ca` in the trig-key handler;
  `probe_trigkey.out`: with the bytes set, PLAY starts nothing and a trig-1
  tap calls FW_START_TRACK(0) from `0x40044612`). The only UI writer of the
  byte is an encoder toggle clamped to 0..1 for the UI's current track
  (`0x400824fe`, via `0x100b14d0`). Skipped by the sequencer's PLAY,
  started by its trig key, a per-track ON/OFF in the pattern: the
  Octatrack's **PLAYS FREE** setting. With the fixture's bytes all clear
  the batch's eight FW_START_TRACK calls return at `0x4009b634` having done
  nothing, which is why the batch never needed them and never noticed.
- FW_SEQ_SELECT is not the difference: the sequencer's bank/pattern
  `0x800065bd/be` read 0/0 after the load on both backends (`seqsel.out`,
  `batch4.log`: written 0 at `0x400a05f6/0608` four times during the
  load, the last from the LOAD PROJECT handler's own last step at
  `0x400907da`), and re-selecting A01 through the panel (PATTERN 0x25.6 +
  trig 1 -> `0x400a1030(0,0)` from `0x40056b6e`, `probe_resel.out`) changed
  nothing while the bytes were set.

### Measured: before / after (matrix PLAY, `frame on`, trigs poked on steps 1/5/9/13 of track 1)

| | `activate_tracks` (today's panel) | bytes left clear |
|---|---|---|
| panel bytes over 1.1 s of play | 50 (`before_activated.tx.bin`) | 96 (`after_noact.tx.bin`) |
| LED-row messages from the PLAY row on | 2: `2b08 .. 2b01` (PLAY on/off) | 33 |
| rows 0x20-0x23 | never sent, state array `00000000` | `2003` at PLAY, then `200c 2030 20c0` / `2103 210c 2130 21c0` / `2256 2259 2265 2295` / `2356 2359 2365 2395` -- one step to the right every 125 ms, wrapping at 16 (`after_frame.out`, 2.1 s = 16 steps) |
| fired-track flashes | none | at PLAY `2201 2205 2215 2255 2301 2305 2315 2355` (8 x set_led on ids 16..30), rows 0x22/0x23 = `55` after |
| ring cursor `0x46104d42` | 0 throughout | +2 at steps 1, 5, 9, 13 and again at the wrap (bytes read for the first three: 0xff, 0x01, 0xff -- `probe_noact.out`) |
| `0x4009d422` / `0x4000c858` / `0x400622da` hits in 1.1 s | 0 / 0 / 0 | 9 / 2 / 2 (`probe_noact.out`) |

Same numbers with `--dsp` (`after_dsp.out`: 306 panel bytes, 43 LED/level
messages over 2.1 s, identical rows; 22.4 s wall vs 9.4 s).

### What to change, and what was changed

- `panel_server`: do NOT set `+84` on PLAY (`activate_tracks` in the
  PLAY-down hook and in `poke_trig`); the OTLIVE fixture plays as saved.
  The flag is a user setting to expose, not a prerequisite. (Changed in the
  verifier round, next section: the two call sites are gone, PLAY answers
  `active=[0, ..]` = the bytes as saved, and `/leds` chases.)
- The port (`tools/emu/ot_emu/main.cpp`): four commands over the pipe so
  this kind of question can be answered without a batch run -- `watch
  <addr>[,..]` / `watch off` (`Machine::watchPc`), `hits` (every hit since
  the last call: `instr:pc:d0:d1:a0:a1:sp:stack0..4:d2..d7:a2..a6`, hex),
  `watchmem <addr> <len>` (`Rtos::watchMem`), `writes`
  (`sample:pc:addr:val:size:tcb`). `rtos.h` says what FW_START_TRACK is.
  `ctest` 7/7, `smoke.py --card` PASS on the rebuilt `out/emu/ot_emu`.
- The note in `COLDFIRE_PORT.md` O14i ("the same open item as under route
  A") and the "sequencer's per-tick message never reaches the UI task"
  reading above are superseded by this section: the message is sys opcode
  22 from the frame builder, and it reaches the LEDs.
- Not measured: what `+0x56` (the plays-free start mode FW_START_TRACK
  reads) needs for a trig-key start to land -- the tap above called it and
  no track ran within 1 s. Whether the 0x55 rows 0x22/0x23 leave after the
  timed-LED countdown (`0x4001387c`) -- they were still lit at 2.1 s -- was
  the port's real gap: the countdown's clock is a DMA timer the port did
  not have. Next section.

## 12 Sep 2026, verifier round: the light follows the current track, and the flashes that never went out were the port's missing DMA timers

Follow-up on the section above, on the C++ port, same rig (`out/emu/ot_emu
--interactive`, OTLIVE/PROJECT, `--rtc 1000000000`, matrix PLAY, `frame on`,
trigs poked on steps 1/5/9/13 of track 1). Scratch (gitignored):
`out/_agents/seqled/` -- `seqcheck.py` (the verifier's per-25-ms LED-row
decode, `--activate <list>`, `--trackkey`, `--watch`), `ledtimer.py` (the
LED countdown, its signaller and the LED arrays watched over PLAY + STOP),
`rv_*.out` (per-track re-checks), `ledtimer_before.out` /
`ledtimer_final.out`, `after_final.out` / `after_final_dsp` (the 2.1 s chase),
`batch_final.log` (the batch cross-check), `loads_*.log` / `popup_*.log` (the
boot mount), `smoke_dtim/` and `smoke_final*/` (the boot screens),
`e2e_ready.png` / `e2e_play.png` (the shipped panel). Boot + load is now
37.5 s of wall (below).

### The light follows the UI's current track

"`activate_tracks` sets the byte on all 8 tracks, which stops every track"
is true; the condition for the LIGHT is the CURRENT track's byte. Measured
(`seqcheck.py --activate <all but one>`, 400 ms of play, the lit pair
against the STEP byte `0x800065b5`): only track 4 (T5) left clear -> chase
(`2003 200c 2030 20c0`, 125.1-125.2 ms per step); only track 5 clear -> no
chase, although its trig fires (`2304`) and the posts run; only track 0
clear -> no chase; only track 0 clear with T1 tapped first (`0x100b14cc`
4 -> 0) -> chase; {0,1,2,3} clear -> none, {4,5,6,7} -> chase. The fixture
loads with track 4 current (`0x100b14cc` = 4, `0x80000000` = 4).

The code says why. The trig-LED pass `0x40043fdc` clears ids 0..31 on every
pass (`0x400131c8` from `0x40044030`/`0x40044040`, 15 passes per 400 ms)
and its tail `0x400444fc..0x40044574` draws ONE pair: track = the byte at
`0x80000000` (+8 when `0x80000012` is set, the MIDI side); `0x4009b290
(track)` must answer 1 (running); `0x4009b2b0(track)` is the position; ids
`2*(pos & 15)` and `+1` are lit through `0x400135b0` (level 15 / d4) and
`0x400131a0` (the phase array `0x460ba98c`; the hits return to `0x4004455e`
and `0x40044574`). FW_TRANSPORT(0) marks a track running only if its `+0x54`
byte is zero (above), so the current track's byte alone decides whether
there is a light; the other seven decide only whether their trigs fire. The
case-22 handler reads the same track as a bit index at `0x400622fe`
(`btst` against msg[1]; `0x40062ab6` -> `0x40045614` when the fired track is
the current one).

### The flashes never went out: the port had no DMA timers

`set_led(id, n)` (`0x40013784`) is not "on": it writes n into a per-id
COUNTDOWN (`0x460ba9cc`, 136 longs), sets the bit in `0x460ba9ae` and
flushes (`set_led_timed` `0x400136f4` adds to the counter instead).
`0x4001387c` walks the table, decrements, clears the bit at zero and
flushes; it runs in the task at `0x4005593c` (RTOS_FORK.md's "key-repeat
timer"), which pends (`0x400007a4`) on `0x46c7e0e2` every pass -- and the
ONLY signaller of `0x46c7e0e2` is the interrupt handler `0x40055cb8`
(vector 0x61 = INTC0 source 33, installed at `0x40040482` from sys's
init), which acknowledges by writing 2 to `0xfc074003`: DTER of **DMA timer
1**. DTRR 68750, DTMR 0x1d (bus/16, restart, reference interrupt) =
68751 * 16 / 132 MHz = 8.333 ms, 120 Hz. Every second tick the same handler
posts `0x01` to the UI queue `0x460d1664` and `0x05` to sys (`0x40061e8e`):
the firmware's 60 Hz UI and sys ticks. The port modelled no DMA timer
(nothing under `0xfc07xxxx`; RTOS_FORK.md's vector table carried 0x61/0x62
as "?"): the counters sat at 10, rows 0x22/0x23 at 0x55, the beat flash
(`set_led(38, 3)` from `0x40056f2a`, once per beat) never ended, and
neither tick ever ran -- 0 hits on `0x4001387c` and `0x40055cfe` over 2.5 s
of play + stop (`ledtimer_before.out`).

The port now carries the block (`periph.h` `DmaTimer`: DTIM0..3 at
`0xfc070000 + 0x4000*n`, INTC0 sources 32+n, DTMR/DTXMR/DTER/DTRR/DTCR/
DTCN, the 132 MHz internal bus clock -- DTRR2 = 132,000,000 with bus/1 is
the firmware's own one-second constant, which is what pins it; gate section
in `test_periph.cpp`). After (`ledtimer_final.out`, `after_final.out`):
`0x4001387c` and the signal run 6 times per 50 ms (291 each in 2.4 s); the
eight fired-track flashes `2201 2205 2215 2255 2301 2305 2315 2355` at PLAY
are followed 83 ms later by `2200` / `2300` (10 ticks); the step-5 flash
`2201` at 450 ms is gone by 550 ms; the beat LED shows as `2465` -> `2425`
(3 ticks = 25 ms) every 500 ms; at 2.1 s the rows are `0c 00 00 00` (were
`0c 00 55 55`); STOP sends `2300 3648 2901 2b01 2200` and the countdown
table reads all zero. 52 LED-row messages over 2.1 s (were 27). The chase
itself is unchanged (18 steps, 125.1-125.2 ms, 0 of 84 slices off the STEP
byte), and identical with `--dsp` (`after_final_dsp`).

What else the block is, measured, and running now:
- DTIM2: DTMR 0x13, DTRR 132,000,000, free-run: the one-second idle poll of
  the MIDI note-length scheduler `0x400409f4` (mask `0x46c7e0de`, 128 slots,
  3-byte messages into the UART0 ring `0x400b966c` through `0x40010bc8`; it
  clears DTCN and reprograms DTRR itself, and is also forced through INTFRC
  34). 6 fires per batch run.
- DTIM3: DTMR 0x0b, bus/1, no interrupt: a free-running timestamp
  (`0x4000169a` / `0x400016cc` in the host-port code, `0x40055b42`).
- DTIM0: DTMR 7 = the DTIN0 pin, no interrupt: the MIDI RX ISR timestamps
  0xF8 with it (`0x4001070a`). No pin model; it holds at 0. (The F8
  estimator's 1,881,600-count floor is 11,289,600 / 6 -- 256*Fs over one
  clock at 15 BPM -- a lead, not a measurement.)

### Two consequences of the timers, and what the port does about them

1. **The boot logo.** The LED/key-scan task's first loop
   (`0x400559c6..0x40055b7a`) clears DTCN3 and animates the logo, yielding
   to nothing, until `int(DTCN3 / 660000.0) > 559` -- 2.8 s. The all-ones
   stub read as 4.29e9 (`0x400a6e38` converts unsigned) and the logo left on
   its first pass, which is what every measurement in this tree was taken
   with (the M6a gate at 205 ms, `ready` at sample 277,821). A faithful
   count holds every boot on the logo for 2.8 s (~12 s of wall) and moves
   every sample stamp. The port keeps the old behaviour as a documented
   quirk (`Rtos::Quirks::skipBootLogo`, on by default: DTIM3 READS 2.8 s
   ahead, a constant its other readers, which take differences, cannot
   see); `--boot-logo` runs the logo. The report line says which is in
   force.
2. **The boot mount.** The sys tick's startup step (`0x40052200` ..
   `jmp 0x400256b8` at `0x4007ec5a`) is the firmware's own "mount the last
   set": it reads the set name at `0x100f8480` ~7,500 instructions after
   the media case and, finding it empty, opened "NO SET IS MOUNTED! PLEASE
   MOUNT ONE." over SET DATE/TIME (`0x400256ce`, then the 75x41 popup
   through `0x400116aa`) and, after YES, the CHOOSE A SET browser -- so the
   panel's boot YES closed the popup and the clock record never got written
   (`smoke_dtim/smoke_boot.png`, `smoke_main.png`). The port used to write
   the names after main's next spin, one tick too late; it writes them at
   the media-case join now (`Rtos::loadProjectLive`), the firmware mounts
   the set itself (`0x400255ec`, no popup), and the load is still the one
   the port posts (`0x400907da` once). The set mount reads the card as the
   firmware does: 10285 ATA commands / 42926 sectors per boot (were 5395 /
   22714); boot + load is 37.5 s of wall (was 21 s) and play runs at ~319 ms
   of emulated time per wall second (was 358). `--names-early` (O7b) now
   means two loads (11932 ATA commands).

### Before / after (matrix PLAY, `frame on`, OTLIVE as saved, trigs poked on steps 1/5/9/13 of track 1)

| | before (`B_none`, `ledtimer_before`) | after (`after_final`, `ledtimer_final`) |
|---|---|---|
| running light | chases, 125.1-125.2 ms per step | same |
| fired-track flashes at PLAY | `2201 2205 2215 2255 2301 2305 2315 2355`; rows 0x22/0x23 = 0x55 through STOP and after | the same eight, then `2200` / `2300` 83 ms later |
| step-5 flash (track 1) | `2201` at 450 ms, stays | `2201` at 450 ms, `2200` by 550 ms |
| beat LED | `2903` once at PLAY | `2903`, then `2465` / `2425` every 500 ms (25 ms on) |
| `0x4001387c` / `0x40055cfe` hits | 0 / 0 | 120 Hz (291 each over 2.4 s) |
| rows at 2.1 s | `0c 00 55 55` | `0c 00 00 00` |
| STOP | `2000 2901 2b01` | `2300 3648 2901 2b01 2200` (the last flash cleared) |
| LED-row messages over 2.1 s | 27 | 52 |
| batch `--sequencer` cross-check | posts 0xff / 0x01 / 0xff at samples 277,993.9 / 300,023.1 / 322,074.7 | the same three, 139.8 samples earlier (277,854.1 / 299,883.3 / 321,935.0), mask 0xff00 -> 0 clear by clear (`batch_final.log`) |

### What changed

- `panel_server.py`: `_before_play` and `poke_trig` no longer call
  `activate_tracks`; PLAY answers `active=[0, 0, 0, 0, 0, 0, 0, 0]` (the
  bytes as saved), `poke_trig` reports `plays-free bytes`. The function
  stays for scripts that want a plays-free track, its docstring corrected.
  The shipped server's `/leds` chases end to end (`panel_e2e.py --port
  8583`: `0c 30 c0 0003 000c ..`, the `5555` flashes gone within 100 ms,
  `e2e_ready.png` = SET DATE/TIME closed, the main page).
- The port: `DmaTimer` (`periph.h` / `periph.cpp`) with its gate section in
  `test_periph.cpp`; INTC0 lines 32..35, the decode, `tickTimers`, the idle
  skip's `nextExpiry`, a report line and `dtim1_fired` / `dtim2_fired` in
  the gate JSON (`rtos.h` / `rtos.cpp` / `main.cpp`); `Quirks::skipBootLogo`
  and `--boot-logo`; the names-at-the-join order in `loadProjectLive`.
  `ctest` 7/7 (the M6a gate still at 205.39 ms, the serial prefix byte for
  byte), `smoke.py --card` PASS on the final binary. `COLDFIRE_PORT.md` is
  not this task's file: the O-milestone note for the DMA timers, the logo
  quirk and the mount order is owed there.

## LED colours (measured 13 Sep 2026 on the emulated firmware)

The trig LEDs (rows 0-3) and the track LEDs (rows 5-6) are bi-colour, two
bitmap bits each: the map's bit is RED, the next bit up is GREEN, both lit is
YELLOW. The level nibble of the lit bit's id (`row*8 + bit`) is the
brightness: 15 full, 5 half. Measured against manual 11.5 / 12.4:

| state | bits | level |
|---|---|---|
| sample trig (`[TRIG]` in GRID RECORDING) | red | 15 |
| trigless lock (`[FUNC]+[TRIG]`) | green | 5 (half-bright) |
| trigless trig (`[TRIG]+[NO]` on a sample trig) | green | 15 |
| one-shot trig (`[FUNC]+[TRIG]` on a sample trig) | red+green = yellow | 15 |
| active track | red | 15 |
| other tracks | green | 15 |
| muted active track (`[FUNC]+[TRACK]`) | red+green = yellow | 15 |
| muted unselected track | off | – |

The page (`applyLeds`) renders the pair and the brightness; the other LEDs
stay single-bit with their fixed colour.

## 13 Sep 2026: the crossfader, what the page encoders edit, the scene chords

On the C++ port (`out/emu/ot_emu --interactive`, stock image, OTLIVE
fixture; the BLANK fixture for the init values). Scratch (gitignored):
`out/_agents/panel-ctl/` -- `lab_params.py` (own child, no cores: every
page x encoder with `watchmem`/`writes`, then the fader rows; `lab_params.log`,
`lab_params.json`), `read_init.py` (`init_8594.json`: the BLANK project's
values through an own server on 8594), `lab_scenes.py` (own server on 8593
with the cores: reset, fader, scenes, takes; `lab_scenes_8593.log`, `S_*.txt`/
`.png`), `check_reset.py` (`R_*`).

### The crossfader is `0x40 <adc>` on the panel UART

The RX parser `0x4009228c` classes a report by its first byte's high nibble:
`0x2r` keys, `0x3r` encoders, **`0x40` the fader** (one payload byte, the pot's
ADC value 0..255, row nibble must be 0), `0x7r` a nine-byte report
(PANEL_LINK.md has the decode). The byte goes through a calibration record at
`0x1ffffe` (magic `0x1234`; absent under emulation, so `pos = byte >> 1`) into
sys message kind 4 (`0x40092fac` -> `0x40092f2c` -> `0x40061e0a`), which
stores `0x460d16c8` (127 = scene A, 0 = scene B: the weight table
`0x80003c60` reads `0x8000_0000` at 127), needs AUDIO CC OUT = INT or INT+EXT
(`0x8000004a` bit 0; the fixture has `MIDI_AUDIO_TRK_CC_OUT=3`), echoes CC 48
= 127 - pos when EXT, and redraws the fader icon (LCD x 104-108, y 59-61).

| sent | `0x460d16c8` | LCD blocks |
|---|---|---|
| `0x40 255` / `254` | 127 | 2 / 1 (the icon; the first also the page) |
| `0x40 128` / `127` | 64 / 63 | 1 |
| `0x40 64` / `192` | 32 / 96 | 1 |
| `0x40 0` / `1` | 0 | 1 |
| rows `0x27`-`0x2f`, `0x37`-`0x3f`, `0x41`, `0x42`, `0x4f` with `0x40` | unchanged | 0 (`0x27`/`0x37`: the tempo readout, 2) |

The server's `/xfader?pos=` (0 = A/left .. 127 = B/right, = CC 48) sends
`0x40 (2*(127-pos)+1)`; the page's fader drags, wheels and arrows through it.
Direction confirmed with sound: AMP VOL locks in scene A make the mix 4.4 dB
quieter at `pos=0` than at `pos=127` (below).

### What the page encoders edit (param_map.json)

From the knob handler `0x40055008` and measured with `knob +1 / +5 / -6` per
slot on T5 (FLEX) and T1 (STATIC), `writes` naming the store `0x40055170`
(and the SRAM mirror at `0x40055172`), the LCD box redrawn each time:

| page (first press, kind `0x460d1684`) | slot s of track t = `base` + ... (`base` = `[0x46c82456]` + `[0x100b14cf]`*6322) | T1 / T5 measured |
|---|---|---|
| PLAYBACK (0) | `0x8edaa + t*30 + machine*6 + s` (machine byte `base+0x8eda2+t`: 0 STATIC 1 FLEX 2 THRU 3 NEIGHBOR 4 PICKUP) | `0x40170f8a..8f` / `0x40171008..0d` (+6 = FLEX) |
| LFO (1) | `0x8ee9a + t*24 + 0 + s` | `0x4017107a..7f` / `0x401710da..df` |
| AMP (2) | `0x8ee9a + t*24 + 6 + s` | `0x40171080..85` / `0x401710e0..e5` |
| FX1 (3) | `0x8ee9a + t*24 + 12 + s` | `0x40171086..8b` / `0x401710e6..eb` (FILTER) |
| FX2 (4) | `0x8ee9a + t*24 + 18 + s` | `0x4017108c..91` / `0x401710ec..f1` (DELAY) |
| LEVEL (row 0x36, any page) | `0x80000c50 + 2t` (pc `0x4004ec5a`), Part copy `base + 0x8ed92 + 2t` (`0x4004ec00`) | 108 -> 109 -> 114 -> 108 |

One detent = one unit, clamped to the descriptor's `[min, min+count-1]`
(RATE / HOLD / REL / WDTH / VOL at 127 stay on +1); PTCH (min 4, count 121)
wrote 64 on +1 -- its hook accumulates fractions, so the reset re-reads and
sends again. The descriptor per page is FUN_40031da4's: PLAYBACK from the
machine (`[0x400d5f38 + 4*machine]`), LFO `0x400d37f6`, AMP `0x400d3988`,
FX1/FX2 from the effect id (`base+0x8ed80+t` / `+0x8ed88+t` into
`0x400d5f58` / `0x400d5fdc`); with E = descriptor - 0x38: name `E+0x4e+6s`,
init `E+0x96+s`, min `E+0xa2+4s`, count `E+0xd2+4s`, live = bit 0 of nibble
s of the long at `descriptor+0x18e` (AMP `0x11811111`: F = XVOL is 8, and
knob F on the AMP page writes nothing, measured). **Init values** = the
BLANK fixture's bytes on all eight tracks = the descriptor defaults: PB 64 0
0 127 0 79, LFO 32 32 32 0 0 0, AMP 0 127 127 64 64, FX1 0 127 0 64 0 64,
FX2 47 0 127 0 127 0, LEVEL 108. `/knob/reset` measured on 8593: AMP VOL 84
-> 64 (`sent [-20]`), PTCH 70 -> 64 (`[-6, -1]`), STRT 33 -> 0, LEVEL 93 ->
108; refused: AMP SETUP (`a SETUP page is open`), MIXER, MIDI mode, AMP F.

### The scene chords, end to end

`lab_scenes.py` on the own server (Shift-latched chords are two `/key`
edges, the same as the page sends):

- `[SCENE A]` (0x23.1) held + `[TRIG 2]` (0x20.1): the Part's slot-A byte
  `base+0x8ed90` 0 -> 1; `[SCENE B]` (0x23.2) + `[TRIG 3]`: `base+0x8ed91`
  -> 2. LED rows while SCENE A is held: `01 00 02` = trig 1 red (this slot's
  scene), trig 9 green (the other slot's), the manual's colours.
- Lock: SCENE A held, AMP page, knob D (VOL) -64 on T1..T4: the Part VOL
  bytes do not move (84/64/64/64), the scene block `blob + pattern*0x18b2 +
  scene*0x100 + 0x8f3e2 + t*0x20` gets byte 15 (= AMP*6 + VOL) = 20/0/0/0
  (`0x401716d1/f1/711/731`, `0xff` = unlocked), the VOL box is drawn
  inverted with the locked value while the key is held (`S_lock_held.txt`).
- Morph: with the cores, PLAY 3 s / STOP at three fader positions: A
  (`pos=0`) -7.7 dBFS RMS, mid (64) -5.5, B (127) -3.3 (takes 3/4/5 of
  that server; peak 32768 in all three -- the fixture clips). The parameter
  boxes keep the Part values; the fader icon moves; the DSP-bound copy is
  what changes (midi_re_scene.md). Nothing in the panel needed fixing for
  the flow: the scene keys latch, the chords land, the fader was the
  missing piece.

## 13 Sep 2026: the encoder push -- key-matrix row 0x27, bit = the encoder

The one panel input that was not in the map. On the port with the OTLIVE
fixture, own server on 8593 (`out/_agents/plock/`: `verify_plock.py` the
scripted run -- with `--scan` it taps the candidate cells until the lock
goes, without it uses `/knob/press`; `probe2.py` the toggle / F / LEVEL
probes; `flash_stream.log` the LED stream; `verify.log`, `verify.json`,
`S_*.txt` / `P2_*.txt` the screens before and after every step):

- **The parser has no push class.** `0x4009228c` (PANEL_LINK.md) accepts
  exactly four first bytes: `0x2r` keys (1 payload byte), `0x3r` encoders
  (1), `0x40` the fader (1), `0x7r` (9); anything else leaves it in its
  header state (`0x40092350`). An encoder report's byte is ADDED to the
  pending delta (`0x4009250c`) or posted as the delta (`0x4009254a`), so
  no value of it can mean "push". The key descriptor table at
  `[0x46c901dc]` (`0x4610048c` here) starts `ff 80` -- modifier row 0xff,
  so the ISR's shifted layer (+770) is never used -- then holds one
  12-byte entry per row 0x20-0x27 x bit 0-7, EVERY one live: `01 <code>
  01 00 460d17ae 00000000` = type 1, key code = row*8+bit (0x00-0x3f),
  down 1 / up 0, the UI queue. Rows 0x28+ index the all-zero shifted
  layer and are dropped. So the only key codes without a panel key are
  row 0x27's `0x38`-`0x3f` -- which is why a tap there always redrew the
  tempo readout (the 11 Sep note above): the UI does handle them.
- **Measured.** GRID RECORDING (REC), TRIG 1 held (the fixture's step 1
  has a sample trig on T5, mask `.. 01 01`), encoder A +5: the PTCH box
  inverts (dark pixels 2634 -> 2842) and ONE byte of the 36,568-byte
  pattern record changes, `0x400e46a1` = the track record (`blob +
  pattern*0x8ed8 + track*0x91a`, T5 = `0x400e4648`) + 0x59: `0xff` (no
  lock) -> `0x45` (69 = 64+5, the locked value). Still holding TRIG 1,
  `27 01` then `27 00` (60 ms apart): the byte is `0xff` again, the box
  is drawn normal (2634), nothing else in the record moved, and after the
  release the trig is still there (mask unchanged, LED red). Knob B +5 ->
  `+0x5a` = `0x05`; `0x27.0` does NOT clear it, `0x27.1` does. Knob F +5
  -> `+0x5e` = `0x54` (RTIM 79+5); `0x27.4` leaves it, `0x27.5` clears
  it. The lock bytes: `track record + 0x59 + slot` for the PLAYBACK page
  (A..F = +0x59..+0x5e), `0xff` = unlocked.
- **It is a toggle.** A push with NO lock on that parameter sets one at
  the current value: `0x27.0` on an unlocked PTCH -> `0x40` (64) and the
  box inverts; the next push -> `0xff`. (`0x27.4` in the F run put an E
  lock `0x00` = RTRG 0 on the step the same way; pushed again, gone.)
  The unit's [TRIG] + knob press does this too; the page's gesture
  inherits it.
- **LEVEL is bit 6**, measured through a scene lock (TRIG + LEVEL turn
  changed no pattern byte here): SCENE A held, LEVEL -5 -> the LEV box
  inverts (dark 2632 -> 2804); `0x27.6` with SCENE A still held -> normal
  again (2632): manual 10.3.1, "pressing the LEVEL knob while holding the
  SCENE key removes the lock". C and D (bits 2, 3) follow from the order
  A B . . E F; bit 7 is spare.
- **The lock LED.** With the lock on and the trig released, `/leds/stream`
  (`flash_stream.log`) shows row 0 go `0x01` -> `0x03` for ~24 ms every
  ~490 ms: the red trig LED gets a green blink (yellow for a frame) twice
  a second -- manual 12.5's "flash rapidly" as the emulated firmware
  emits it (a `/leds` snapshot reads `0x01` 23 times in 24). Without the
  lock there is no row-0 traffic; while the trig key is held the blink
  does not run.

`key_map.json` carries it as `knob_push` (a = [0x27, 0] .. f = [0x27, 5],
level = [0x27, 6]). The server's `/knob/press?row=0x30..0x36` sends the
down / up pair through the same per-row state as `/key`, so a trig held by
`/key` or by the page's Shift-latched chord stays held around it; the page
sends it for a double-click on an encoder while a TRIG key is held (alone,
a double-click is still `/knob/reset`).
