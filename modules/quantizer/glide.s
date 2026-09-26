| SCALE QUANTIZER -- the GLIDE setting (24 Sep 2026). GNU as, -mcpu=5475.
|
| One byte, 0 = OFF, 1..127 = the synth's glide time, PINNED at a fixed
| address (modules/quantizer/manifest.py GLIDE_AT = 0x400d2cdc, the last long
| of the second zero run 0x400d24d0..0x400d2ce0) because two units read it:
| the quantizer unit (the SEQUENCER row, the project line, the legato key
| hook -- by this symbol, resolved by the build's link) and the synth voice
| engine (modules/synth/poly.s, a DRAM unit linked apart from the OS-resident
| units), which reads it as an OS absolute. It lives in the OS image like
| qz_scale (the main OS runs from DRAM) and is 0 in the image.
        .text
        .global qz_glide
qz_glide:
        .byte   0
        .balign 4
