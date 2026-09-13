// The two DSP56300 cores behind the ColdFire's host port -- milestone O8.
//
// WHAT THE WINDOW IS. `0x20000000`-`0x20000fff` is the HI08 HOST-SIDE
// register file of whichever core the GPIO byte at `0xfc0a400c` selects,
// one byte register per 4-byte stride, in the LOW byte of a 16-bit access:
//
//   +0x00  ICR   interrupt control   (RREQ 0, TREQ 1, HF0 3, HF1 4, INIT 7)
//   +0x04  CVR   command vector      (HV 6:0, HC 7)
//   +0x08  ISR   interrupt status    (RXDF 0, TXDE 1, TRDY 2, HF2 3, HF3 4, HREQ 7)
//   +0x0c  IVR   interrupt vector
//   +0x14  TXH / RXH   bits 23:16 of the 24-bit word
//   +0x18  TXM / RXM   bits 15:8
//   +0x1c  TXL / RXL   bits 7:0 -- the write that SENDS, the read that TAKES
//
// ✅ Every one of those comes from the firmware's own code, not the manual
// (docs/firmware/COLDFIRE_PORT.md, O8): the loader at `0x40001d4c` writes 0 to +0x04,
// spins on `+0x08 & 6` (TXDE|TRDY) before every word, writes the three lanes
// high-mid-low, and the payload uploader at `0x40001b18` spins on `+0x08 & 1`
// (RXDF) and reads +0x14/+0x18/+0x1c back for the DSP's echo. The frame
// handler at `0x4000aad0` writes `0x8c` to +0x04 (HC | vector 0x0c) and polls
// bit 7 until the DSP takes the host command. And `0x81` to +0x00 -- read for
// months as "start the DSP" -- is ICR INIT|RREQ: an interface reset.
//
// THE DSP SIDE is the vendored dsp56kEmu, exactly as `tools/harness/dsp_host` builds
// it (two cores, X/Y 0x30000-0x3ffff of core 1 aliased onto core 0's arrays),
// plus its `DspBoot`: the emulation of the chip's HI08 bootstrap ROM (count,
// address, words, jump) that the firmware's 50- and 58-word uploads are
// written for. Once the ROM has jumped, host words go to the real HDI08 and
// the firmware's own bootstrap code echoes them back -- the far side of the
// handshake O6 had to fake.
//
// TIMING. The cores are stepped in lockstep with the ColdFire: `ratio` DSP
// instructions per ColdFire instruction during the boot (which has no sample
// clock), `ips` instructions per sample once the RTOS runs. Both are knobs.
// `ips` defaults to 4160 = 512 x 8.125: the payload never writes PCTL, so the
// core runs at the DSP56720's reset PLL (0x2B60C2: NF/(NR*NO) = 195/24) from
// an EXTAL the payload itself makes the audio clock (P:0x30024 routes EXTAL
// into both ESAI chains, TPSR=1/TPM=0/TFP=0, 8 slots x 32 bits), so
// Fsys/fs = 512 x 8.125 whatever the crystal is (COLDFIRE_PORT.md, O8:
// "the ESAI rate"). The ESAI fires ONE SLOT per `ips / 8` instructions -- the
// vendored clock's "cycles per sample" is per slot (its "2 samples = 1
// frame" comment). ⚠️ A core whose bootstrap ROM has not finished is HELD
// (the ROM jumps only after the last word), and a core is never run past the
// due count, so the ColdFire's polls see the DSP make progress between them
// and not before.
#pragma once

#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include <pthread.h>

#include "machine.h"

namespace dsp56k
{
	class Memory;
	class Peripherals56362;
	class Peripherals56367;
	class DSP;
	class DspBoot;
	class HDI08;
	class IMemoryValidator;
}

namespace ot
{
	class DspPair final : public Coprocessor
	{
	public:
		static constexpr uint32_t g_window = 0x20000000, g_windowEnd = 0x20001000;
		static constexpr uint32_t g_select = 0xfc0a400c;
		static constexpr uint32_t g_shareLo = 0x30000, g_shareHi = 0x40000;
		static constexpr uint32_t g_pSize = 0x80000;			// the P memory each core is built with
		bool faulted(int _core) const;

