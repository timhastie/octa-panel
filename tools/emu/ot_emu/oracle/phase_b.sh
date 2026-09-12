#!/bin/bash
# The Phase B gate (COLDFIRE_PORT.md O16a): the candidate against BOTH frozen
# references with the Phase B audio contract, everything else byte-strict.
#
#   tools/emu/ot_emu/oracle/phase_b.sh CAND [oracle.sh options, e.g. --build-dir DIR --tag NAME]
#
# Runs oracle.sh twice -- against out/emu/ot_emu.ref-1e76ac5 (the pre-speed
# binary) and out/emu/ot_emu.ref-73c2815 (Phase A's PGO binary at HEAD
# 73c2815) -- with --audio-tol 2 --wav-tol 8 --audio-frac 0.5 --frame-tol 1.
# The candidate's jobs run once (the second pass hits the cache and only
# compares). Exit 0 only if both passes are all-PASS. Override the
# references with OT_ORACLE_REF_A / OT_ORACLE_REF_B.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
REF_A="${OT_ORACLE_REF_A:-$ROOT/out/emu/ot_emu.ref-1e76ac5}"
REF_B="${OT_ORACLE_REF_B:-$ROOT/out/emu/ot_emu.ref-73c2815}"
[ $# -ge 1 ] || { echo "usage: phase_b.sh CAND [oracle.sh options]"; exit 2; }
CAND="$1"; shift
TOL=(--audio-tol 2 --wav-tol 8 --audio-frac 0.5 --frame-tol 1)
rc=0
for ref in "$REF_A" "$REF_B"; do
	echo "===== phase B gate vs $(basename "$ref")"
	"$HERE/oracle.sh" "$ref" "$CAND" "${TOL[@]}" "$@" || rc=1
done
[ $rc = 0 ] && echo "===== phase B gate: PASS against both references" || echo "===== phase B gate: FAIL (see the reports above)"
exit $rc
