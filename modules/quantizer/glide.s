| SCALE QUANTIZER -- the GLIDE and POLY settings (24 Sep 2026). GNU as, -mcpu=5475.
|
| Two bytes, PINNED at a fixed address (modules/quantizer/manifest.py GLIDE_AT
| = 0x400d2cdc, the last long of the second zero run 0x400d24d0..0x400d2ce0)
| because two units read them: the quantizer unit (the SEQUENCER rows, the
| project lines, the key hooks -- by symbol, resolved by the build's link)
| and the synth voice engine (modules/synth/poly.s, a DRAM unit linked apart
| from the OS-resident units), which reads them as OS absolutes. They live in
| the OS image like qz_scale (the main OS runs from DRAM) and are 0 there.
|   qz_glide  0 = OFF, 1..127 = the synth's glide time  (GLIDE_AT)
|   qz_poly   0 = OFF, 1 = ON: the synth is paraphonic   (GLIDE_AT + 1)
        .text
        .global qz_glide, qz_poly
qz_glide:
        .byte   0
qz_poly:
        .byte   0
        .balign 4
