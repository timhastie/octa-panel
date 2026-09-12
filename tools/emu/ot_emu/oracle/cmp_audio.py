#!/usr/bin/env python3
"""Compare two audio captures within an LSB tolerance, and check they are
sample-ALIGNED (Phase B contract, docs/firmware/COLDFIRE_PORT.md O16a).

    cmp_audio.py A B [--fmt s16|wav24|auto] [--channels N] [--tol LSB]
                     [--frac PERCENT] [--len-tol FRAMES]

Formats: `s16` = raw little-endian signed 16-bit interleaved (the pipe's
`audio read` PCM, --channels 2 by default); `wav24` = the batch's
`--audio-out` WAV (24-bit words, one slot per channel; 16-bit WAVs are
accepted and compared as 16-bit words); `auto` (default) picks wav24 for
a `.wav` suffix, s16 otherwise.

Reports, on one line: frames x channels per side, max |diff| over the
compared samples, how many samples differ and the fraction in percent,
the first differing frame (channel, both values), the onset frame (first
frame with any non-zero sample) on each side, any trailing length
difference, and a shift hint (does B look like A moved by +-1..3 frames?)
when the samples differ. Then a PASS/FAIL line.

PASS requires ALL of: identical header (channels, width, rate for WAV);
|frames_A - frames_B| <= --len-tol (default 0; the extra trailing frames
are not compared); max |diff| <= --tol (default 0); differing samples
<= --frac percent of the compared samples (default 0); the onset frame
identical on both sides (alignment: a schedule change may move an LSB,
it may not move WHEN the audio starts). Exit 0 on PASS, 1 on FAIL, 2 on
a missing or unparseable file. Byte-identical inputs are reported without
decoding (the fast path)."""
import argparse
import array
import sys


