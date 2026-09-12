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
- **The panel's key rows are 0x20-0x26 only.** Rows 0x27-0x2f were tapped
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
  no encoder-push cell exists in rows 0x23-0x2f.
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