		// `_ratio`: DSP instructions per ColdFire instruction (boot clock);
		// `_ips`: DSP instructions per sample (RTOS clock). 4160 is the
		// firmware's own arithmetic (see the file comment; ❌ 4535 was the
		// datasheet's 200 MIPS ceiling, not this board's clock); the ratio is
		// that against the port's 3990 ColdFire instructions per sample.
		// Neither is a measurement of either emulator's cadence.
		static constexpr double g_dspIps = 4160.0;
		static constexpr double g_cfIps = 3990.0;
		static constexpr uint32_t g_esaiSlots = 8;		// TDC = 7 in both payload TCCRs
		// O17 (12 Sep 2026): `_rt` = the REAL-TIME MODE (--dsp-rt). The two
		// cores run under the vendored JIT, each on a worker thread, ON THE
		// LOCKSTEP SCHEDULE: the ColdFire stays the master of emulated time
		// -- its ticks book the due count exactly as O16c has it -- and at
		// every touch point (sync at burst ends and exact steps, host-port
		// read/write, the eDMA's push/pull/ring gate, the idle skip, the
		// probes) the due count is POSTED to the workers, which run their
		// core to it and never past it. So the ordering the firmware's frame
		// protocol depends on (the DSP never ahead of the ColdFire's clock,
		// a pull runs the core until the word is there, the bank id is in
		// HOTX before the ColdFire reads it) is lockstep's by construction,
		// while the ColdFire's bursts and the cores' chunks overlap in wall
		// time. What moves against the lockstep interpreter: the ColdFire
		// sees the bank-word edge at the burst end after it (the worker
		// raises an atomic flag the burst loop ends on), the two cores
		// interleave by wall time within a bounded skew instead of per
		// instruction, and a core polling the host port is fast-forwarded
		// to its next peripheral event (the interpreter's idle step, JIT
		// edition). The audio contract for this mode is FUNCTIONAL, not
		// byte-identical (docs/firmware/COLDFIRE_PORT.md O17); every other
		// mode is untouched (each rt branch is behind m_rt).
		DspPair(double _ratio = g_dspIps / g_cfIps, double _ips = g_dspIps, bool _rt = false);
		~DspPair() override;
		bool rt() const { return m_rt; }
		bool rtOk() const { return m_rtOk; }				// the mode could be set up (MMU alias, JIT, threads)
		const std::string& rtWhy() const { return m_rtWhy; }	// ... or why not
		std::string rtStatus();								// one line: MIPS per core, worker CPU, frames, waits, edges, faults (dsp.cpp)
		bool realtime() const override { return m_rt; }
		bool edgePending() const override { return m_rt && m_edgePending.load(std::memory_order_relaxed) != 0; }

		bool read(uint32_t _addr, uint8_t _size, uint32_t& _out) override;
		bool write(uint32_t _addr, uint8_t _size, uint32_t _val) override;
		void tickInstructions(uint64_t _n) override;
		double tickSamples(double _n) override;
		void setHostWordHook(std::function<bool(int)> _f) override { m_hostWordHook = std::move(_f); m_hookArmed.store(static_cast<bool>(m_hostWordHook), std::memory_order_release); }
		int selected() const override { return m_sel; }
		bool hostRingEmpty(int _core) const override;
		void pushHalfwords(uint32_t _addr, const std::vector<uint16_t>& _hw) override;
		size_t pullHalfwords(uint32_t _addr, int _core, std::vector<uint16_t>& _out, size_t _n) override;
		std::string blockNote(int _core) override;
		uint32_t peekWord(int _core, char _space, uint32_t _addr) const override;
		uint64_t pulled(int _core) const;
		uint64_t pullShort(int _core) const;

