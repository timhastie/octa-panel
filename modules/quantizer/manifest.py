"""SCALE QUANTIZER -- a per-project SCALE setting (OFF plus 24 scales) that
quantizes the audio tracks' pitch, as the Digitakt / Digitone do. With a
scale on, the PTCH knob on the PLAYBACK page steps to the next scale degree
in the turn direction instead of one raw unit (from 0 up in PHRYGIAN: 1, 3,
5, 7, 8, 10, 12 ...; below the root the same degrees an octave down), and a
[TRIG] key in CHROMATIC trig mode snaps to the nearest degree (ties to the
lower) before it becomes the pitch the voice, the lock and the screen see.
OFF is stock: every detour replays what it displaced.

WHERE THE SETTING LIVES. PROJECT > CONTROL > SEQUENCER gains a fourth row,
SCALE, under CHAIN AFTER / SILENCE TRACKS / LFO AUTO CHANGE: the window is a
generic list (state 0x460e43d8, init 0x40065c7c, draw 0x40065b14, keys
0x40065cec, LEVEL knob 0x40065c98) over three parallel pointer tables --
labels 0x400b27d0, getters 0x400b27dc, setters 0x400b282c -- which the
build grows to four entries (TableGrow) and repoints. The list is created
with count 3 / visible 3 (`pea 3; pea 3` at 0x40065c7c); the count becomes
4 (poke) and the three visible rows scroll, as the firmware's longer lists
do, once the draw loop indexes the tables from the scroll offset instead of
0 (qz_draw). The value byte lives in the unit (qz_scale), OFF in the image.

PERSISTENCE. The project file is text (project.work, KEY=value lines); the
loader 0x400866c4 is a strcmp chain that REJECTS an unknown key (0x40086d3a,
error -51), but skips any line starting with '#' at 0x400867a2 before the
chain -- on stock firmware too. So the setting is written as
"#SEQUENCER_SCALE=n" right after PATTERN_CHANGE_CHAIN_BEHAVIOR (qz_wr,
in the writer 0x40088882..), read back by a detour on the '#' check
(qz_ld_line), and reset to OFF at the start of every storing load
(qz_ld_entry; the loader's parse-only pass leaves it alone). A stock unit
loads such a project unchanged.

THE KNOB. 0x40055008(slot, delta) resolves the Part byte, calls the slot's
own handler (PTCH: 0x40032d08, a fractional accumulator), clamps to the
descriptor's [min, min+count-1] and stores at 0x40055170 -- the hook. A
turn with a [TRIG] key held never reaches it: the p-lock editor (the store
at 0x40050e60, found with ot_emu's watchmem) starts from the step's lock
byte, or the Part's value when there is none, and writes the lock and its
SRAM mirror; it is hooked the same way, with the same rule. With
a scale on, for slot A of a kind-0 (PLAYBACK) page whose slot A is named
"PTCH" (STATIC 0x400d301c, FLEX 0x400d31ae, PICKUP 0x400d3664; THRU and
NEIGHBOR have no PTCH), the stored value is recomputed from the value the
knob was turned from: |delta| steps to the next raw on a scale semitone
(raw = 64 + 5 * semitones, 4..124; a value between degrees -- set with the
scale off, or a fraction -- snaps to the nearest degree in the turn
direction), unchanged at the ends, which is the stock clamp. The screen,
the SRAM mirror, a held trig's lock (0x40042158) and the MIDI CC echo all
take the stored value after the hook. RATE (slot D) is a playback rate
(-63..+63 per the manual, 0 = stopped, negative = backwards), not a
semitone quantity, and is left alone.

CHROMATIC. 0x4004fb94(track, key index 0..24, press) turns the index into
the raw pitch at 0x4004fc58 (`lea (4,%a2,%a2.l*4),%a2` = 5*idx + 4, root =
index 12 = TRIG 13), writes it as the lock byte 0x46c7dfda + t*32, trigs the
voice (0x40005030 / 0x46c80354) and, in LIVE RECORDING or with a trig held,
records it as the trig's PTCH lock (0x40042158 with a2). The detour snaps
the index before the lea. The MIDI note the key sends stays the key's own
(it is matched on release).

GLIDE (24 Sep 2026). A fifth SEQUENCER row, GLIDE: OFF / 1..127, the synth
machine's glide time (modules/synth/synth.s slews a synth voice's PTCH word
toward its target with a first-order lag, 10 ms at 1 .. 1 s at 127), saved
as "#SYNTH_GLIDE=n" after the SCALE line by the same writer and loader
detours (a storing load starts from OFF). The byte, qz_glide, is PINNED at
GLIDE_AT (glide.s, the last long of the second zero run) because the synth
cave -- position independent, ratified bytes -- reads it as an OS absolute;
this unit reaches it by symbol through one accessor (qz_glide_of). With
GLIDE on, a CHROMATIC key pressed while another key of the track is held is
a LEGATO note: two jmp detours in the key handler 0x4004fb94 (qz_leg1 at
0x4004fbfe, qz_leg2 at 0x4004fc94) take the stock's FUNC + key trigless path
-- no voice restart, no envelope retrigger, the pitch changes and glides --
suppress the old key's voice note-off (its MIDI note-off still goes out) and
make the new key the held key: releasing the first key does nothing,
releasing the last releases the note (the 303 rule). GLIDE OFF, FUNC held, a
release or nothing held: stock, byte for byte. Legato applies to any audio
track (a sample changes pitch without a restart); the glide itself is the
synth's.

Verified in ot_emu through the virtual panel (README). UNFLASHED.
"""

