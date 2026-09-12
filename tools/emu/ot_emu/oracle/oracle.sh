#!/bin/bash
# THE REGRESSION ORACLE for tools/emu/ot_emu: run a fixed battery on two
# binaries (a frozen reference and a candidate) and diff what the firmware
# did. Written 12 Sep 2026 for the speed work (COLDFIRE_PORT.md O15a-O15f),
# moved into the repo with audio tolerances for Phase B (O16a).
#
#   tools/emu/ot_emu/oracle/oracle.sh [REF] CAND
#       [--build-dir DIR] [--fresh] [--tag NAME] [--skip render,interdsp,...]
#       [--stamp-tol SAMPLES] [--frame-tol N]
#       [--audio-tol LSB16] [--wav-tol LSB24] [--audio-frac PERCENT]
#       [--ref BIN] [--image BIN] [--card IMG] [--card2 IMG] [--set NAME]
#       [--project NAME] [--out DIR]
#
# Inputs (flag, or the environment variable, or the default under out/):
#   reference  --ref      OT_ORACLE_REF      out/emu/ot_emu.ref-73c2815
#                                            (a first positional overrides both)
#   image      --image    OT_ORACLE_IMAGE    out/raw/section_3_MAIN_OS.bin
#   card       --card     OT_ORACLE_CARD     out/_agents/port/otlive.img   (card, inter, interdsp)
#   card2      --card2    OT_ORACLE_CARD2    out/_agents/audio/otlive2.img (render)
#   set        --set      OT_ORACLE_SET      OTLIVE
#   project    --project  OT_ORACLE_PROJECT  PROJECT
#   cache+reports --out   OT_ORACLE_OUT      out/_oracle   (runs/<sha>/, reports/)
#   python     -          OT_ORACLE_PY       .venv/bin/python3 (else python3)
# None of these are in git: the firmware image and the card fixtures stay
# under out/ (CONTEXT.md: no Elektron bytes in the repo).
#
# The battery (every job in parallel, each with its own timeout):
#   stock     the ctest rtos gate through the CLI: stock image, --ms 1000,
#             --serial-out + --golden  -> boot log, UART A/B bytes, golden JSON
#   card      the O14i standard card batch (card, set/project, --ms 1000,
#             --serial-out + --golden) -> the same three
#   render    the O14k reference render (card2, --dsp --sequencer
#             --internal-clock --poke-trig 5 --main-level 64 --frames 3000
#             --pre-roll 200 --audio-out) -> run3_core0.wav + log
#   inter     --interactive on card, --rtc 1000000000, the drive.py key
#             sequence -> ready line, boot log, replies, UART A stream, peeks,
#             run stamps
#   interdsp  the same with --dsp, `audio start main` before PLAY, `audio read`
#             after every run -> audio.pcm, plus everything above
#   ctest     7/7 on --build-dir (the candidate's build tree), if given
#
# Tolerances (all default 0 = byte-strict):
#   --stamp-tol S   `run` sample stamps may differ by S samples (cmp_stamps.py)
#   --frame-tol N   `run` frame counts may differ by N; the audio captures may
#                   differ in LENGTH by N frames (trailing frames not compared)
#   --audio-tol L   interdsp.pcm (s16): max |diff| <= L LSB        (cmp_audio.py)
#   --wav-tol L     render.wav (24-bit words): max |diff| <= L
#   --audio-frac P  at most P percent of the compared samples may differ
#   Alignment is never tolerated: the onset frame (first non-zero sample)
#   must be identical on both sides. Everything else (logs, serial, goldens,
#   UART stream, peeks, replies) is always byte-strict.
# Phase B contract (O16a): --audio-tol 2 --wav-tol 8 --audio-frac 0.5
# --frame-tol 1, against BOTH references (see phase_b.sh).
#
# Every check prints PASS/FAIL with the first differing offset/line. Exit 0
# only if all PASS. Outputs are cached per binary sha256 under
# <out>/runs/<sha>/<job>/ (the interactive jobs also keyed on drive.py's
# sha; every job on its inputs' size+mtime); --fresh reruns. Reference ==
# candidate (same bytes) always reruns the candidate side into
# runs/<sha>-b/: that is the determinism check.
#
# Legitimately NOT byte-identical, hence stripped or fixed: `status wall=`
# and `status idle=` (drive.py's wall.txt: reported, never compared),
# output-file paths in the batch logs (normalised to <OUT>), the binary's
# own path (<EMU>), and the RTC: the batch default is --rtc off (loopback),
# --interactive here pins --rtc 1000000000 so the SET DATE/TIME dialog and
# the clock record are the same on every machine and run. ctest's timing
# lines are not compared.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
PY="${OT_ORACLE_PY:-$ROOT/.venv/bin/python3}"
[ -x "$PY" ] || PY=python3
export PATH=/opt/homebrew/bin:$PATH

