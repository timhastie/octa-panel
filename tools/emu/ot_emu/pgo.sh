#!/bin/bash
# tools/emu/ot_emu/pgo.sh -- the profile-guided-optimisation ritual (O15d).
#
#   bash tools/emu/ot_emu/pgo.sh              # -> out/emu/ot_emu, PGO + LTO
#
# 1. instrumented build            out/emu-pgo-gen   (-DOT_PGO_GENERATE=ON)
# 2. training runs on that binary  the bench.py sequence: boot on the OTLIVE
#                                  card, frame on, PLAY, 16 x `run 250`, quit
#                                  -- once without, once with --dsp (lockstep)
#                                  and once with --dsp --dsp-rt (the panel's sound mode)
#                                  (LLVM_PROFILE_FILE -> out/emu-pgo/raw/)
# 3. llvm-profdata merge           out/emu-pgo/ot_emu.profdata
# 4. optimised build               out/emu            (-DOT_PGO_PROFILE=...)
#
# The profile is BUILD-HOST SPECIFIC: it belongs to this compiler, these
# flags and the source bytes it was collected on. After ANY source change
# under tools/emu/ot_emu or vendor/{mc68k,dsp56300} run this again; a stale
# profile only loses speed (functions whose CFG hash no longer matches get no
# weights), never correctness -- but the point of it is the speed. Nothing is
# committed: the profile lives under out/ like the binaries.
#
# Re-running is safe and is the normal use: every run makes a new profile,
# and the whole of out/emu is recompiled against it (the CMakeLists puts the
# profile's SHA-256 on every compile line, so objects from an earlier profile
# -- the vendored cores' above all -- never survive into the link; two
# profiles cannot be LTO-linked together). The instrumented tree is
# incremental (a source change rebuilds what depends on it, nothing else
# feeds it). Step 4 checks its own work: no object in out/emu may be older
# than the profile it was built with.
#
# Options:
#   --dest DIR      where the optimised build goes         (out/emu)
#   --gen DIR       the instrumented tree                  (out/emu-pgo-gen)
#   --prof DIR      profiles: raw/*.profraw, ot_emu.profdata (out/emu-pgo)
#   --card IMG      training card image     (out/_agents/port/otlive.img)
#   --image BIN     firmware image          (out/raw/section_3_MAIN_OS.bin)
#   --no-dsp        train without the cores only (the --dsp run takes ~3 min)
#   --skip-train    reuse an existing --prof/ot_emu.profdata (steps 1-3 skipped)
#   --clean-gen     delete the instrumented tree afterwards (it is kept so a
#                   different training sequence needs no rebuild)
#   -j N            build parallelism (8)
# Environment: LLVM_PROFDATA=<path> overrides the `xcrun -f llvm-profdata` lookup.
#
# Rosetta: on this Mac an Intel Homebrew lives at /usr/local, and `bash
# pgo.sh` from a PATH with /usr/local/bin ahead of /bin runs an x86_64 bash;
# every child (cmake, clang) inherits the translation and clang then targets
# x86_64 by default -- a Rosetta ot_emu, 2-3x slower and not the operator's.
# The script re-executes itself natively if it finds itself translated, and
# refuses to finish with a binary of the wrong architecture.
set -euo pipefail
if [ "$(uname -s)" = Darwin ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
	echo "pgo.sh: running under Rosetta ($(command -v bash)); re-executing natively" >&2
	exec arch -arm64 /bin/bash "$0" "$@"
fi

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
export PATH=/opt/homebrew/bin:$PATH

DEST=out/emu
GEN=out/emu-pgo-gen
PROF=out/emu-pgo
CARD=out/_agents/port/otlive.img
IMAGE=out/raw/section_3_MAIN_OS.bin
DSP=1
TRAIN=1
KEEPGEN=1
JOBS=8
while [ $# -gt 0 ]; do
	case "$1" in
		--dest) DEST=$2; shift 2;;
		--gen) GEN=$2; shift 2;;
		--prof) PROF=$2; shift 2;;
		--card) CARD=$2; shift 2;;
		--image) IMAGE=$2; shift 2;;
		--no-dsp) DSP=0; shift;;
		--skip-train) TRAIN=0; shift;;
		--clean-gen) KEEPGEN=0; shift;;
		-j) JOBS=$2; shift 2;;
		-h|--help) sed -n '2,48p' "$0"; exit 0;;
		*) echo "pgo.sh: unknown option $1" >&2; exit 2;;
	esac
done

PY=.venv/bin/python3
[ -x "$PY" ] || PY=python3
SRC=tools/emu/ot_emu
PROFDATA="$PROF/ot_emu.profdata"
say() { printf '\npgo.sh: %s  [%s]\n' "$*" "$(date +%H:%M:%S)"; }

