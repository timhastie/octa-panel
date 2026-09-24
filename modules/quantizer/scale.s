| SCALE QUANTIZER -- the SCALE accessor's fixed door (24 Sep 2026). GNU as, -mcpu=5475.
|
| Six bytes PINNED at SCALE_AT = 0x400d2ca8 (modules/quantizer/manifest.py),
| just under the paraphonic key mailbox (keys.s at 0x400d2cb0): `jmp qz_scale_mask`,
| linked after quantizer.s so the symbol resolves. The synth's DRAM engine
| (modules/synth/poly.s po_snap) calls this address to read the scale's
| pitch-class mask (d0, 0 = OFF) and snap chord notes onto it, without a copy
| of the mask table; it first checks that a `jmp` is here, so a remix
| without the quantizer (zeros at this address) snaps nothing.
        .text
        .global qz_scale_at
qz_scale_at:
        jmp     qz_scale_mask