def parse_wav(d):
    """-> (channels, bits, rate, data bytes) or raises ValueError."""
    if len(d) < 12 or d[0:4] != b"RIFF" or d[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    o = 12
    channels = bits = rate = fmt = None
    while o + 8 <= len(d):
        cid = d[o:o + 4]
        ln = int.from_bytes(d[o + 4:o + 8], "little")
        body = o + 8
        if cid == b"fmt " and ln >= 16:
            fmt = int.from_bytes(d[body:body + 2], "little")
            channels = int.from_bytes(d[body + 2:body + 4], "little")
            rate = int.from_bytes(d[body + 4:body + 8], "little")
            bits = int.from_bytes(d[body + 14:body + 16], "little")
        elif cid == b"data":
            if fmt not in (1, 0xfffe) or bits not in (16, 24) or not channels:
                raise ValueError(f"unsupported WAV (fmt {fmt}, {bits} bits, {channels} ch)")
            return channels, bits, rate, d[body:body + min(ln, len(d) - body)]
        o = body + ln + (ln & 1)
    raise ValueError("no data chunk")


def decode(data, bits):
    if bits == 16:
        a = array.array("h")
        a.frombytes(data[:len(data) - len(data) % 2])
        if sys.byteorder != "little":
            a.byteswap()
        return a
    n = len(data) // 3
    fb = int.from_bytes
    return [fb(data[i:i + 3], "little", signed=True) for i in range(0, n * 3, 3)]


def onset_frame(data, frame_bytes):
    """first frame holding a non-zero byte, or None (all zero / empty)."""
    nz = len(data) - len(data.lstrip(b"\x00"))
    return None if nz >= len(data) else nz // frame_bytes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--fmt", choices=("s16", "wav24", "auto"), default="auto")
    ap.add_argument("--channels", type=int, default=2, help="s16: words per frame (default 2)")
    ap.add_argument("--tol", type=int, default=0, help="max |diff| allowed, in the format's LSB")
    ap.add_argument("--frac", type=float, default=0.0, help="max differing samples, percent of compared")
    ap.add_argument("--len-tol", type=int, default=0, help="max |frames_A - frames_B|")
    a = ap.parse_args()

    try:
        da = open(a.a, "rb").read()
        db = open(a.b, "rb").read()
    except OSError as e:
        print(f"missing: {e}")
        return 2
    fmt = a.fmt
    if fmt == "auto":
        fmt = "wav24" if a.a.lower().endswith(".wav") else "s16"
    try:
        if fmt == "wav24":
            cha, bia, raa, pa = parse_wav(da)
            chb, bib, rab, pb = parse_wav(db)
            hdr = f"WAV {cha} ch {bia}-bit {raa} Hz"
            if (cha, bia, raa) != (chb, bib, rab):
                print(f"header differs: A {cha} ch {bia}-bit {raa} Hz, B {chb} ch {bib}-bit {rab} Hz")
                print("FAIL: header")
                return 1
            ch, bits = cha, bia
        else:
            ch, bits, pa, pb = a.channels, 16, da, db
            hdr = f"s16 x {ch}"
    except ValueError as e:
        print(f"unparseable: {e}")
        return 2
    wb = bits // 8
    fbytes = ch * wb
    fa, fb_ = len(pa) // fbytes, len(pb) // fbytes
    oa, ob = onset_frame(pa, fbytes), onset_frame(pb, fbytes)
    ons = lambda o: "none" if o is None else str(o)
    problems = []
    if abs(fa - fb_) > a.len_tol:
        problems.append(f"length {fa} vs {fb_} frames (len-tol {a.len_tol})")
    if oa != ob:
        problems.append(f"onset moved: frame {ons(oa)} vs {ons(ob)}")

    if pa == pb:
        print(f"identical ({hdr}; {fa} frames, {len(pa)} data bytes; onset frame {ons(oa)})")
        print("PASS")
        return 0

    n = min(fa, fb_) * ch
    xa = decode(pa[:n * wb], bits)
    xb = decode(pb[:n * wb], bits)
    worst = 0
    ndiff = 0
    first = None
    for i in range(n):
        d = xa[i] - xb[i]
        if d:
            ndiff += 1
            if d < 0:
                d = -d
            if d > worst:
                worst = d
            if first is None:
                first = i
    pct = 100.0 * ndiff / n if n else 0.0
    line = (f"{hdr}; frames {fa} / {fb_}; compared {n} samples; max |diff| = {worst} (tol {a.tol}); "
            f"differing {ndiff} = {pct:.4f} % (limit {a.frac:g} %); onset frame {ons(oa)} / {ons(ob)}")
    if first is not None:
        line += f"; first diff frame {first // ch} ch {first % ch} ({xa[first]} vs {xb[first]})"
    if fa != fb_:
        line += f"; trailing {abs(fa - fb_)} extra frame(s) on {'A' if fa > fb_ else 'B'} not compared"
    # shift hint: does B look like A displaced by a few frames? (over a window after the first diff)
    if first is not None:
        w0 = max(0, (first // ch - 8) * ch)
        w1 = min(n, w0 + 20000 * ch)
        def wmax(k):   # max |A[i] - B[i-k]| over the window
            lo, hi = max(w0, w0 + k), min(w1, w1 + k)
            if hi - lo < ch * 16:
                return None
            m = 0
            for i in range(lo, hi):
                d = xa[i] - xb[i - k]
                if d < 0:
                    d = -d
                if d > m:
                    m = d
            return m
        m0 = wmax(0)
        hints = []
        if m0 is not None and m0 > a.tol:     # only when the unshifted window is itself out of tolerance
            for s in (-3, -2, -1, 1, 2, 3):
                m = wmax(s * ch)
                if m is not None and m <= a.tol:
                    hints.append(f"B == A shifted {s:+d} frame(s) within tol over {(w1 - w0) // ch} frames from frame {w0 // ch}")
        if hints:
            line += "; SHIFT HINT: " + "; ".join(hints)
    print(line)
    if worst > a.tol:
        problems.append(f"max |diff| {worst} > {a.tol}")
    if pct > a.frac:
        problems.append(f"differing {pct:.4f} % > {a.frac:g} %")
    if problems:
        print("FAIL: " + "; ".join(problems))
        return 1
    print(f"PASS within tolerance ({ndiff} samples differ, max {worst} LSB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