REF="${OT_ORACLE_REF:-$ROOT/out/emu/ot_emu.ref-73c2815}"
IMG="${OT_ORACLE_IMAGE:-$ROOT/out/raw/section_3_MAIN_OS.bin}"
CARD="${OT_ORACLE_CARD:-$ROOT/out/_agents/port/otlive.img}"
CARD2="${OT_ORACLE_CARD2:-$ROOT/out/_agents/audio/otlive2.img}"
SET="${OT_ORACLE_SET:-OTLIVE}"
PROJECT="${OT_ORACLE_PROJECT:-PROJECT}"
OUT="${OT_ORACLE_OUT:-$ROOT/out/_oracle}"

POS=(); BUILD=""; FRESH=0; STOL=0; FTOL=0; ATOL=0; WTOL=0; AFRAC=0; TAG=""; SKIP=","
while [ $# -gt 0 ]; do
	case "$1" in
		--build-dir) BUILD="$2"; shift 2;;
		--fresh) FRESH=1; shift;;
		--stamp-tol) STOL="$2"; shift 2;;
		--frame-tol) FTOL="$2"; shift 2;;
		--audio-tol) ATOL="$2"; shift 2;;
		--wav-tol) WTOL="$2"; shift 2;;
		--audio-frac) AFRAC="$2"; shift 2;;
		--tag) TAG="$2"; shift 2;;
		--skip) SKIP=",$2,"; shift 2;;
		--ref) REF="$2"; shift 2;;
		--image) IMG="$2"; shift 2;;
		--card) CARD="$2"; shift 2;;
		--card2) CARD2="$2"; shift 2;;
		--set) SET="$2"; shift 2;;
		--project) PROJECT="$2"; shift 2;;
		--out) OUT="$2"; shift 2;;
		-h|--help) sed -n 2,68p "$0"; exit 0;;
		--*) echo "unknown option $1"; exit 2;;
		*) POS+=("$1"); shift;;
	esac
done
case ${#POS[@]} in
	1) CAND="${POS[0]}";;
	2) REF="${POS[0]}"; CAND="${POS[1]}";;
	*) echo "usage: oracle.sh [REF] CAND [options]  (--help for the list)"; exit 2;;
esac
for f in "$REF" "$CAND" "$IMG" "$CARD" "$CARD2"; do
	[ -e "$f" ] || { echo "missing: $f"; exit 2; }
done
abs() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }
REF="$(abs "$REF")"; CAND="$(abs "$CAND")"; IMG="$(abs "$IMG")"; CARD="$(abs "$CARD")"; CARD2="$(abs "$CARD2")"
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
[ -n "$BUILD" ] && BUILD="$(cd "$BUILD" && pwd)"

sha() { shasum -a 256 "$1" | cut -c1-12; }
fp() { stat -f '%N %z %m' "$1"; }    # a cheap input fingerprint: path, size, mtime
RSHA="$(sha "$REF")"; CSHA="$(sha "$CAND")"
RDIR="$OUT/runs/$RSHA"; CDIR="$OUT/runs/$CSHA"
SAME=0
if [ "$RSHA" = "$CSHA" ]; then SAME=1; CDIR="$OUT/runs/$CSHA-b"; fi
mkdir -p "$RDIR" "$CDIR" "$OUT/reports"
STAMP="$(date +%Y%m%d-%H%M%S)"
REPORT="$OUT/reports/$STAMP${TAG:+-$TAG}.txt"
T_START=$(date +%s)