		// -- probes ----------------------------------------------------------
		uint32_t peekP(int _core, uint32_t _addr) const;
		uint32_t peekX(int _core, uint32_t _addr) const;
		uint32_t peekY(int _core, uint32_t _addr) const;
		uint32_t pc(int _core) const;
		bool bootFinished(int _core) const;
		uint64_t executed(int _core) const;
		uint64_t hostWordsIn(int _core) const;		// words the host sent (ROM + HDI08)
		uint64_t hostWordsOut(int _core) const;		// words the host took back
		uint64_t hostCommands(int _core) const;
		// ESAI frames put out between consecutive host commands (0x8c): the
		// falsifier for the pacing. Sixteen per frame is what the ring's
		// double buffer needs; anything else and one bank is never taken.
		uint64_t framesPerCommandMin(int _core) const;
		uint64_t framesPerCommandMax(int _core) const;
		uint64_t framesPerCommandSixteen(int _core) const;
		uint32_t bootLength(int _core) const;		// what the ROM was told
		uint32_t bootAddress(int _core) const;
		// Every host-side event, in order, when enabled: "sel", "icr", "cvr",
		// "tx", "rx", "hc-taken". Cheap enough to leave on for a boot.
		struct Event { uint64_t due; int core; char kind[8]; uint32_t val; };
		void setLog(bool _on) { m_logOn = _on; }
		const std::vector<Event>& log() const { return m_log; }
		std::string report() const;
		// A line per core every `_every` executed instructions: the PC, the
		// DSP's own instruction counter, the ESAI frame counters and the ESAI /
		// HDI08 status registers -- what to read when a core stops making
		// progress and the question is when it stopped.
		void setTrace(uint64_t _every) { m_traceEvery = _every; refreshInstrumented(); }
		void setTraceFrom(uint64_t _executed) { m_traceFrom = _executed; refreshInstrumented(); }	// O9b: a window, so the cap holds the END of a run
		// The idle fast-forward: a core found in a poll loop (the last PCs in
		// a window of three words, outside any hardware DO loop) is advanced
		// to its next peripheral event instead of executing the polls. What it
		// polls -- the host port, the mailbox, memory -- can only change when
		// the ColdFire or the other core runs, and both keep running. Default
		// on; `setIdleSkip(false)` for a fidelity check.
		void setIdleSkip(bool _on) { m_idleSkip = _on; }
		// The vendored library's own log lines (ESAI register writes, underruns).
		static void setVerbose(bool _on);
		uint64_t idleSkipped(int _core) const;
		// The inter-core mailbox: words core 0 sent core 1 and back.
		uint64_t mailboxWords(int _from) const;
		const std::vector<std::string>& trace() const { return m_trace; }