if [ "$TRAIN" = 1 ]; then
	[ -f "$IMAGE" ] || { echo "pgo.sh: no firmware image at $IMAGE" >&2; exit 1; }
	[ -f "$CARD" ] || { echo "pgo.sh: no training card image at $CARD (--card)" >&2; exit 1; }
	if [ -n "${LLVM_PROFDATA:-}" ]; then :
	elif command -v llvm-profdata >/dev/null 2>&1; then LLVM_PROFDATA=llvm-profdata
	elif command -v xcrun >/dev/null 2>&1; then LLVM_PROFDATA="$(xcrun -f llvm-profdata)"
	else echo "pgo.sh: no llvm-profdata (set LLVM_PROFDATA=)" >&2; exit 1; fi

	say "1/4 instrumented build -> $GEN"
	cmake --fresh -B "$GEN" -S "$SRC" -DOT_PGO_GENERATE=ON
	cmake --build "$GEN" -j"$JOBS"
	if [ "$(uname -s)" = Darwin ] && [ "$(lipo -archs "$GEN/ot_emu")" != "$(uname -m)" ]; then
		echo "pgo.sh: the instrumented build is $(lipo -archs "$GEN/ot_emu") on a $(uname -m) host (Rosetta build?)" >&2; exit 1
	fi

	say "2/4 training runs (profiles -> $PROF/raw)"
	rm -rf "$PROF/raw"; mkdir -p "$PROF/raw"
	train() {	# train <tag> [--dsp]: the bench.py sequence on the instrumented binary
		local tag=$1; shift
		LLVM_PROFILE_FILE="$PROF/raw/$tag-%p.profraw" "$PY" - "$GEN/ot_emu" "$IMAGE" "$CARD" "$tag" "$@" <<'EOF'
import subprocess, sys, time
binp, image, card, tag, *extra = sys.argv[1:]
argv = [binp, "--image", image, "--card", card, "--interactive", "--mount", "--set", "OTLIVE",
        "--project", "PROJECT", "--internal-clock"] + extra
dsp = "--dsp" in extra
t0 = time.perf_counter()
p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
def cmd(c):
    p.stdin.write(c + "\n"); p.stdin.flush()
    while True:
        line = p.stdout.readline()
        if not line: raise SystemExit(f"pgo.sh: {tag}: child died at {c}")
        line = line.rstrip("\n")
        if line.startswith(("ok", "err", "audio", "status", "peek", "tx", "ready", "watch", "hits")):
            return line
while True:
    line = p.stdout.readline()
    if not line: raise SystemExit(f"pgo.sh: {tag}: child died in boot")
    if line.startswith("ready"): break
print(f"  {tag}: ready after {time.perf_counter() - t0:.1f} s: {line.strip()}", flush=True)
cmd("frame on")
if dsp: cmd("audio start main")
cmd("key 0x25 0x01"); cmd("run 60"); cmd("key 0x25 0x00"); cmd("run 100")
t = time.perf_counter()
for i in range(16):
    cmd("run 250")
    if dsp: cmd("audio read")
w = time.perf_counter() - t
print(f"  {tag}: 4000 emulated ms in {w:.2f} s wall = {4000/w:.0f} emulated ms per wall s (instrumented)", flush=True)
p.stdin.write("quit\n"); p.stdin.flush()
p.wait(timeout=60)
if p.returncode != 0: raise SystemExit(f"pgo.sh: {tag}: exit {p.returncode}")
EOF
	}
	train nodsp
	[ "$DSP" = 1 ] && train dsp --dsp
	[ "$DSP" = 1 ] && train rt --dsp --dsp-rt		# O17b: the JIT workers, the fence, the rings
	ls -l "$PROF/raw"

	say "3/4 llvm-profdata merge -> $PROFDATA"
	"$LLVM_PROFDATA" merge -output="$PROFDATA" "$PROF"/raw/*.profraw
	"$LLVM_PROFDATA" show "$PROFDATA" | sed 's/^/  /'
	[ "$KEEPGEN" = 1 ] || rm -rf "$GEN"
else
	[ -f "$PROFDATA" ] || { echo "pgo.sh: --skip-train but no $PROFDATA" >&2; exit 1; }
	say "1-3/4 skipped: reusing $PROFDATA"
fi

say "4/4 optimised build (PGO + LTO) -> $DEST"
# No `rm -rf "$DEST"`: the frozen reference binary lives beside the build in
# out/emu. --fresh clears the cache only; the recompile of every object
# against the new profile is the CMakeLists' job (the profile's SHA-256 is on
# every compile line), checked below.
cmake --fresh -B "$DEST" -S "$SRC" -DOT_PGO_PROFILE="$ROOT/$PROFDATA"
cmake --build "$DEST" -j"$JOBS"

# Sanity: every object in the tree must postdate the profile -- one compiled
# against an earlier profile would already have failed the LTO link
# ("linking module flags 'ProfileSummary': IDs have conflicting values"),
# and this catches a tree that got there without linking.
stale=$(find "$DEST" -name '*.o' ! -newer "$PROFDATA" | wc -l | tr -d ' ')
nobj=$(find "$DEST" -name '*.o' | wc -l | tr -d ' ')
if [ "$stale" != 0 ]; then
	echo "pgo.sh: $stale of $nobj objects in $DEST are older than $PROFDATA (not rebuilt against it)" >&2; exit 1
fi
echo "pgo.sh: all $nobj objects in $DEST postdate $PROFDATA"

# Sanity: the operator's binary must be the optimised one, never the
# instrumented one (an instrumented ot_emu carries __llvm_prf_* sections and
# runs ~2x slower).
if otool -l "$DEST/ot_emu" 2>/dev/null | grep -q __llvm_prf; then
	echo "pgo.sh: $DEST/ot_emu is INSTRUMENTED -- refusing to leave it there" >&2; exit 1
fi
if [ "$(uname -s)" = Darwin ] && [ "$(lipo -archs "$DEST/ot_emu")" != "$(uname -m)" ]; then
	echo "pgo.sh: $DEST/ot_emu is $(lipo -archs "$DEST/ot_emu") on a $(uname -m) host (Rosetta build?)" >&2; exit 1
fi
say "done: $DEST/ot_emu ($(stat -f %z "$DEST/ot_emu") bytes) built with $PROFDATA"