say() { echo "$@" | tee -a "$REPORT"; }
say "oracle.sh $STAMP"
say "reference : $REF  (sha $RSHA) -> $RDIR"
say "candidate : $CAND  (sha $CSHA)$( [ $SAME = 1 ] && echo '  SAME BYTES: determinism run' ) -> $CDIR"
say "inputs    : image $IMG; card $CARD; card2 $CARD2; set $SET; project $PROJECT"
[ -n "$BUILD" ] && say "build dir : $BUILD (ctest)"
say "tolerance : stamps $STOL samples, frames $FTOL; audio pcm $ATOL LSB, wav $WTOL LSB, at most $AFRAC % of samples; alignment strict"

# ---- the jobs ---------------------------------------------------------------
PIDS=""
cleanup() {
	for p in $PIDS; do kill -TERM "$p" 2>/dev/null; done
	sleep 0.2
	for p in $PIDS; do kill -KILL "$p" 2>/dev/null; done
}
trap 'cleanup; exit 130' INT TERM
trap 'cleanup' EXIT

skipped() { case "$SKIP" in *,"$1",*) return 0;; esac; return 1; }

# job NAME DIR BIN FORCE -- runs (in the background) unless DIR/NAME/DONE says 0
job() {
	local name="$1" dir="$2" bin="$3" force="$4"
	local d="$dir/$name"
	skipped "$name" && return 0
	# the cache key beside the binary: the inputs this job reads (and, for the
	# interactive checks, the driver that produced them)
	local key
	case "$name" in
		stock) key="$(fp "$IMG")";;
		render) key="$(fp "$IMG"); $(fp "$CARD2"); $SET $PROJECT";;
		card) key="$(fp "$IMG"); $(fp "$CARD"); $SET $PROJECT";;
		inter|interdsp) key="$(fp "$IMG"); $(fp "$CARD"); $SET $PROJECT; drive $(sha "$HERE/drive.py")";;
	esac
	if [ "$force" = 0 ] && [ "$FRESH" = 0 ] && [ "$(cat "$d/DONE" 2>/dev/null)" = "0" ] && [ "$(cat "$d/inputs.txt" 2>/dev/null)" = "$key" ]; then
		echo "  $name: cached in $d"
		return 0
	fi
	rm -rf "$d"; mkdir -p "$d"
	echo "$key" > "$d/inputs.txt"
	case "$name" in
		stock)
			( "$PY" "$HERE/tmo.py" 600 "$d/stdout.txt" -- "$bin" --image "$IMG" --ms 1000 \
				--serial-out "$d/serial" --golden "$d/golden.json"; echo $? > "$d/DONE" ) & ;;
		card)
			( "$PY" "$HERE/tmo.py" 900 "$d/stdout.txt" -- "$bin" --image "$IMG" --card "$CARD" --mount \
				--set "$SET" --project "$PROJECT" --ms 1000 --serial-out "$d/serial" --golden "$d/golden.json"; echo $? > "$d/DONE" ) & ;;
		render)
			( "$PY" "$HERE/tmo.py" 1200 "$d/stdout.txt" -- "$bin" --image "$IMG" --card "$CARD2" --mount \
				--set "$SET" --project "$PROJECT" --dsp --sequencer --internal-clock --poke-trig 5 --main-level 64 \
				--audio-out "$d/run3" --frames 3000 --pre-roll 200; echo $? > "$d/DONE" ) & ;;
		inter)
			( "$PY" "$HERE/drive.py" --emu "$bin" --out "$d" --image "$IMG" --card "$CARD" --set "$SET" --project "$PROJECT" \
				--timeout 900 >"$d/drive.out" 2>&1 ) & ;;
		interdsp)
			( "$PY" "$HERE/drive.py" --emu "$bin" --out "$d" --image "$IMG" --card "$CARD" --set "$SET" --project "$PROJECT" \
				--dsp --timeout 1200 >"$d/drive.out" 2>&1 ) & ;;
	esac
	PIDS="$PIDS $!"
	echo "  $name: started pid $! -> $d"
}