		// -- audio (O9) -------------------------------------------------------
		// The X-side ESAI's eight slots, TX0 out and RX0 in: the only audio
		// port either payload sets up (payload A, P:0x30024, DMA2 out of the
		// X:0x8000 ring and DMA3 into X:0x8100; payload B configures none, and
		// core 1 reports 0 ESAI frames). ESAI_1 is configured alongside but
		// no DMA feeds it. Which physical input or output each slot is has
		// NOT been measured -- see docs/firmware/COLDFIRE_PORT.md O9.
		static constexpr uint32_t g_audioSlots = 8;
		// Keep every transmitted frame (8 words, slot order) for a WAV.
		void setAudioCapture(const bool _on) { m_capture = _on; }
		const std::vector<int32_t>& audioOut(int _core) const;
		// -- audio over the pipe (12 Sep 2026, --interactive `audio ...`) ----
		// A second, BOUNDED capture of core 0's TX0, beside the batch one
		// above (which is one-shot, unbounded, written at exit -- and never
		// under --interactive, which returns before the reports). The same
		// sink, the same de-rotated ring words: `Main` keeps words 2/3 (the
		// main L/R pair, measured 12 Sep: run3_core0.wav slots 2/3 = the
		// fixture sample x 0.70 at level 64), `Cue` words 4/5 (the second
		// pair, 3.1 dB lower), `All` the eight. Kept as 16-bit (the 24-bit
		// word >> 8: the pipe never carries more) in a ring of
		// g_streamCapFrames frames; a client takes frames out as it goes,
		// and when it does not the OLDEST are overwritten and counted.
		enum class StreamMode : uint8_t { Off, Main, Cue, All };
		static constexpr uint32_t g_streamCapFrames = 60 * 44100;	// 60 s: 10.6 MB for a pair, 42.3 MB for all eight
		static uint32_t streamWords(const StreamMode _m) { return _m == StreamMode::All ? g_audioSlots : _m == StreamMode::Off ? 0 : 2; }
		void setAudioStream(StreamMode _mode);		// Off stops and frees; any other mode (re)starts empty
		StreamMode audioStream() const { return m_streamMode; }
		// Move up to _maxFrames pending frames (interleaved, streamWords() each) onto _out; returns the frames moved.
		size_t takeAudioStream(std::vector<int16_t>& _out, size_t _maxFrames);
		struct StreamStatus { StreamMode mode; uint64_t captured, pending, dropped; };
		StreamStatus streamStatus() const;		// O17: locked in the rt mode (the sink runs on core 0's thread)
		// Feed RX0 from the transport start (the first 0x8c) on: `_channels`
		// interleaved channels onto slots 0..channels-1, silence past the end
		// -- or the built-in tones (slot k = a sine at 500 x (k+1) Hz, -20 dBFS).
		void setAudioInput(std::vector<int32_t> _interleaved, uint32_t _channels);
		void setAudioTones(const bool _on) { m_tones = _on; }
		// O14: feed the input from the DSP's first receive frame instead of the
		// first 0x8c. The default (from the first command) arms a step-1
		// recorder against a DSP->host pipeline still holding pre-start silence,
		// which the recorder then keeps as the buffer's first ~96 samples --
		// RTOS_FORK 10.43-10.46's "retrigger gap". Hardware's pipeline holds
		// live input at that moment; this option reproduces that condition.
		void setAudioInputFromBoot(const bool _on) { m_inputFromBoot = _on; }
		uint64_t txAtFirstCommand(int _core) const;
		uint64_t rxAtFirstCommand(int _core) const;
		uint64_t txSlotNonZero(int _core, uint32_t _slot) const;
		uint64_t rxSlotNonZero(int _core, uint32_t _slot) const;
		// The DSP's own instruction counter (which clocks its ESAI) minus the
		// interpreter calls the budget used to count -- `rep` iterations, mostly.
		uint64_t counterSurplus(int _core) const;
		uint64_t zeroDeltaCalls(int _core) const;
		// The activity map: at every frame command (0x8c) on core 0, the count
		// of non-zero words in each 4K chunk of X and Y of BOTH cores (the
		// shared window included) -- one line per command. Where audio (or
		// anything) lives on the DSP, frame by frame, without knowing where
		// to look first.
		void setActivityMap(const bool _on) { m_mapOn = _on; }
		const std::vector<std::string>& activityMap() const { return m_map; }
		// The write map: every NON-ZERO data write either core makes, binned
		// by 256-word region of X and Y, flushed as one line per frame
		// command. A region written with content is where audio (or state)
		// passes through, whatever the buffer is called.
		void setWriteMap(const bool _on) { m_writesOn = _on; }
		// A write watch on one DSP word: the last 16 (pc, value, executed) that wrote it (O9b).
		void setWriteWatch(const int _core, const char _space, const uint32_t _addr) { m_watchCore = _core; m_watchSpace = _space; m_watchAddr = _addr; m_watchOn = true; }
		struct WatchHit { uint32_t pc, val; uint64_t executed; uint32_t last[4]; uint32_t ddr0, dco0, dsr1, dco1, r4, r6, area, r0; };
		const std::vector<WatchHit>& writeWatchHits() const { return m_watchHits; }
		// A PC watch on one core: registers at every arrival (last 24), the DSP side of route A's --watch-pc (O9b).
		struct PcWatchHit { uint64_t executed; uint32_t a1, a0, b1, b0, x0, x1, y0, y1, r0, r4, r6, n4, sp, r2, m2, r1, n1, r7; };
		void setPcWatch(const int _core, const uint32_t _pc, const uint64_t _from = 0) { m_pcWatchCore = _core; m_pcWatchPc = _pc; m_pcWatchFrom = _from; m_pcWatchOn = true; refreshInstrumented(); }
		// O12 cycle meter: instructions executed between two PCs on one core, per
		// arrival pair (the dispatcher's head to its exit = one frame's DSP work,
		// in the same unit as dsp_host's meter). Spins outside the pair (the host
		// wait, the bank wait) are not counted, which is what "busy" failed to do.
		struct Stopwatch { int core = -1; uint32_t start = 0, stop = 0; uint64_t t0 = 0; bool armed = false; uint64_t n = 0, sum = 0, max = 0, min = ~0ull; std::vector<uint32_t> last; };
		void setStopwatch(const int _core, const uint32_t _start, const uint32_t _stop) { m_sw.core = _core; m_sw.start = _start; m_sw.stop = _stop; refreshInstrumented(); }
		const Stopwatch& stopwatch() const { return m_sw; }
		const std::vector<PcWatchHit>& pcWatchHits() const { return m_pcWatchHits; }
		const std::vector<std::string>& writeMap() const { return m_writeMap; }

