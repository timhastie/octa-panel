#!/usr/bin/env python3
"""Compare two stamps.txt (drive.py): `<step> <ms> <sample> <frames> <stop>`
per `run`. Stop words and step list must match exactly; sample stamps within
--tol samples, frame counts within --ftol. Default 0/0 = byte-strict.
Prints the max deviations either way."""
import argparse
import sys

ap = argparse.ArgumentParser()
ap.add_argument("a")
ap.add_argument("b")
ap.add_argument("--tol", type=float, default=0.0)
ap.add_argument("--ftol", type=int, default=0)
a = ap.parse_args()
try:
    la = open(a.a).read().split()
    lb = open(a.b).read().split()
except OSError as e:
    print(f"missing: {e}")
    sys.exit(2)
ra = [la[i:i + 5] for i in range(0, len(la), 5)]
rb = [lb[i:i + 5] for i in range(0, len(lb), 5)]
if len(ra) != len(rb):
    print(f"run count differs: {len(ra)} vs {len(rb)}")
    sys.exit(1)
worst_s = worst_f = 0.0
first = None
for i, (x, y) in enumerate(zip(ra, rb)):
    if x[0] != y[0] or x[1] != y[1] or x[4] != y[4]:
        print(f"run #{i + 1}: {x} vs {y} (step/ms/stop differ)")
        sys.exit(1)
    ds = abs(float(x[2]) - float(y[2]))
    df = abs(int(x[3]) - int(y[3]))
    worst_s = max(worst_s, ds)
    worst_f = max(worst_f, df)
    if first is None and (ds > a.tol or df > a.ftol):
        first = (i + 1, x, y)
print(f"{len(ra)} runs; max |dsample| = {worst_s:g}, max |dframes| = {worst_f:g} (tol {a.tol:g} / {a.ftol})")
if first:
    print(f"first out of tolerance: run #{first[0]} {first[1]} vs {first[2]}")
    sys.exit(1)
