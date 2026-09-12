#!/usr/bin/env python3
"""Compare two text files after normalising the run-specific bits the oracle
documents as legitimately different (output directories, binary paths).

    cmp_text.py A B [--strip DIR_A DIR_B] [--strip-re REGEX]...
prints `identical (<n> lines)` and exits 0, or `line <k>: <a> | <b>` for the
FIRST differing line and exits 1. Missing file -> exit 2."""
import argparse
import re
import sys

ap = argparse.ArgumentParser()
ap.add_argument("a")
ap.add_argument("b")
ap.add_argument("--strip", nargs=2, action="append", default=[], help="path prefix pair replaced by <OUT>")
ap.add_argument("--strip-re", action="append", default=[], help="regex removed from both sides")
a = ap.parse_args()


def load(path, prefixes):
    try:
        lines = open(path, errors="replace").read().split("\n")
    except OSError as e:
        print(f"missing: {e}")
        sys.exit(2)
    out = []
    for l in lines:
        for pre in prefixes:
            l = l.replace(pre, "<OUT>")
        for r in a.strip_re:
            l = re.sub(r, "", l)
        out.append(l)
    return out


la = load(a.a, [p[0] for p in a.strip] + [p[1] for p in a.strip])
lb = load(a.b, [p[1] for p in a.strip] + [p[0] for p in a.strip])
n = max(len(la), len(lb))
for i in range(n):
    x = la[i] if i < len(la) else "<EOF>"
    y = lb[i] if i < len(lb) else "<EOF>"
    if x != y:
        print(f"line {i + 1}: {x[:160]!r} | {y[:160]!r}")
        sys.exit(1)
print(f"identical ({len(la)} lines)")
