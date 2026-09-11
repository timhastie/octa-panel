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