		// Run both cores up to the due count now (the ticks only book it),
		// interleaved in quanta of g_quantum instructions (O9b).
		static double g_quantum;		// O12: --dsp-quantum N (default 64); huge = each core runs its whole due span in turn
		void runDue();
		bool stepCore(int _i, double _limit);
		// O16c: LAZY BATCHING (proposal E2). With `_n` = 0 (the default, and
		// the boot's and the batch's schedule) every tick runs the cores to
		// the due count at once: the O12 interleave, bit for bit. With `_n`
		// > 0 a tick only BOOKS the due count, and the cores run in one
		// chunk -- runDue over the whole backlog, the quantum interleave
		// inside it -- at the points where the ColdFire can observe or
		// affect them: sync() (the run loop, before tickTimers()/deliver()
		// at every burst end and exact step), any host-port access (read /
		// write / the eDMA's push and pull / the ring gate), tickSamples (the
		// idle skip, one sample at a time as before), every probe, and when
		// the backlog reaches `_n` instructions inside a burst. The
		// ColdFire's own clock and the ESAI clock (one slot per ips/8 DSP
		// instructions) are unchanged: only WHEN the pair's work is done
		// moves, by at most one burst or `_n`, whichever is less -- the
		// frame edge (the bank word) is seen at the end of the burst it
		// fell in instead of at the instruction. That is the Phase B audio
		// contract (O16a) at most -- and with the chunk replaying the
		// tick sequence and the edge guard (below) it is byte-identical in
		// practice (the O16c gate). `--dsp-lazy N` in main.cpp; the default
		// in every mode is g_lazyDefault.
		static constexpr double g_lazyDefault = 4160.0;		// one sample
		void setLazy(const double _n) { if(m_rt) return; m_lazy = _n > 0.0 ? _n : 0.0; m_edgeGuard = 1e300; m_edgeWindowEnd = m_lazy > 0.0 ? 0.0 : 1e300; }	// a first prediction at the first lazy runDue; the rt mode has its own schedule
		double lazy() const { return m_lazy; }
		void sync() override { if(m_rt) rtSync(); else catchUp(); }
		void catchUp(bool _observe = false);		// run the backlog now (a no-op when there is none, and always when exact); O17: `_observe` = the caller reads the cores' state (a host-port read, the drain gate): the rt mode brings them to the count
		void runChunk();	// the backlog, replayed tick by tick (dsp.cpp)
		// O16b: the pair's own counters -- runDue calls and passes, stepCore
		// wrapper calls, interpreter steps and idle steps per core. Free to
		// keep (one add each); printed on stderr at exit with OT_DSP_STATS=1.
		// O16c adds the lazy side: host-port reads/writes, chunks (runDue
		// calls that had a backlog) with the sum and max of the backlog in
		// DSP instructions, and the frame edge's lateness -- at the bank
		// write, how far the ColdFire's booked count was ahead of core 0.
		struct Stats { uint64_t runDue = 0, passes = 0, stepCalls = 0, interp[2] = {0, 0}, idleSteps[2] = {0, 0};
			uint64_t hostReads = 0, hostWrites = 0, syncs = 0, chunks = 0, edges = 0, edgesIdle = 0, edgesLate = 0, guardTicks = 0, predictions = 0;
			double chunkSum = 0.0, chunkMax = 0.0, edgeLateSum = 0.0, edgeLateMax = 0.0; };
		const Stats& stats() const { return m_stats; }
		// Run ONE core until `_ready` or `_budget` instructions (the read-back
		// needs the DSP to produce each word). Returns whether it became ready.
		bool runCoreUntil(int _core, const std::function<bool()>& _ready, uint64_t _budget);