say "--- launching (reference)"
for j in stock card render inter interdsp; do job $j "$RDIR" "$REF" 0; done
say "--- launching (candidate)"
for j in stock card render inter interdsp; do job $j "$CDIR" "$CAND" $SAME; done
CTEST_OUT=""
if [ -n "$BUILD" ] && ! skipped ctest; then
	CTEST_OUT="$CDIR/ctest.txt"
	( cd "$BUILD" && "$PY" "$HERE/tmo.py" 600 "$CTEST_OUT" -- ctest --output-on-failure ) &
	PIDS="$PIDS $!"
	echo "  ctest: started pid $! -> $CTEST_OUT"
fi

# wait for every job (each has its own timeout)
for p in $PIDS; do wait "$p" 2>/dev/null; done
PIDS=""
say "--- all jobs finished after $(( $(date +%s) - T_START )) s wall"

# ---- the comparisons ----------------------------------------------------------
NPASS=0; NFAIL=0
verdict() {   # verdict NAME RC DETAIL
	if [ "$2" = 0 ]; then NPASS=$((NPASS+1)); say "PASS  $1  $3"; else NFAIL=$((NFAIL+1)); say "FAIL  $1  $3"; fi
}
cmp_bin() {   # cmp_bin LABEL A B
	local out rc
	if [ ! -f "$2" ] || [ ! -f "$3" ]; then verdict "$1" 1 "missing file ($2 / $3)"; return; fi
	out="$(cmp "$2" "$3" 2>&1)"; rc=$?
	if [ $rc = 0 ]; then verdict "$1" 0 "byte-identical ($(stat -f %z "$2") bytes)"
	else verdict "$1" 1 "$(echo "$out" | head -1 | sed "s|$2|A|;s|$3|B|")  (sizes $(stat -f %z "$2") / $(stat -f %z "$3"))"; fi
}
cmp_txt() {   # cmp_txt LABEL A B [extra args]
	local out rc l="$1" a="$2" b="$3"; shift 3
	out="$("$PY" "$HERE/cmp_text.py" "$a" "$b" --strip "$RDIR" "$CDIR" --strip "$REF" "$CAND" "$@" 2>&1)"; rc=$?
	verdict "$l" $rc "$out"
}
cmp_audio() { # cmp_audio LABEL A B FMT TOL -> cmp_audio.py with the run's tolerances; the detail line + verdict
	local l="$1" a="$2" b="$3" fmt="$4" tol="$5" rc
	if [ ! -f "$a" ] || [ ! -f "$b" ]; then verdict "$l" 1 "missing file ($a / $b)"; return; fi
	"$PY" "$HERE/cmp_audio.py" "$a" "$b" --fmt "$fmt" --tol "$tol" --frac "$AFRAC" --len-tol "$FTOL" > "$b.cmp.txt" 2>&1; rc=$?
	verdict "$l" $rc "$(tr '\n' ' ' < "$b.cmp.txt")"
}
done_ok() {   # done_ok DIR NAME -> checks both DONE files say 0
	local a="$RDIR/$2/DONE" b="$CDIR/$2/DONE"
	local ra="$(cat "$a" 2>/dev/null)" rb="$(cat "$b" 2>/dev/null)"
	if [ "$ra" = 0 ] && [ "$rb" = 0 ]; then return 0; fi
	verdict "$2.exit" 1 "reference exit '${ra:-none}', candidate exit '${rb:-none}' (124 = timeout; see $CDIR/$2/)"
	return 1
}
walls() {     # walls NAME FILE -> "ref Xs / cand Ys"
	local a="$RDIR/$1/$2" b="$CDIR/$1/$2"
	echo "wall ref $(cut -d' ' -f1 "$a" 2>/dev/null)s / cand $(cut -d' ' -f1 "$b" 2>/dev/null)s"
}

