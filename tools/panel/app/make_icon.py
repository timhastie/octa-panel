#!/usr/bin/env python3
"""Draw the app icon as a PNG, stdlib only: a dark rounded square with a
pale LCD rectangle and a row of four key dots under it (the panel's own
colours from panel.html). build.sh turns it into an .icns with sips +
iconutil. Nothing here is a trademark.

    python3 tools/panel/app/make_icon.py out/_panel_app_build/icon.png [size=1024]
"""
import struct
import sys
import zlib


def png_rgba(w, h, rows):
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def rounded_rect_coverage(x, y, x0, y0, x1, y1, r):
    """0..1 coverage of pixel centre (x, y) by the rounded rect, with a
    one-pixel soft edge (a signed distance clamped to [-.5, .5])."""
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 - r
    return min(1.0, max(0.0, 0.5 - d))


def blend(dst, src, a):
    return tuple(int(round(d + (s - d) * a)) for d, s in zip(dst, src))


def draw(size):
    m = size * 0.10                      # the icon keeps a transparent margin like macOS icons
    body = (size * 0.185)                # corner radius of the dark square
    x0, y0, x1, y1 = m, m, size - m, size - m
    # the LCD: upper two thirds of the body, the bezel a little darker than the body
    lx0, ly0 = size * 0.22, size * 0.24
    lx1, ly1 = size * 0.78, size * 0.55
    bez = size * 0.018
    keys_y = size * 0.68
    key_r = size * 0.045
    key_xs = [size * (0.30 + i * 0.1333) for i in range(4)]

    dark, edge = (43, 43, 45), (18, 18, 19)
    bezel, lcd = (12, 12, 13), (201, 205, 196)
    key, key_red = (154, 154, 151), (192, 58, 43)
    pix = (26, 26, 26)

    rows = []
    for y in range(size):
        row = bytearray()
        yc = y + 0.5
        for x in range(size):
            xc = x + 0.5
            a = rounded_rect_coverage(xc, yc, x0, y0, x1, y1, body)
            if a <= 0.0:
                row += b"\x00\x00\x00\x00"
                continue
            # body with a slightly lighter top (a gradient like panel.html's #unit)
            t = (yc - y0) / (y1 - y0)
            col = blend(dark, (48, 48, 50), max(0.0, 0.12 - t) / 0.12) if t < 0.12 else dark
            # inner edge line
            ea = rounded_rect_coverage(xc, yc, x0 + size * 0.012, y0 + size * 0.012,
                                       x1 - size * 0.012, y1 - size * 0.012, body - size * 0.012)
            col = blend(edge, col, ea)
            # LCD bezel + glass
            ba = rounded_rect_coverage(xc, yc, lx0 - bez, ly0 - bez, lx1 + bez, ly1 + bez, size * 0.02)
            col = blend(col, bezel, ba)
            la = rounded_rect_coverage(xc, yc, lx0, ly0, lx1, ly1, size * 0.008)
            col = blend(col, lcd, la)
            # a hint of pixels: a text-like bar and a cursor block on the glass
            if la > 0.5:
                gx, gy = (xc - lx0) / (lx1 - lx0), (yc - ly0) / (ly1 - ly0)
                if 0.10 < gy < 0.22 and 0.08 < gx < 0.62 and int(gx * 40) % 2 == 0:
                    col = pix
                if 0.36 < gy < 0.48 and 0.08 < gx < 0.44 and int(gx * 40) % 2 == 0:
                    col = pix
                if 0.62 < gy < 0.86 and 0.08 < gx < 0.30:
                    col = pix
            # four keys, the first one red
            for i, kx in enumerate(key_xs):
                d = ((xc - kx) ** 2 + (yc - keys_y) ** 2) ** 0.5
                ka = min(1.0, max(0.0, key_r - d + 0.5))
                col = blend(col, key_red if i == 0 else key, ka)
            row += bytes(col) + bytes([int(round(a * 255))])
        rows.append(row)
    return png_rgba(size, size, rows)


if __name__ == "__main__":
    out = sys.argv[1]
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
    with open(out, "wb") as f:
        f.write(draw(size))