	private:
		struct Core;
		Core& cur() { return *m_cores[m_sel & 1]; }
		// -- O17: the real-time mode (dsp.cpp, "THE REAL-TIME MODE") ------------
		// One worker per core. The ColdFire thread writes the first line
		// (the posted target), the worker the second (what it reached);
		// the two never share a line, so a post costs the worker one miss
		// and a publish costs the ColdFire one, only when it looks.
		struct RtCore
		{
			alignas(128) std::atomic<int> pred{0};			// ColdFire -> worker: 1: keep running past the target until HOTX holds a word (the read-back pull)
			std::atomic<bool> kick{false};					// a host command is in the core's external queue: move it in before the next block
			std::atomic<bool> parked{false};				// the worker sleeps on `cv` (a post must wake it)
			std::atomic<uint64_t> offset{0};				// executed = counter + offset (the due count when the boot ROM jumped)
			alignas(128) std::atomic<uint64_t> reached{0};	// worker -> ColdFire: the counter, published every g_rtPublish instructions and when idle
			std::atomic<bool> idle{false};					// nothing to run: at the target, the predicate met, the skip's edge, a fault
			std::atomic<uint32_t> idleGen{0};				// the post generation the worker went idle for
			std::atomic<uint64_t> parks{0};
			std::atomic<bool> faulted{false};
			std::atomic<uint32_t> pc{0};					// the last block-entry PC (a status field, published with the MIPS)
			std::atomic<uint64_t> executedInstr{0}, skipped{0}, blocks{0}, skewWaits{0}, skewTimeouts{0}, edgeStops{0};
			std::atomic<double> mips{0.0}, xmips{0.0}, cpuS{0.0}, busy{0.0};
			alignas(128) std::mutex mx;
			std::condition_variable cv;
			pthread_t thread{};
			bool started = false;
		};
		static constexpr uint64_t g_rtPublish = 256;		// the worker publishes its counter at least this often (instructions)
		// The posted due count, ONE line for both workers (each subtracts its
		// offset): a post is one store and one generation bump.
		alignas(128) std::atomic<uint64_t> m_rtDue{0};
		std::atomic<uint32_t> m_rtGen{0};					// bumped by every post; a worker echoes it in idleGen when it stops for it
		std::atomic<bool> m_rtParked{false};				// some worker is parked (a post looks at the two flags only then)
		bool m_rt = false, m_rtOk = false;
		std::string m_rtWhy;
		int m_shmFd = -1;						// the shared window's backing store: one 256 KB object, six views (2 cores x P/X/Y)
		uint32_t* m_sharedPtr = nullptr;		// core 0's X view of it (every view is the same memory)
		RtCore m_rtc[2];
		std::atomic<bool> m_stop{false};
		std::atomic<bool> m_bootDone[2]{{false}, {false}};
		std::atomic<bool> m_hookArmed{false};
		std::atomic<int> m_pullCore{-1};		// the core a read-back pull is running past the due count; the other follows it
		// the bank-word edge (core 0, P:0x73): raised on the worker, applied on the ColdFire thread
		std::atomic<uint32_t> m_edgePending{0};
		std::atomic<uint64_t> m_edgeExec{0};	// executed (counter + offset) at the last edge
		std::atomic<bool> m_bankTakePending{false};
		// the idle skip (tickSamples): the workers stop at the first edge inside it
		std::atomic<bool> m_skipArmed{false}, m_skipEdge{false};
		// knobs (OT_RT_*, read once at setup; dsp.cpp says what each is)
		uint64_t m_rtLagMax = 4160, m_rtLead = 8320, m_rtSkew = 512, m_rtPostQuantum = 32, m_rtReadQuantum = 32, m_rtCheckQuantum = 512, m_rtTickPost = 512;	// m_rtLead: how far the cores may run AHEAD of the due count (the file comment in dsp.cpp: the DSP timeline is unchanged by it, the ColdFire sees the edge that much earlier)
		double m_rtSpinUs = 20.0;
		bool m_rtFastForward = true, m_rtGuard = false, m_rtReadWait = false;
		uint32_t m_rtDoIter = 0;
		// ColdFire-side bookkeeping
		uint64_t m_rtPosted = 0, m_rtChecked = 0, m_rtTicks = 0, m_rtAtPosted = ~0ull;	// m_rtAtPosted: the posted count the cores were last seen at (an observing touch point's cache)
		uint64_t m_rtPosts = 0, m_rtWakes = 0, m_rtWaits = 0, m_rtWaitTimeouts = 0, m_rtSkipWaits = 0, m_rtEdgesApplied = 0, m_rtEdgesInPull = 0, m_rtGuardTicks = 0, m_rtGuardWindows = 0;
		double m_rtWaitS = 0.0, m_rtSkipWaitS = 0.0, m_rtEdgeLateSum = 0.0, m_rtEdgeLateMax = 0.0;
		uint64_t m_rtEdgeLateN = 0, m_rtIcrTreqDropped = 0, m_rtSkipEdges = 0, m_rtPulls = 0;
		uint64_t m_gateCalls = 0, m_gateFalse = 0, m_gateBlocks = 0;	// the eDMA's drain gate (hostRingEmpty), both modes: a diagnostic on the OT_DSP_STATS line
		double m_gateSpanSum = 0.0, m_gateOpenAt = -1.0, m_gateLateSum = 0.0;
		double m_rtPullS = 0.0;
		std::vector<std::string> m_rtShortLog;	// the first read-back words not in time, with both workers' state (a diagnostic in rtstatus)
		bool m_rtTraceOn = false; std::vector<std::string> m_rtTrace; uint64_t m_rtTraceCtr0 = 0;	// OT_RT_TRACE=1: core 0's blocks after an eDMA push (stderr at exit)
		double m_rtSkipEdgeLateSum = 0.0, m_rtSkipEdgeLateMax = 0.0;
		double rtTickSamples(double _n);		// the idle skip: whole samples, ended at the first edge inside it
		std::atomic<uint64_t> m_edgesRaised{0}, m_edgesRaisedInPull{0};
		mutable std::mutex m_streamMx;
		std::mutex m_logMx;
		bool rtSetup();							// the MMU alias, the JIT configuration, the threads
		void rtWorker(int _i);					// the per-core thread
		void rtSkewWait(int _i, uint64_t _myExec);	// worker: hold this core within m_rtSkew of the other
		void rtPost(uint64_t _quantum);			// ColdFire: publish the due count to the workers (when it moved by _quantum; 0 = always)
		void rtWake(int _i);
		bool rtWait(uint64_t _slack, int _core = -1);	// ColdFire: hold until both cores (or `_core`) are within _slack of the posted count (false: a core faulted / timed out)
		void rtCatchUp(bool _observe);			// the touch point: post and bound the lag; observing = post the exact count and wait for it
		void rtSync();							// the run loop's sync: edges, then the touch point
		void rtApplyEdges(bool _inSkip = false);	// ColdFire: the host-word hook for every pending edge (the lateness kept apart for a skip's)
		void rtPredictEdge();					// the O16c prediction on the workers' published grid (the guard experiment)
		void rtRefresh();						// ColdFire: executed / idleSkipped / faulted from the workers' published counters
		void rtFault(int _i, const char* _why);
		void noteFrom(int _core, const char* _kind, uint32_t _val);
		// O16b: the per-instruction body (stepCore minus its runnable checks)
		// and its cold parts. `m_instrumented` is the one flag the hot path
		// tests for the stopwatch, the PC watch and the trace; the PC ring
		// and the TIMER0 capture are always on (the fault report and the
		// batch report print them).
		bool stepBody(Core& c, int i, double _limit);
		bool faultPc(Core& c, uint32_t _pc, double _limit) __attribute__((noinline));
		void timerCapture(Core& c) __attribute__((noinline));
		void instrumentBefore(Core& c, int i, uint32_t pc) __attribute__((noinline));
		void traceLine(Core& c, int i, uint32_t pc) __attribute__((noinline));
		void refreshInstrumented() { m_instrumented = m_traceEvery != 0 || m_pcWatchOn || m_sw.core == 0 || m_sw.core == 1; }
		bool m_instrumented = false;
		Stats m_stats;
		void note(const char* _kind, uint32_t _val);
		void icrWrite(uint32_t _v);
		void cvrWrite(uint32_t _v);
		uint32_t isrRead();
		void sendWord(uint32_t _word);
		uint32_t rxPeek();
		uint32_t rxTake();