for name in stock card; do
	skipped $name && continue
	say "--- $name ($(walls $name stdout.txt.wall))"
	done_ok x $name || continue
	cmp_txt "$name.log" "$RDIR/$name/stdout.txt" "$CDIR/$name/stdout.txt"
	cmp_bin "$name.serial_a" "$RDIR/$name/serial.a" "$CDIR/$name/serial.a"
	cmp_bin "$name.serial_b" "$RDIR/$name/serial.b" "$CDIR/$name/serial.b"
	cmp_bin "$name.golden" "$RDIR/$name/golden.json" "$CDIR/$name/golden.json"
	"$PY" "$ROOT/tools/emu/ot_emu/oracle.py" "$RDIR/$name/golden.json" "$CDIR/$name/golden.json" > "$CDIR/$name/oracle_py.txt" 2>&1; rc=$?
	verdict "$name.oracle_py" $rc "$(tail -1 "$CDIR/$name/oracle_py.txt")"
done

if ! skipped render; then
	say "--- render ($(walls render stdout.txt.wall))"
	if done_ok x render; then
		cmp_audio "render.wav" "$RDIR/render/run3_core0.wav" "$CDIR/render/run3_core0.wav" wav24 "$WTOL"
		cmp_txt "render.log" "$RDIR/render/stdout.txt" "$CDIR/render/stdout.txt"
	fi
fi

for name in inter interdsp; do
	skipped $name && continue
	say "--- $name (boot $(grep '^boot' "$RDIR/$name/wall.txt" 2>/dev/null | cut -d' ' -f2)s / $(grep '^boot' "$CDIR/$name/wall.txt" 2>/dev/null | cut -d' ' -f2)s; run $(grep '^run' "$RDIR/$name/wall.txt" 2>/dev/null | cut -d' ' -f2)s / $(grep '^run' "$CDIR/$name/wall.txt" 2>/dev/null | cut -d' ' -f2)s wall, ref / cand)"
	done_ok x $name || { tail -2 "$CDIR/$name/wall.txt" 2>/dev/null | sed 's/^/      /' | tee -a "$REPORT"; continue; }
	cmp_txt "$name.ready" "$RDIR/$name/ready.txt" "$CDIR/$name/ready.txt"
	cmp_txt "$name.bootlog" "$RDIR/$name/boot.log" "$CDIR/$name/boot.log"
	cmp_txt "$name.replies" "$RDIR/$name/steps.txt" "$CDIR/$name/steps.txt"
	cmp_bin "$name.uart" "$RDIR/$name/tx.bin" "$CDIR/$name/tx.bin"
	cmp_txt "$name.uart_per_step" "$RDIR/$name/txlen.txt" "$CDIR/$name/txlen.txt"
	cmp_txt "$name.peeks" "$RDIR/$name/peeks.txt" "$CDIR/$name/peeks.txt"
	"$PY" "$HERE/cmp_stamps.py" "$RDIR/$name/stamps.txt" "$CDIR/$name/stamps.txt" --tol "$STOL" --ftol "$FTOL" > "$CDIR/$name/stamps_cmp.txt" 2>&1; rc=$?
	verdict "$name.stamps" $rc "$(tr '\n' ' ' < "$CDIR/$name/stamps_cmp.txt")"
	[ $name = interdsp ] && cmp_audio "$name.pcm" "$RDIR/$name/audio.pcm" "$CDIR/$name/audio.pcm" s16 "$ATOL"
done

if [ -n "$CTEST_OUT" ]; then
	say "--- ctest ($BUILD, $(cut -d' ' -f1 "$CTEST_OUT.wall" 2>/dev/null)s wall)"
	if grep -q "^100% tests passed" "$CTEST_OUT" 2>/dev/null && grep -q "tests passed out of 7" "$CTEST_OUT"; then
		verdict ctest 0 "$(grep 'tests passed' "$CTEST_OUT")"
	else
		verdict ctest 1 "$(grep -E 'tests passed|Failed|TIMEOUT|No tests' "$CTEST_OUT" "$CTEST_OUT.stderr" 2>/dev/null | head -3 | tr '\n' ' ')"
	fi
fi

say "=== $NPASS PASS, $NFAIL FAIL; $(( $(date +%s) - T_START )) s wall; report $REPORT"
[ $NFAIL = 0 ]