from remix.schema import Detour, Kind, Linked, Module, Poke, TableGrow

H = bytes.fromhex

# The GLIDE byte's fixed address (glide.s): the last long of the second zero
# run 0x400d24d0..0x400d2ce0 (the synth page cave is pinned at its start and
# ends at 0x400d2c6c). modules/synth/synth.s reads it as GLIDE_AT -- keep
# the two constants equal.
GLIDE_AT = 0x400d2cdc

MODULE = Module(
    name="quantizer",
    key="SCALE QUANTIZER",
    kind=Kind.CF_PATCH,
    doc="PROJECT > CONTROL > SEQUENCER > SCALE: the PTCH knob and CHROMATIC "
        "trig keys quantize to a scale (24 scales, OFF = stock); > GLIDE: the "
        "synth's glide time (OFF, 1..127) and 303-style legato on the chromatic keys.",
    linked=(                                   # link order: qz names qz_glide
        Linked("qzg", "modules/quantizer/glide.s", cpu="5475", cave_addr=GLIDE_AT),
        Linked("qz", "modules/quantizer/quantizer.s", cpu="5475"),
    ),
    detours=(
        Detour(0x40055170, H("1482" "1a82" "320e"), "qz", "qz_knob",
               "knob handler 0x40055008: the store -- PTCH steps by scale degree",
               kind="jsr"),
        Detour(0x40050e60, H("13848859" "73b9100b14cc"), "qz", "qz_plock",
               "p-lock editor (TRIG held): the lock store -- PTCH steps by scale degree",
               kind="jsr", pad_to=10),
        Detour(0x4004fc58, H("45f2ac04" "71b9100b14cf"), "qz", "qz_chrom",
               "CHROMATIC trig key -> pitch: snap the key index to the scale",
               kind="jsr", pad_to=10),
        Detour(0x4004fbfe, H("4ab946c7dd26" "663e"), "qz", "qz_leg1",
               "CHROMATIC key held + a new key with GLIDE on: keep the held key, no voice note-off",
               kind="jmp", pad_to=8),
        Detour(0x4004fc94, H("4ab946c7dd26" "6716"), "qz", "qz_leg2",
               "CHROMATIC legato with GLIDE on: the trigless trig, the new key becomes the held key",
               kind="jmp", pad_to=8),
        Detour(0x40065bca, H("4282" "4fef0020"), "qz", "qz_draw",
               "SEQUENCER window draw loop: index the row tables from the scroll offset",
               kind="jmp"),
        Detour(0x400866cc, H("2c2f05c0" "202f05c4"), "qz", "qz_ld_entry",
               "project loader entry: a storing load starts from SCALE = OFF",
               kind="jmp", pad_to=8),
        Detour(0x400867a2, H("122f048f" "7101" "7a23"), "qz", "qz_ld_line",
               "project loader '#' line: read #SEQUENCER_SCALE=n",
               kind="jmp", pad_to=8),
        Detour(0x400888aa, H("73398000004f" "2f01"), "qz", "qz_wr",
               "project writer: #SEQUENCER_SCALE=n after PATTERN_CHANGE_CHAIN_BEHAVIOR",
               kind="jmp", pad_to=8),
    ),
    tables=(
        TableGrow("SEQUENCER labels", old=0x400b27d0, count=3,
                  symbols=(("qz", "qz_lbl_scale"), ("qz", "qz_lbl_glide")),
                  refs=((0x40065bd8, 0x400b27d0),)),
        TableGrow("SEQUENCER getters", old=0x400b27dc, count=3,
                  symbols=(("qz", "qz_get"), ("qz", "qz_get_glide")),
                  refs=((0x40065bde, 0x400b27dc),)),
        TableGrow("SEQUENCER setters", old=0x400b282c, count=3,
                  symbols=(("qz", "qz_set"), ("qz", "qz_set_glide")),
                  refs=((0x40065cc4, 0x400b282c), (0x40065d3e, 0x400b282c),
                        (0x40065d58, 0x400b282c), (0x40065d72, 0x400b282c))),
    ),
    pokes=(
        Poke(0x40065c7c, expect=H("48780003"), write=H("48780005"),
             note="SEQUENCER window: 3 -> 5 rows (SCALE, GLIDE; 3 visible, scrolls)"),
    ),
)