		std::vector<std::unique_ptr<Core>> m_cores;
		// THE SHARED WINDOW, ONE MEMORY: P, X and Y of both cores at
		// 0x30000-0x3ffff (CHIP.md: measured on hardware; and the firmware
		// needs it -- core 1's entry P:0x38000 is written by core 0's upload).
		std::vector<uint32_t> m_shared;
		int m_sel = 0;
		double m_ratio, m_ips;
		double m_due = 0.0;
		double m_ranDue = 0.0;		// O16c: the due count the cores were last run to (== m_due when exact)
		uint64_t m_pendingTicks = 0;	// O16c: ticks booked since then (each one `+= m_ratio`; runChunk replays them)
		double m_lazy = 0.0;		// O16c: the backlog, in DSP instructions, that forces a chunk inside a burst; 0 = exact
		bool m_inSamples = false;	// O16c: inside tickSamples (an edge there is the idle skip's, not the batching's)
		// O16c: THE EDGE GUARD. The one event the DSP raises on its own that
		// the ColdFire must see at the instruction -- the bank word (P:0x73,
		// the frame edge) -- is predictable: the dispatcher writes it when
		// DMA2's source pointer (DSR2) equals 0x8070 or 0x80f0, a one-slot
		// window, and DSR2 advances one word per ESAI slot exec, which the
		// vendored clock fires every esaiCyclesPerSlot instructions from its
		// m_lastClock. So from DSR2 and the counter at the last frame
		// callback (a slot exec) the boundary slot is known, and the pair
		// ticks EXACTLY (runDue per ColdFire instruction, the pre-O16c
		// schedule) from three slots before it until one slot after or
		// until the write fires; lazily elsewhere. `predictEdge` sets the
		// window in due units; stepBody's bank-write path reports an edge
		// outside a window as `edgesLate` (a misprediction, measurable).
		double m_edgeGuard = 1e300, m_edgeWindowEnd = 1e300;
		bool m_edgeSeen = false;
		uint64_t m_edgeSlot = 0;		// the slot counter the last prediction was made from
		bool m_edgeLog = false;
		void predictEdge();
		bool m_logOn = false;
		uint64_t m_traceEvery = 0, m_traceFrom = 0;
		bool m_idleSkip = true;
		bool m_capture = false, m_tones = false, m_mapOn = false, m_writesOn = false;
		// The pipe's ring (setAudioStream): g_streamCapFrames x streamWords() int16, oldest at m_streamHead
		StreamMode m_streamMode = StreamMode::Off;
		std::vector<int16_t> m_stream;
		size_t m_streamHead = 0, m_streamCount = 0;		// frames
		uint64_t m_streamCaptured = 0, m_streamDropped = 0;	// frames since `audio start`
		void streamPush(const int32_t* _words);			// one de-rotated frame of eight 24-bit words
		std::atomic<bool> m_pulling{false};	// O17: read on the worker (a bank write inside a pull is counted)
		bool m_pcWatchOn = false; int m_pcWatchCore = 0; uint32_t m_pcWatchPc = 0; uint64_t m_pcWatchFrom = 0;	// with a `from`, the FIRST 24 arrivals after it are kept
		std::vector<PcWatchHit> m_pcWatchHits;
		Stopwatch m_sw;
		bool m_watchOn = false; int m_watchCore = 0; char m_watchSpace = 'X'; uint32_t m_watchAddr = 0;
		std::vector<WatchHit> m_watchHits;			// inside pullHalfwords: host-port words are the read-back, not the bank id
		bool m_hostWordFired = false;	// set by runDue when the hook fired; tickSamples stops its slice on it
		std::function<bool(int)> m_hostWordHook;
		std::vector<std::string> m_map, m_writeMap;
		std::vector<int32_t> m_input;
		uint32_t m_inputChannels = 0;
		bool m_inputFromBoot = false;
		// THE INTER-CORE MAILBOX, one register each way. 🟡 Inferred from the
		// firmware's use, not from a datasheet: core A writes Y:$FFFFD7 and
		// waits while bit 1 of Y:$FFFFD6 is set; core B waits for bit 1 of
		// Y:$FFFFD3 and reads Y:$FFFFD4 (docs/firmware/COLDFIRE_PORT.md, O8). Modelled
		// symmetrically: $D7 = my transmit data, $D6 bit 1 = it is still
		// unread; $D4 = my receive data, $D3 bit 1 = one is waiting.
		struct Mailbox { uint32_t data = 0; std::atomic<bool> full{false}; uint64_t words = 0; };	// O17: `full` crosses the two worker threads (data before full, release/acquire)
		Mailbox m_mail[2];		// m_mail[k]: written by core k, read by core k^1
		std::vector<std::string> m_trace;
		std::vector<Event> m_log;
	};
}
