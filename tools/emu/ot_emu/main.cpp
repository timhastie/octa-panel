// ot_emu -- drive the headless Octatrack machine from the command line.
//
//   out/emu/ot_emu --image out/raw/section_3_MAIN_OS.bin [--max N] [--periph]
//
// MILESTONE O1: boot to the RTOS handoff. Route A is the oracle -- it reaches
// `trap #0` and reports the same peripheral touches -- so the useful output
// here is (a) where this stopped and (b) what it touched on the way, both
// directly comparable with `tools/emu/emu_rtos.py` / `emu_bringup.boot`.
//
// Expect it to stop on an unimplemented opcode long before the handoff: the
// vendored Musashi is ColdFire V2 and this firmware is V4e. That report is
// the work list for the ISA half of the port, one opcode at a time.
#include <cstdio>
#include <chrono>
#include <iostream>
#include <map>
#include <memory>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <cerrno>
#include <fstream>
#include <string>
#include <vector>
#include <algorithm>
#include <utility>
#include <string>
#include <functional>
#include <poll.h>		// O15f: the paced child sleeps inside poll() on stdin

#include "machine.h"
#include "rtos.h"
#include "dsp.h"

#include <mach/mach.h>
#include <pthread.h>
#include <thread>
#include <atomic>
#include <unordered_map>
#include <dlfcn.h>
#include "wav.h"

namespace
{
	std::vector<uint8_t> readFile(const std::string& _path)
	{
		std::ifstream f(_path, std::ios::binary);
		if(!f)
			return {};
		return std::vector<uint8_t>(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
	}

	// -- --interactive: the machine as a server on stdin/stdout (11 Sep 2026)
	//
	// After the ordinary boot (and load, when asked) the process prints ONE
	// line `ready sample=<double> frames=<u64>` and then serves commands, one
	// per line, one reply line per command, flushed:
	//
	//   run <ms>          -> ok sample=<double> frames=<u64> stop=<word>
	//   key <row> <mask>  -> ok            two bytes into UART A's receive queue
	//   knob <row> <delta>-> ok            row, then delta & 0xff (signed detent delta)
	//   tx                -> tx <hex>      UART A's transmit bytes since the last tx
	//   peek <addr> <len> -> peek <hex>    len <= 4096; unmapped -> err
	//   poke <addr> <hex> -> ok
	//   frame on|off      -> ok            Rtos::setFrame
	//   status            -> status sample= ms= frames= frame=on|off idle= wall=
	//   quit              -> ok            then exit 0
	//
	// The card (O19, 13 Sep 2026; needs --card, else `err no card`):
	//
	//   card status       -> card ok rw=0|1 path=<file> written=<sectors the firmware wrote>
	//                             through=<sectors pwrite()n to the file> errors=<short writes>
	//   card flush        -> card ok rw=0|1 through=<n> errors=<n>   fsync of the image file
	//                             (a no-op without --card-rw: `rw=0`). With --card-rw every
	//                             committed sector is already in the file before its WRITE
	//                             completes; the flush is for a client that wants it on disk
	//                             now. `quit`, EOF and the destructor fsync as well.
	//
	// Instruments over the pipe (12 Sep 2026, the trig-LED investigation:
	// KEYMAP.md "the trigs-fired note"), the batch's --watch-pc / --watch-mem
	// with the report on demand instead of at exit:
	//
	//   watch <addr>[,<addr>...] -> ok   Machine::watchPc (replaces the list; `watch off` clears it)
	//   hits              -> hits n=<count> <rec> ... one record per PC hit since the last `hits`:
	//                        <instr>:<pc>:<d0>:<d1>:<a0>:<a1>:<sp>:<stack0>:<stack1>:<stack2>:<stack3>:<stack4>:<d2>..<d7>:<a2>..<a6>
	//                        (hex, no 0x; stack0 is the return address at a routine's entry, stack1.. its arguments)
	//   watchmem <addr> <len> -> ok      Rtos::watchMem (adds a range; nothing removes one)
	//   writes            -> writes n=<count> <rec> ... one per watched write since the last `writes`:
	//                        <sample>:<pc>:<addr>:<val>:<size>:<tcb>
	//
	// The hit log is the machine's (capped at 2M records, as the batch); a
	// watched PC costs one compare per instruction, `watch off` when done.
	//
	// Audio over the pipe (12 Sep 2026; needs --dsp, else `err`). Core 0's
	// ESAI TX0 frames, by de-rotated RING WORD (dsp.cpp's sink): words 2/3
	// are the main L/R pair, 4/5 the second pair 3.1 dB lower, 0/1/6/7 zero
	// (measured on the batch's run3_core0.wav, out/_agents/audio). Under
	// --interactive the batch's --audio-out never writes (the loop returns
	// before the reports), and the gain table 0x80003c60 stayed zero because
	// --main-level was posted only inside --sequencer -- so every voice was
	// silent (O9b's trap). Now --main-level defaults to 64 here (main()).
	//
	//   audio start [main|cue|all] -> ok   DspPair::setAudioStream: main = words 2/3 as L,R (the default),
	//                                      cue = 4/5, all = the eight words per frame; restarts empty if on
	//   audio read [<maxframes>]   -> audio <frames> <hex>   everything captured since the previous read
	//                                      (at most <maxframes>): little-endian signed 16-bit interleaved
	//                                      PCM, one L,R (or eight words) per frame, the 24-bit words >> 8;
	//                                      those frames are released. Never blocks: it answers what is there.
	//   audio status               -> audio status on=0|1 mode=off|main|cue|all captured=<frames since start>
	//                                      pending=<frames unread> rate=44100 dropped=<frames overwritten> cap=<ring frames>
	//   audio stop                 -> ok   frees the ring
	//
	// The ring holds the last DspPair::g_streamCapFrames (60 s); `run`
	// without a read past that overwrites the oldest and counts them in
	// `dropped`. A 25 ms `run` is ~1100 frames = 4.4 KB = 8.8 KB of hex.
	//
	// Real-time pacing (O15f, 12 Sep 2026; both opt-in, the old commands
	// byte-for-byte as before -- the oracle drives fixed `run`s):
	//
	//   pace on [rate]    -> ok   FREE-RUNNING: while no command line is pending on
	//                             stdin the child advances emulated time in 10 ms
	//                             slices (`run 10`) so that it tracks its own wall
	//                             clock x rate (1.0 = hardware rate): a slice ahead
	//                             -> it sleeps INSIDE poll() on stdin (a command
	//                             wakes it at once), behind -> flat out, more than
	//                             250 ms behind -> it re-anchors (counted). A pending
	//                             line is served between slices, one reply per
	//                             command, exactly as unpaced; a `run` meanwhile
	//                             advances on top of the pacer (the pacer then
	//                             waits for the wall clock). A fault/illegal stop
	//                             ends the free run (`pacestatus stop=` says so).
	//   pace off          -> ok
	//   pacestatus        -> pacestatus on=0|1 rate=<r> ratio=<r> lag=<ms> slices=<n> reanchors=<n>
	//                             slept=<s> busy=<s> stop=<word> ms=<emulated ms>
	//                             ratio = emulated ms per wall s over the last >= 1 s
	//                             window / 1000 (0 until a window closed); lag = how far
	//                             emulated time is behind its wall target now (0 when
	//                             ahead); slept = wall s inside poll(); busy = wall s
	//                             inside slices; stop = the last slice's Stop word.
	//   run <ms> wall <seconds> -> ok sample= frames= stop=wall|time|...
	//                             the plain run, ALSO ended when the wall budget is
	//                             spent: the deadline is asked at every burst end
	//                             (Rtos::Changes::OnEvent), at most 4096 instructions
	//                             apart (Machine::instructions()); where the run ends
	//                             moves no firmware event (timers, frames and the
	//                             panel UART advance by sample count). `stop=wall`
	//                             when the budget ended it. A script that needs
	//                             `run` to advance exactly <ms> must not pass `wall`.
	//
	// `err <message>` for anything else -- an empty line, an unknown word,
	// the wrong number of arguments (`tx`, `status` and `quit` take none) --
	// and the loop keeps serving. Integer arguments are decimal or 0x..
	// (`08` is eight: no octal; `key 0x26 08` was `err usage` under strtoull
	// base 0 and `010` meant 8 -- fixed 11 Sep 2026); `run`'s ms is any
	// non-negative decimal (`50`, `50.0`, `1e+03` as the panel's `:g`
	// prints it); hex payloads are lowercase, no spaces. `run` is
	// the only command that costs emulated time; nothing else touches the
	// run loop, so a client is never left waiting between commands. `wall`
	// in `status` is the wall-clock seconds spent INSIDE `run` since ready
	// (pipe latency and the client's own time excluded), which is the
	// number a speed measurement wants. `stop` is the Rtos::Stop word --
	// `time` is the ordinary case; `fault`/`illegal` mean the machine is
	// stopped and every later `run` will say so again.
	//
	// This is the panel's protocol (tools/panel/panel_server.py drives route
	// A the same way: `uart64.rx.extend([row, mask])`, `run(ms=50)`), so the
	// server can drive this port instead of the Python emulator without a
	// change of contract. The batch behaviour above and below is untouched:
	// the loop starts where the batch reports would, and `quit` returns
	// before them. The one thing --interactive changes about the machine is
	// the RTC's default (`--rtc`, below): the batch keeps the DSPI loopback.
	bool parseNumber(const std::string& _s, uint64_t& _out)
	{
		// Decimal, or 0x/0X hex. A leading zero is NOT octal.
		size_t i = 0;
		uint64_t base = 10;
		if(_s.size() > 2 && _s[0] == '0' && (_s[1] == 'x' || _s[1] == 'X'))
		{
			i = 2;
			base = 16;
		}
		if(i >= _s.size())
			return false;
		uint64_t v = 0;
		for(; i < _s.size(); ++i)
		{
			const auto c = _s[i];
			uint64_t d;
			if(c >= '0' && c <= '9') d = c - '0';
			else if(base == 16 && c >= 'a' && c <= 'f') d = c - 'a' + 10;
			else if(base == 16 && c >= 'A' && c <= 'F') d = c - 'A' + 10;
			else return false;
			if(v > (UINT64_MAX - d) / base)
				return false;
			v = v * base + d;
		}
		_out = v;
		return true;
	}

	bool parseSigned(const std::string& _s, int64_t& _out)
	{
		// An optional sign, then parseNumber's decimal-or-0x.
		bool neg = false;
		size_t i = 0;
		if(!_s.empty() && (_s[0] == '-' || _s[0] == '+'))
		{
			neg = _s[0] == '-';
			i = 1;
		}
		uint64_t v = 0;
		if(!parseNumber(_s.substr(i), v) || v > (1ull << 62))
			return false;
		_out = neg ? -static_cast<int64_t>(v) : static_cast<int64_t>(v);
		return true;
	}

	bool parseHex(const std::string& _s, std::vector<uint8_t>& _out)
	{
		if(_s.empty() || _s.size() % 2)
			return false;
		_out.clear();
		for(size_t i = 0; i < _s.size(); i += 2)
		{
			unsigned v = 0;
			for(size_t k = 0; k < 2; ++k)
			{
				const auto c = static_cast<unsigned char>(_s[i + k]);
				v <<= 4;
				if(c >= '0' && c <= '9') v |= c - '0';
				else if(c >= 'a' && c <= 'f') v |= c - 'a' + 10;
				else if(c >= 'A' && c <= 'F') v |= c - 'A' + 10;
				else return false;
			}
			_out.push_back(static_cast<uint8_t>(v));
		}
		return true;
	}

	std::vector<std::string> splitWords(const std::string& _line)
	{
		std::vector<std::string> words;
		std::string cur;
		for(const char c : _line)
		{
			if(c == ' ' || c == '\t' || c == '\r')
			{
				if(!cur.empty()) { words.push_back(cur); cur.clear(); }
			}
			else
				cur += c;
		}
		if(!cur.empty())
			words.push_back(cur);
		return words;
	}

	const char* stopWord(const ot::Rtos::Stop _s)
	{
		switch(_s)
		{
		case ot::Rtos::Stop::Gate:    return "gate";
		case ot::Rtos::Stop::Time:    return "time";
		case ot::Rtos::Stop::Fault:   return "fault";
		case ot::Rtos::Stop::Illegal: return "illegal";
		}
		return "?";
	}

	// O17 diagnostic: OT_SELFPROF=<hz> -- a sampler thread suspends the main
	// thread <hz> times a second, reads its program counter (Mach thread
	// state) and, at exit, prints the busiest addresses on stderr with the
	// image's load address (symbolize with `atos -o <binary> -l <load>`). The
	// macOS `sample` tool never returned on this process (12 Sep 2026).
	struct SelfProfiler
	{
		std::thread thread;
		std::atomic<bool> stop{false};
		mach_port_t target = MACH_PORT_NULL;
		std::unordered_map<uint64_t, uint32_t> hist;
		uint64_t samples = 0;
		void start(const double _hz)
		{
			target = pthread_mach_thread_np(pthread_self());
			thread = std::thread([this, _hz]
			{
				const auto period = std::chrono::duration<double>(1.0 / _hz);
				while(!stop.load(std::memory_order_relaxed))
				{
					std::this_thread::sleep_for(period);
					if(thread_suspend(target) != KERN_SUCCESS)
						continue;
#if defined(__aarch64__)
					arm_thread_state64_t st;
					mach_msg_type_number_t n = ARM_THREAD_STATE64_COUNT;
					if(thread_get_state(target, ARM_THREAD_STATE64, reinterpret_cast<thread_state_t>(&st), &n) == KERN_SUCCESS)
					{
						const uint64_t pc = arm_thread_state64_get_pc(st);
						++hist[pc & ~0x3ull];
						++samples;
					}
#endif
					thread_resume(target);
				}
			});
		}
		void finish()
		{
			if(!thread.joinable())
				return;
			stop.store(true);
			thread.join();
			std::vector<std::pair<uint32_t, uint64_t>> top;
			for(const auto& [pc, n] : hist)
				top.emplace_back(n, pc);
			std::sort(top.begin(), top.end(), [](const auto& a, const auto& b) { return a.first > b.first; });
			Dl_info info{};
			dladdr(reinterpret_cast<void*>(&serveInteractiveMarker), &info);
			std::fprintf(stderr, "selfprof: %llu samples, image %s at %p\n", static_cast<unsigned long long>(samples), info.dli_fname ? info.dli_fname : "?", info.dli_fbase);
			for(size_t i = 0; i < top.size() && i < 60; ++i)
			{
				Dl_info fi{};
				dladdr(reinterpret_cast<void*>(top[i].second), &fi);
				std::fprintf(stderr, "selfprof: %6u %5.1f%% 0x%llx %s+%llu\n", top[i].first, 100.0 * top[i].first / static_cast<double>(samples), static_cast<unsigned long long>(top[i].second),
					fi.dli_sname ? fi.dli_sname : "?", fi.dli_saddr ? static_cast<unsigned long long>(top[i].second - reinterpret_cast<uint64_t>(fi.dli_saddr)) : 0ull);
			}
		}
		static void serveInteractiveMarker() {}
	};

	int serveInteractive(ot::Machine& _m, ot::Rtos& _rtos, ot::DspPair* _dsp, ot::AtaCard* _card)
	{
		SelfProfiler prof;
		if(const char* e = std::getenv("OT_SELFPROF"); e && std::atof(e) > 0.0)
			prof.start(std::atof(e));
		struct ProfEnd { SelfProfiler& p; ~ProfEnd() { p.finish(); } } profEnd{prof};
#ifdef __APPLE__
		// O17: the ColdFire's thread joins the DSP workers' scheduling band.
		// The workers run at QOS_CLASS_USER_INITIATED (the vendored
		// ThreadPriority::High -- Apple silicon's performance cores); the main
		// thread at the default class was left to an efficiency core beside
		// them and ran its own emulation at half speed (measured 12 Sep 2026:
		// the same instruction count as lockstep's, twice the wall).
		if(_dsp && _dsp->rt())
			pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0);
#endif
		// The first `tx` answers everything since the boot: the panel's
		// whole screen and LED state is in that stream (the boot's full draw,
		// then diffs), and a decoder fed from byte 0 has all of it.
		size_t txCursor = 0;
		size_t hitCursor = 0, writeCursor = 0;	// `hits` / `writes` report since the previous call
		double wallInRun = 0.0;
		const auto reply = [](const std::string& _s)
		{
			std::fputs(_s.c_str(), stdout);
			std::fputc('\n', stdout);
			std::fflush(stdout);
		};
		char buf[160];
		std::snprintf(buf, sizeof buf, "ready sample=%.3f frames=%llu", _rtos.sample(),
			static_cast<unsigned long long>(_rtos.frameCount()));
		reply(buf);

		// -- O15f: the pacer. Off unless `pace on`; nothing below runs the
		// machine while it is off, so an unpaced session is the old loop.
		// Emulated time is made to track wall time x rate from an anchor
		// (wall, ms) taken at `pace on` and moved only by a re-anchor: with
		// the lead (emulated - target) at a slice or more the child sleeps
		// the excess, capped at one slice, inside poll() on stdin; below
		// that it runs one 10 ms slice; more than PACE_MAX_LAG_MS behind (a
		// core slower than real time, a long command) it re-anchors so the
		// lag does not accumulate into a catch-up burst later. Idle, a
		// slice is a few idle skips (microseconds), so the loop is nearly
		// all sleep; playing, a slice costs what the core costs and the
		// lead never builds, so it is flat out until the core is >= 1.0x.
		constexpr double PACE_SLICE_MS = 10.0, PACE_MAX_LAG_MS = 250.0;
		bool paceOn = false;
		double paceRate = 1.0;
		std::chrono::steady_clock::time_point paceAnchorWall{}, paceWinWall{};
		double paceAnchorMs = 0.0, paceWinMs = 0.0, paceRatio = 0.0, paceSleptS = 0.0, paceBusyS = 0.0;
		uint64_t paceSlices = 0, paceReanchors = 0;
		ot::Rtos::Stop paceStop = ot::Rtos::Stop::Time;
		const auto stdinPending = []() -> bool
		{
			// A line already in a buffer (iostream's, or stdio's: cin is
			// synced with stdio, whose FILE may hold bytes poll() cannot see)
			// or bytes on the pipe.
			if(std::cin.rdbuf()->in_avail() > 0)
				return true;
#ifdef __APPLE__
			if(stdin->_r > 0)
				return true;
#endif
			pollfd pfd{0, POLLIN, 0};
			return ::poll(&pfd, 1, 0) > 0;
		};
		const auto paceWindow = [&](const std::chrono::steady_clock::time_point _now)
		{
			// The ratio: emulated ms per wall s over the last closed window
			// of at least a second (commands served inside it included).
			const double sinceS = std::chrono::duration<double>(_now - paceWinWall).count();
			if(sinceS >= 1.0)
			{
				paceRatio = (_rtos.ms() - paceWinMs) / (sinceS * 1000.0);
				paceWinWall = _now;
				paceWinMs = _rtos.ms();
			}
		};
		const auto paceTargetMs = [&](const std::chrono::steady_clock::time_point _now) -> double
		{
			return paceAnchorMs + std::chrono::duration<double, std::milli>(_now - paceAnchorWall).count() * paceRate;
		};
		const auto paceStep = [&]() -> bool	// false: the machine stopped (fault/illegal), the free run ends
		{
			const auto now = std::chrono::steady_clock::now();
			const double lead = _rtos.ms() - paceTargetMs(now);
			if(lead >= PACE_SLICE_MS)
			{
				const double s = std::min(lead - PACE_SLICE_MS + 1.0, PACE_SLICE_MS) / 1000.0 / paceRate;
				// Inside poll(): a command line on stdin ends the sleep at
				// once (a sleep_for here made every command wait up to a
				// slice: 13.7 ms median key round trip vs 0.09, measured on
				// the prototype, out/_agents/speed-pacing).
				pollfd pfd{0, POLLIN, 0};
				::poll(&pfd, 1, std::max(1, static_cast<int>(s * 1000.0 + 0.5)));
				const auto t1 = std::chrono::steady_clock::now();
				paceSleptS += std::chrono::duration<double>(t1 - now).count();
				paceWindow(t1);
				return true;
			}
			if(lead < -PACE_MAX_LAG_MS)
			{
				paceAnchorWall = now;
				paceAnchorMs = _rtos.ms();
				++paceReanchors;
			}
			paceStop = _rtos.run(PACE_SLICE_MS, false);
			const auto t1 = std::chrono::steady_clock::now();
			const double w = std::chrono::duration<double>(t1 - now).count();
			paceBusyS += w;
			wallInRun += w;
			++paceSlices;
			paceWindow(t1);
			return paceStop == ot::Rtos::Stop::Time || paceStop == ot::Rtos::Stop::Gate;
		};
		bool served = false;		// the previous line was a command: the client may be mid-round
		const auto nextLine = [&](std::string& _line) -> bool
		{
			if(paceOn && served)
			{
				// A client sends its commands in rounds (the panel's tx, audio
				// read, pacestatus: ~100 us apart), and a slice started in
				// that gap makes every command of the round wait a slice --
				// four slices (231 ms) per page click while playing with the
				// cores, measured 12 Sep 2026. One millisecond of poll() after
				// a reply lets the round through; at 1.0x it comes out of the
				// sleep, flat out it is < 1 % (a round per 200 ms).
				served = false;
				pollfd pfd{0, POLLIN, 0};
				::poll(&pfd, 1, 1);
			}
			while(paceOn && !stdinPending())
			{
				if(!paceStep())
				{
					paceOn = false;		// the machine stopped: the next `run` answers as it always did
					break;
				}
			}
			return static_cast<bool>(std::getline(std::cin, _line));
		};

		std::string line;
		while(nextLine(line))
		{
			served = true;
			const auto w = splitWords(line);
			if(w.empty())
			{
				reply("err empty command");
				continue;
			}
			const auto& cmd = w[0];
			if(cmd == "pace")
			{
				if(w.size() == 2 && w[1] == "off")
				{
					paceOn = false;
					reply("ok");
					continue;
				}
				double rate = 1.0;
				char* end = nullptr;
				const bool ok = w.size() >= 2 && w[1] == "on" && w.size() <= 3
					&& (w.size() == 2 || (rate = std::strtod(w[2].c_str(), &end), end && !*end && rate > 0.0 && rate <= 1000.0));
				if(!ok)
				{
					reply("err usage: pace on [rate] | pace off");
					continue;
				}
				paceOn = true;
				paceRate = rate;
				paceAnchorWall = paceWinWall = std::chrono::steady_clock::now();
				paceAnchorMs = paceWinMs = _rtos.ms();
				paceRatio = 0.0;
				paceStop = ot::Rtos::Stop::Time;
				reply("ok");
				continue;
			}
			if(cmd == "rtstatus")
			{
				// O17: the real-time mode's counters -- MIPS per core (the
				// counter's rate; xmips = executed, without the fast-forward),
				// worker busy fraction and CPU seconds, core 0's ESAI frames,
				// the due count and each core's lag behind it, posts / wakes /
				// waits, edges raised / applied / inside pulls with their
				// lateness, skew waits, fast-forwarded instructions, read-back
				// words not in time, dropped host words, faults, the knobs.
				// `err rtstatus needs --dsp-rt` otherwise.
				if(w.size() != 1)
				{
					reply("err usage: rtstatus");
					continue;
				}
				if(!_dsp || !_dsp->rt())
				{
					reply("err rtstatus needs --dsp-rt");
					continue;
				}
				reply(_dsp->rtStatus() + " | cfinstr=" + std::to_string(_m.instructions()) + " ms=" + std::to_string(_rtos.ms()));
				continue;
			}
			if(cmd == "cfstatus")
			{
				// O17: the ColdFire's own instruction count and the emulated clock
				// (a rate meter for any mode: instructions per wall second between
				// two calls is what the ColdFire's thread itself achieves).
				if(w.size() != 1)
				{
					reply("err usage: cfstatus");
					continue;
				}
				std::snprintf(buf, sizeof buf, "cfstatus instructions=%llu ms=%.3f pc=%08x", static_cast<unsigned long long>(_m.instructions()), _rtos.ms(), _m.pc());
				reply(buf);
				continue;
			}
			if(cmd == "edmastatus")
			{
				// O17b diagnostic: the eDMA's booked completions (channel@sample, gated ones included), its IRQ lines,
				// the gated-wait count, and INTC0's mask on the frame source and the eDMA channels
				if(w.size() != 1)
				{
					reply("err usage: edmastatus");
					continue;
				}
				std::string out = "edmastatus outstanding=" + std::to_string(_rtos.edma().outstanding()) + " gatedwaits=" + std::to_string(_rtos.edma().gatedWaits()) + " irq=";
				for(uint32_t ch = 0; ch < 16; ++ch)
					out += _rtos.edma().irq(ch) ? "1" : "0";
				double nextDue = 0.0;
				out += " nextdue=" + (_rtos.edma().nextDue(nextDue) ? std::to_string(nextDue) : std::string("none"));
				out += " src1masked=" + std::to_string(_rtos.intc0().masked(1) ? 1 : 0) + " src8masked=" + std::to_string(_rtos.intc0().masked(8) ? 1 : 0)
					+ " src1asserting=" + std::to_string(_rtos.intc0().assertedSource(1) ? 1 : 0) + " src8asserting=" + std::to_string(_rtos.intc0().assertedSource(8) ? 1 : 0)
					+ " framepending=" + std::to_string(_rtos.framePending() ? 1 : 0) + " sample=" + std::to_string(_rtos.sample());
				reply(out);
				continue;
			}
			if(cmd == "pacestatus")
			{
				if(w.size() != 1)
				{
					reply("err usage: pacestatus");
					continue;
				}
				const auto now = std::chrono::steady_clock::now();
				const double lag = paceOn ? std::max(0.0, paceTargetMs(now) - _rtos.ms()) : 0.0;
				std::snprintf(buf, sizeof buf, "pacestatus on=%d rate=%.3f ratio=%.3f lag=%.1f slices=%llu reanchors=%llu slept=%.3f busy=%.3f stop=%s ms=%.3f",
					paceOn ? 1 : 0, paceRate, paceRatio, lag, static_cast<unsigned long long>(paceSlices),
					static_cast<unsigned long long>(paceReanchors), paceSleptS, paceBusyS, stopWord(paceStop), _rtos.ms());
				reply(buf);
				continue;
			}
			if(cmd == "quit")
			{
				if(w.size() != 1)
				{
					reply("err usage: quit");
					continue;
				}
				if(_card)
					_card->flush();		// O19: the write-back file on disk before the exit
				reply("ok");
				return 0;
			}
			if(cmd == "card")
			{
				// O19: the card's write-back state (see the protocol above)
				if(w.size() != 2 || (w[1] != "status" && w[1] != "flush"))
				{
					reply("err usage: card status | card flush");
					continue;
				}
				if(!_card)
				{
					reply("err no card");
					continue;
				}
				if(w[1] == "flush")
				{
					const bool ok = _card->flush();
					std::snprintf(buf, sizeof buf, "card %s rw=%d through=%llu errors=%llu", ok ? "ok" : "fsync-failed",
						_card->writeBack() ? 1 : 0, static_cast<unsigned long long>(_card->writtenThrough()),
						static_cast<unsigned long long>(_card->writeErrors()));
					reply(buf);
					continue;
				}
				std::string line = "card ok rw=" + std::string(_card->writeBack() ? "1" : "0")
					+ " path=" + (_card->writeBack() ? _card->writeBackPath() : std::string("-"))
					+ " written=" + std::to_string(_card->sectorsWritten())
					+ " through=" + std::to_string(_card->writtenThrough())
					+ " errors=" + std::to_string(_card->writeErrors());
				reply(line);
				continue;
			}
			if(cmd == "run")
			{
				double ms = 0.0, wallBudget = 0.0;
				char* end = nullptr;
				const bool plain = w.size() == 2;
				bool ok = plain || (w.size() == 4 && w[2] == "wall");
				ok = ok && !((ms = std::strtod(w[1].c_str(), &end), !end || *end) || !(ms >= 0.0) || ms > 1e9);
				ok = ok && (plain || !((wallBudget = std::strtod(w[3].c_str(), &end), !end || *end) || !(wallBudget > 0.0) || wallBudget > 1e6));
				if(!ok)
				{
					// the old reply for every old input; the new text only when the wall form was attempted
					reply(w.size() == 4 && w[2] == "wall" ? "err usage: run <ms> [wall <seconds>]" : "err usage: run <ms>");
					continue;
				}
				const auto t0 = std::chrono::steady_clock::now();
				ot::Rtos::Stop st;
				bool wallHit = false;
				if(plain)
					st = _rtos.run(ms, false);		// the old path, untouched (the oracle's runs)
				else
				{
					// O15f: the deadline is a condition on nothing the machine
					// does, so it is asked where the loop asks any event
					// condition -- at every burst end and exact step -- and
					// the clock is read once per 4096 instructions (a
					// steady_clock read per burst would cost ~5 % on bursts
					// that average ~70 instructions). Where the run ends
					// moves no firmware event.
					const auto deadline = t0 + std::chrono::duration_cast<std::chrono::steady_clock::duration>(std::chrono::duration<double>(wallBudget));
					uint64_t next = _m.instructions() + 4096;
					const std::function<bool()> stop = [&]() -> bool
					{
						const auto n = _m.instructions();
						if(n < next)
							return false;
						next = n + 4096;
						if(std::chrono::steady_clock::now() < deadline)
							return false;
						wallHit = true;
						return true;
					};
					st = _rtos.runUntil(ms, stop, ot::Rtos::Changes::OnEvent);
				}
				wallInRun += std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
				std::snprintf(buf, sizeof buf, "ok sample=%.3f frames=%llu stop=%s", _rtos.sample(),
					static_cast<unsigned long long>(_rtos.frameCount()), wallHit ? "wall" : stopWord(st));
				reply(buf);
				continue;
			}
			if(cmd == "key")
			{
				uint64_t row = 0, mask = 0;
				if(w.size() != 3 || !parseNumber(w[1], row) || !parseNumber(w[2], mask) || row > 0xff || mask > 0xff)
				{
					reply("err usage: key <row> <mask> (0..255)");
					continue;
				}
				_rtos.uartA().rxPush(static_cast<uint8_t>(row));
				_rtos.uartA().rxPush(static_cast<uint8_t>(mask));
				reply("ok");
				continue;
			}
			if(cmd == "knob")
			{
				uint64_t row = 0;
				int64_t delta = 0;
				if(w.size() != 3 || !parseNumber(w[1], row) || !parseSigned(w[2], delta) || row > 0xff || delta < -128 || delta > 255)
				{
					reply("err usage: knob <row> <delta> (-128..127)");
					continue;
				}
				_rtos.uartA().rxPush(static_cast<uint8_t>(row));
				_rtos.uartA().rxPush(static_cast<uint8_t>(delta & 0xff));
				reply("ok");
				continue;
			}
			if(cmd == "tx")
			{
				if(w.size() != 1)
				{
					reply("err usage: tx");
					continue;
				}
				const auto& tx = _rtos.serialTxA();
				std::string out = "tx ";
				out.reserve(3 + 2 * (tx.size() - txCursor));
				static const char* const hex = "0123456789abcdef";
				for(size_t i = txCursor; i < tx.size(); ++i)
				{
					out += hex[tx[i] >> 4];
					out += hex[tx[i] & 15];
				}
				txCursor = tx.size();
				reply(out);
				continue;
			}
			if(cmd == "peek")
			{
				uint64_t addr = 0, len = 0;
				if(w.size() != 3 || !parseNumber(w[1], addr) || !parseNumber(w[2], len) || addr > 0xffffffffull || len < 1 || len > 4096 || addr + len > 0x100000000ull)
				{
					reply("err usage: peek <addr> <len> (1..4096)");
					continue;
				}
				if(!_m.mapped(static_cast<uint32_t>(addr), static_cast<uint32_t>(len)))
				{
					std::snprintf(buf, sizeof buf, "err unmapped %#llx+%llu", static_cast<unsigned long long>(addr), static_cast<unsigned long long>(len));
					reply(buf);
					continue;
				}
				std::string out = "peek ";
				static const char* const hex = "0123456789abcdef";
				for(uint64_t i = 0; i < len; ++i)
				{
					const auto b = _m.read8(static_cast<uint32_t>(addr + i));
					out += hex[b >> 4];
					out += hex[b & 15];
				}
				reply(out);
				continue;
			}
			if(cmd == "poke")
			{
				uint64_t addr = 0;
				std::vector<uint8_t> bytes;
				if(w.size() != 3 || !parseNumber(w[1], addr) || addr > 0xffffffffull || !parseHex(w[2], bytes) || bytes.size() > 4096 || addr + bytes.size() > 0x100000000ull)
				{
					reply("err usage: poke <addr> <hex> (1..4096 bytes, even number of hex digits)");
					continue;
				}
				if(!_m.mapped(static_cast<uint32_t>(addr), static_cast<uint32_t>(bytes.size())))
				{
					std::snprintf(buf, sizeof buf, "err unmapped %#llx+%zu", static_cast<unsigned long long>(addr), bytes.size());
					reply(buf);
					continue;
				}
				for(size_t i = 0; i < bytes.size(); ++i)
					_m.write8(static_cast<uint32_t>(addr + i), bytes[i]);
				reply("ok");
				continue;
			}
			if(cmd == "frame")
			{
				if(w.size() != 2 || (w[1] != "on" && w[1] != "off"))
				{
					reply("err usage: frame on|off");
					continue;
				}
				_rtos.setFrame(w[1] == "on");
				reply("ok");
				continue;
			}
			if(cmd == "status")
			{
				if(w.size() != 1)
				{
					reply("err usage: status");
					continue;
				}
				std::snprintf(buf, sizeof buf, "status sample=%.3f ms=%.3f frames=%llu frame=%s idle=%llu wall=%.3f",
					_rtos.sample(), _rtos.ms(), static_cast<unsigned long long>(_rtos.frameCount()),
					_rtos.frameOn() ? "on" : "off", static_cast<unsigned long long>(_rtos.idleSkips()), wallInRun);
				reply(buf);
				continue;
			}
			if(cmd == "watch")
			{
				std::vector<uint32_t> addrs;
				bool ok = w.size() == 2;
				if(ok && w[1] != "off")
				{
					size_t q = 0;
					while(ok && q <= w[1].size())
					{
						auto e = w[1].find(',', q);
						if(e == std::string::npos) e = w[1].size();
						uint64_t v = 0;
						ok = parseNumber(w[1].substr(q, e - q), v) && v <= 0xffffffffull;
						addrs.push_back(static_cast<uint32_t>(v));
						q = e + 1;
					}
				}
				if(!ok)
				{
					reply("err usage: watch <addr>[,<addr>...] | watch off");
					continue;
				}
				_m.watchPc(std::move(addrs));
				reply("ok");
				continue;
			}
			if(cmd == "hits")
			{
				if(w.size() != 1)
				{
					reply("err usage: hits");
					continue;
				}
				const auto& hs = _m.pcHits();
				std::string out = "hits n=" + std::to_string(hs.size() - hitCursor);
				// The record is 23 fields: at most 1 + 16 + 22 * 9 = 215 bytes
				// when every register is 8 hex digits (a 64-bit instruction
				// count 16), which the shared 160-byte `buf` cut short --
				// silently, snprintf truncates -- so the record gets its own.
				char rec[256];
				for(size_t i = hitCursor; i < hs.size(); ++i)
				{
					const auto& h = hs[i];
					std::snprintf(rec, sizeof rec, " %llx:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x:%x",
						static_cast<unsigned long long>(h.instruction), h.pc, h.d0, h.d1, h.a0, h.a1, h.sp,
						h.stack[0], h.stack[1], h.stack[2], h.stack[3], h.stack[4],
						h.d[2], h.d[3], h.d[4], h.d[5], h.d[6], h.d[7], h.a[2], h.a[3], h.a[4], h.a[5], h.a[6]);
					out += rec;
				}
				hitCursor = hs.size();
				reply(out);
				continue;
			}
			if(cmd == "watchmem")
			{
				uint64_t addr = 0, len = 0;
				if(w.size() != 3 || !parseNumber(w[1], addr) || !parseNumber(w[2], len) || addr > 0xffffffffull || len < 1 || addr + len > 0x100000000ull)
				{
					reply("err usage: watchmem <addr> <len>");
					continue;
				}
				_rtos.watchMem(static_cast<uint32_t>(addr), static_cast<uint32_t>(len));
				reply("ok");
				continue;
			}
			if(cmd == "writes")
			{
				if(w.size() != 1)
				{
					reply("err usage: writes");
					continue;
				}
				const auto& ws = _rtos.memWrites();
				std::string out = "writes n=" + std::to_string(ws.size() - writeCursor);
				for(size_t i = writeCursor; i < ws.size(); ++i)
				{
					const auto& r = ws[i];
					std::snprintf(buf, sizeof buf, " %.1f:%x:%x:%x:%u:%x", r.sample, r.pc, r.addr, r.val, r.size, r.tcb);
					out += buf;
				}
				writeCursor = ws.size();
				reply(out);
				continue;
			}
			if(cmd == "audio")
			{
				const auto sub = w.size() > 1 ? w[1] : std::string();
				if(!_dsp)
				{
					reply("err audio needs --dsp");
					continue;
				}
				using Mode = ot::DspPair::StreamMode;
				if(sub == "start")
				{
					const auto mode = w.size() == 2 || w[2] == "main" ? Mode::Main
						: w[2] == "cue" ? Mode::Cue : w[2] == "all" ? Mode::All : Mode::Off;
					if(w.size() > 3 || mode == Mode::Off)
					{
						reply("err usage: audio start [main|cue|all]");
						continue;
					}
					_dsp->setAudioStream(mode);
					reply("ok");
					continue;
				}
				if(sub == "stop")
				{
					if(w.size() != 2)
					{
						reply("err usage: audio stop");
						continue;
					}
					_dsp->setAudioStream(Mode::Off);
					reply("ok");
					continue;
				}
				if(sub == "status")
				{
					if(w.size() != 2)
					{
						reply("err usage: audio status");
						continue;
					}
					const auto st = _dsp->streamStatus();
					static const char* const g_modes[] = {"off", "main", "cue", "all"};
					std::snprintf(buf, sizeof buf, "audio status on=%d mode=%s captured=%llu pending=%llu rate=44100 dropped=%llu cap=%u",
						st.mode != Mode::Off, g_modes[static_cast<int>(st.mode)],
						static_cast<unsigned long long>(st.captured), static_cast<unsigned long long>(st.pending),
						static_cast<unsigned long long>(st.dropped), ot::DspPair::g_streamCapFrames);
					reply(buf);
					continue;
				}
				if(sub == "read")
				{
					uint64_t maxFrames = ot::DspPair::g_streamCapFrames;
					if(w.size() > 3 || (w.size() == 3 && (!parseNumber(w[2], maxFrames) || !maxFrames)))
					{
						reply("err usage: audio read [<maxframes>]");
						continue;
					}
					if(_dsp->audioStream() == Mode::Off)
					{
						reply("err audio off (audio start first)");
						continue;
					}
					std::vector<int16_t> pcm;
					const auto n = _dsp->takeAudioStream(pcm, static_cast<size_t>(std::min<uint64_t>(maxFrames, ot::DspPair::g_streamCapFrames)));
					std::string out = "audio " + std::to_string(n) + " ";
					out.reserve(out.size() + 4 * pcm.size());
					static const char* const hex = "0123456789abcdef";
					for(const auto v : pcm)
					{
						const auto u = static_cast<uint16_t>(v);		// little-endian: low byte first
						out += hex[(u >> 4) & 15]; out += hex[u & 15];
						out += hex[u >> 12]; out += hex[(u >> 8) & 15];
					}
					reply(out);
					continue;
				}
				reply("err usage: audio start [main|cue|all] | audio read [<maxframes>] | audio status | audio stop");
				continue;
			}
			reply("err unknown command " + cmd);
		}
		return 0;		// EOF on stdin: the client went away
	}
}

int main(int _argc, char** _argv)
{
	// ⚠️ LINE-BUFFERED, ALWAYS. Redirected to a file, printf is block-buffered,
	// so a run that dies mid-way writes NOTHING -- the O6 auto-map runaway
	// crashed three times before anyone saw a line of the report that would
	// have named it. Same family as the panic printer O7 could not finish.
	std::setvbuf(stdout, nullptr, _IOLBF, 0);
	std::string image = "out/raw/section_3_MAIN_OS.bin";
	uint64_t maxInstructions = 50'000'000;	// route A's own budget
	bool showPeripherals = false;
	bool profile = false;
	std::string golden;
	std::string cardImage;		// a FAT16 card image built by emu_rtos.stage_project
	bool mount = false;			// post the card-mount request to the SYS task
	bool cardRw = false;		// O19: --card-rw -- WRITE SECTORS go through to the image FILE (AtaCard::setWriteBack); the file is the card
	std::string ataTrace;		// write every task-file access here, for diffing against route A
	std::string periphTrace;	// every peripheral access over the load, for the same diff
	std::string peeks;			// comma-separated hex addresses to print after the load
	std::string cmdLog;		// every ATA COMMAND in order, for diffing against route A
	uint64_t pcRing = 0;		// instructions to record from the first ATA command
	double loadMs = 6000.0;		// emulated ms to run after LOAD PROJECT is posted
	std::string setName = "OCTABAM", projectName = "ONEAUX";
	std::string serialOut;
	double runMs = 1000.0;
	double ips = 3990.0;
	bool frame = false;			// the DSP frame clock; off by default, as in route A
	bool bootLogo = false;		// let the boot logo run its 2.8 s on DTIM3 (Rtos::Quirks::skipBootLogo off)
	bool sequencer = false;		// M6c: load, start the transport, run the sequencer for real
	int frames = 400;			// with --sequencer: DSP frames to run after the transport start
	int pokeTrig = 0;			// with --sequencer: set a trig on track 1 at this step (1-64)
	bool internalClock = false;	// with --sequencer: clear CLOCK RECEIVE
	int bankOverride = -1;		// with --sequencer: switch to this bank (default: the file's saved bank)
	std::string m6cGolden;		// the M6c facts as JSON, for tools/emu/ot_emu/oracle.py
	std::string watchMem;		// ADDR,LEN -- log every write into that range (route A's own flag)
	std::string watchPc;		// comma-separated addresses -- log registers there (route A's own flag)
	bool namesEarly = false;	// write the SET/PROJECT names BEFORE the mount -- see O7b
	std::string hostPortLog;	// every write into the DSP host-port window -> FILE (O8)
	bool dsp = false;			// O8: put the two real DSP cores behind the host port
	bool dspRt = false;			// O17: --dsp-rt -- the cores under the JIT on worker threads, on the lockstep schedule (dsp.cpp, THE REAL-TIME MODE); --interactive only
	double dspRatio = ot::DspPair::g_dspIps / ot::DspPair::g_cfIps, dspIps = ot::DspPair::g_dspIps;	// their clock, in DSP instructions per ColdFire instruction / per sample (dsp.h says where 4160 comes from)
	std::string dspLog;			// every host-side event on the DSP pair -> FILE
	uint64_t dspTrace = 0;		// a status line per core every N DSP instructions
	uint64_t dspTraceFrom = 0;	// ... only once a core has executed this many (a window at the end of a run)
	bool dspNoIdle = false;
	bool dspDrainPaced = false;	// EXPERIMENT: a host-port burst completes when the DSP drained it (measured 8 Sep: one frame of exactly 16 ESAI frames, then the completion ISR loses an edge and stalls)		// execute every poll of an idle core (fidelity check; slow)
	bool dspVerbose = false;	// the vendored DSP library's own log lines
	std::string edmaLog;		// every eDMA kick with its TCD fields -> FILE (O8 step 4)
	std::string dspPeek;		// core:space:addr,len[;...] -- DSP memory to print at the end
	std::string blockLog;		// every host-port BLOCK with its non-zero count -> FILE
	std::string blockDump;		// O9d: every host-port block's CONTENT (binary) -> FILE
	std::string audioOut;		// O9: PREFIX -> PREFIX_core<k>.wav, every X-side ESAI TX0 frame (8 slots) the core put out
	std::string audioIn;		// O9: a WAV onto RX0's slots from the transport start, or "tones"
	bool audioInFromBoot = false;	// O14: feed it from the DSP boot instead (a live input already flowing when a step-1 recorder arms)
	int preRoll = 0;			// O14: frames of the frame engine to run BEFORE the transport start (a warm DSP, as on hardware)
	std::string dspPcWatch;		// O9b: core:pc -- registers at the last 24 arrivals at that DSP PC
	std::string dspStopwatch;	// O12: core:startpc:stoppc -- instructions between the two, per pair (the cycle meter)
	std::string dspWatch;		// O9b: core:space:addr -- the last 16 writers of one DSP word
	std::string dspMap;		// O9: per-frame non-zero counts per 4K chunk of both cores' X and Y -> FILE
	std::string dspWrites;		// O9: per-frame NON-ZERO WRITE counts per 256-word region of both cores' X and Y -> FILE
	std::string coverage;		// O9b: every ColdFire PC executed from the transport start on, with its count -> FILE (diff two runs)
	bool frameTimer = false;	// O9b: keep the free-running 16-sample frame timer with --dsp (default: the DSP's bank word is the frame edge)
	double dspLazy = ot::DspPair::g_lazyDefault;	// O16c: --dsp-lazy N -- the pair's ticks are booked and replayed in chunks of up to N DSP instructions at the ColdFire's touch points (0 = the per-tick path); the default in every mode, byte-identical to it
	std::string pokeAfterLoad;	// O9c: "addr=byte;addr=byte" written after the load, before the frames (drive an apply the load skips)
	int mainLevel = -1;			// O9b: post sys command 4 (SET MAIN LEVEL) with this level after the load; -1 = don't (the emulated load never does, and every voice then renders at gain zero)
	bool mainLevelGiven = false;	// 12 Sep 2026: --interactive defaults it to 64 (the panel wants sound); `--main-level off` keeps the -1. The batch default stays -1.
	std::string memDump;		// O10.21: "addr,len=path[;...]" -- ColdFire memory ranges, raw bytes, to FILE at the very end (peeks only support one word, pre-sequencer; this is a range, post-run)
	bool interactive = false;	// 11 Sep 2026: after the boot (and load), serve the line protocol on stdin/stdout (serveInteractive above)
	std::string rtc;			// 11 Sep 2026: the DSPI chip-select-2 clock -- "host", "off", or <epoch seconds> (UTC, frozen); default off, host under --interactive (Dspi::RtcClock)

	for(int i = 1; i < _argc; ++i)
	{
		const std::string a = _argv[i];
		if(a == "--image" && i + 1 < _argc)		image = _argv[++i];
		else if(a == "--max" && i + 1 < _argc)	maxInstructions = std::strtoull(_argv[++i], nullptr, 0);
		else if(a == "--periph")				showPeripherals = true;
		else if(a == "--profile")				profile = true;
		else if(a == "--golden" && i + 1 < _argc)	golden = _argv[++i];
		else if(a == "--card" && i + 1 < _argc)		cardImage = _argv[++i];
		else if(a == "--mount")						mount = true;
		else if(a == "--card-rw")					cardRw = true;
		else if(a == "--ata-trace" && i + 1 < _argc)	ataTrace = _argv[++i];
		else if(a == "--periph-trace" && i + 1 < _argc)	periphTrace = _argv[++i];
		else if(a == "--peek" && i + 1 < _argc)		peeks = _argv[++i];
		else if(a == "--cmd-log" && i + 1 < _argc)		cmdLog = _argv[++i];
		else if(a == "--pc-ring" && i + 1 < _argc)	pcRing = std::strtoull(_argv[++i], nullptr, 0);
		else if(a == "--load-ms" && i + 1 < _argc)	loadMs = std::atof(_argv[++i]);
		else if(a == "--set" && i + 1 < _argc)		setName = _argv[++i];
		else if(a == "--project" && i + 1 < _argc)	projectName = _argv[++i];
		else if(a == "--serial-out" && i + 1 < _argc)	serialOut = _argv[++i];
		else if(a == "--ms" && i + 1 < _argc)	runMs = std::atof(_argv[++i]);
		else if(a == "--ips" && i + 1 < _argc)	ips = std::atof(_argv[++i]);
		else if(a == "--frame")					frame = true;
		else if(a == "--boot-logo")				bootLogo = true;
		else if(a == "--sequencer")				sequencer = true;
		// --sequencer needs a mounted card and a loaded project: it implies both.
		else if(a == "--frames" && i + 1 < _argc)	frames = std::atoi(_argv[++i]);
		else if(a == "--poke-trig" && i + 1 < _argc)	pokeTrig = std::atoi(_argv[++i]);
		else if(a == "--internal-clock")			internalClock = true;
		else if(a == "--bank" && i + 1 < _argc)		bankOverride = std::atoi(_argv[++i]);
		else if(a == "--m6c-golden" && i + 1 < _argc)	m6cGolden = _argv[++i];
		else if(a == "--watch-mem" && i + 1 < _argc)	watchMem = _argv[++i];
		else if(a == "--watch-pc" && i + 1 < _argc)	watchPc = _argv[++i];
		else if(a == "--names-early")			namesEarly = true;
		else if(a == "--hostport-log" && i + 1 < _argc)	hostPortLog = _argv[++i];
		else if(a == "--dsp")					dsp = true;
		else if(a == "--dsp-rt")				{ dsp = true; dspRt = true; }
		else if(a == "--dsp-ratio" && i + 1 < _argc)	dspRatio = std::atof(_argv[++i]);
		else if(a == "--dsp-ips" && i + 1 < _argc)	dspIps = std::atof(_argv[++i]);
		else if(a == "--dsp-log" && i + 1 < _argc)	dspLog = _argv[++i];
		else if(a == "--dsp-trace" && i + 1 < _argc)	dspTrace = std::strtoull(_argv[++i], nullptr, 0);
		else if(a == "--dsp-trace-from" && i + 1 < _argc)	dspTraceFrom = std::strtoull(_argv[++i], nullptr, 0);
		else if(a == "--dsp-no-idle")			dspNoIdle = true;
		else if(a == "--dsp-drain-paced")		dspDrainPaced = true;
		else if(a == "--dsp-verbose")			dspVerbose = true;
		else if(a == "--dsp-quantum" && i + 1 < _argc)	ot::DspPair::g_quantum = std::atof(_argv[++i]);	// O12: the core interleave quantum (instructions)
		else if(a == "--dsp-lazy" && i + 1 < _argc)	dspLazy = std::atof(_argv[++i]);	// O16c: lazy batching of the pair, N DSP instructions (0 = exact)
		else if(a == "--edma-log" && i + 1 < _argc)	edmaLog = _argv[++i];
		else if(a == "--dsp-peek" && i + 1 < _argc)	dspPeek = _argv[++i];
		else if(a == "--block-log" && i + 1 < _argc)	blockLog = _argv[++i];
		else if(a == "--block-dump" && i + 1 < _argc)	blockDump = _argv[++i];
		else if(a == "--audio-out" && i + 1 < _argc)	audioOut = _argv[++i];
		else if(a == "--audio-in" && i + 1 < _argc)	audioIn = _argv[++i];
		else if(a == "--audio-in-from-boot")		audioInFromBoot = true;
		else if(a == "--pre-roll" && i + 1 < _argc)	preRoll = std::atoi(_argv[++i]);
		else if(a == "--dsp-map" && i + 1 < _argc)	dspMap = _argv[++i];
		else if(a == "--dsp-watch" && i + 1 < _argc)	dspWatch = _argv[++i];
		else if(a == "--dsp-pcwatch" && i + 1 < _argc)	dspPcWatch = _argv[++i];
		else if(a == "--dsp-stopwatch" && i + 1 < _argc)	dspStopwatch = _argv[++i];
		else if(a == "--dsp-writes" && i + 1 < _argc)	dspWrites = _argv[++i];
		else if(a == "--coverage" && i + 1 < _argc)	coverage = _argv[++i];
		else if(a == "--main-level" && i + 1 < _argc)	{ const std::string v = _argv[++i]; mainLevel = v == "off" ? -1 : std::atoi(v.c_str()); mainLevelGiven = true; }
		else if(a == "--mem-dump" && i + 1 < _argc)	memDump = _argv[++i];
		else if(a == "--poke" && i + 1 < _argc)		pokeAfterLoad = _argv[++i];
		else if(a == "--frame-timer")				frameTimer = true;
		else if(a == "--interactive")				interactive = true;
		else if(a == "--rtc" && i + 1 < _argc)		rtc = _argv[++i];
		else
		{
			std::printf("usage: ot_emu [--image FILE] [--max N] [--periph] [--profile]\n"
			"              [--golden FILE] [--ms N] [--boot-logo]\n");
			return 2;
		}
	}

	if(sequencer)
		mount = true;			// M6c needs the card mounted and the project loaded
	if(dspRt && !interactive)
	{
		// The batch modes keep the lockstep interpreter (their reports and
		// captures are byte-identical across builds, the oracle's contract);
		// the rt mode's audio is functional, not byte-identical.
		std::printf("dsp-rt     : cannot start the real-time mode: --dsp-rt needs --interactive (the batch keeps the lockstep interpreter)\n");
		return 2;
	}
	if(interactive && !mainLevelGiven)
		mainLevel = 64;			// the batch never posts it unless asked (byte-identical reports); the pipe wants audible voices

	// --rtc: the batch keeps the DSPI loopback (chip-select 2 answers 0, the
	// 2000-00-00 dialog: goldens and serial captures reproducible, and route
	// A's stock Dspi agrees); --interactive wants the real date on the panel.
	auto rtcMode = interactive ? ot::Dspi::RtcClock::Host : ot::Dspi::RtcClock::Off;
	int64_t rtcEpoch = 0;
	if(rtc == "host")
		rtcMode = ot::Dspi::RtcClock::Host;
	else if(rtc == "off")
		rtcMode = ot::Dspi::RtcClock::Off;
	else if(!rtc.empty())
	{
		char* end = nullptr;
		errno = 0;
		rtcEpoch = std::strtoll(rtc.c_str(), &end, 10);
		if(errno || !end || *end || rtcEpoch < 0)
		{
			std::printf("--rtc: host, off, or seconds since 1970 (UTC), not '%s'\n", rtc.c_str());
			return 2;
		}
		rtcMode = ot::Dspi::RtcClock::Fixed;
	}

	const auto img = readFile(image);
	if(img.empty())
	{
		std::printf("cannot read %s (run `make os` for the stock image, or `make bus` for a built one)\n",
			image.c_str());
		return 1;
	}
	std::printf("image      : %s (%zu bytes) at %#x\n", image.c_str(), img.size(), ot::Machine::g_imageBase);

	ot::Machine m(img);
	if(profile)
		m.setProfile(64);
	// ⚠️ BEFORE THE BOOT RUNS. Installed after it (where it used to be, behind
	// `rtos.install()`), a watch on a BOOT address reported zero hits whether
	// the address ran or not -- and the only caller of the DSP program loader
	// is at 0x4000050c, which is a boot address (O8, 8 Sep 2026).
	if(!watchPc.empty())
	{
		std::vector<uint32_t> addrs;
		size_t q = 0;
		while(q < watchPc.size())
		{
			auto e = watchPc.find(',', q);
			if(e == std::string::npos) e = watchPc.size();
			addrs.push_back(static_cast<uint32_t>(std::strtoul(watchPc.substr(q, e - q).c_str(), nullptr, 0)));
			q = e + 1;
		}
		m.watchPc(addrs);
		std::printf("watch-pc   : %zu address(es), armed before the boot\n", addrs.size());
	}
	if(!hostPortLog.empty())
		m.setHostPortLog(true);		// before the boot: the DSP upload happens IN it
	// The DSP pair, BEFORE the boot for the same reason: the firmware programs
	// both cores through the host port at instruction ~4.27M of the boot.
	std::unique_ptr<ot::DspPair> dspPair;
	if(dsp)
	{
		dspPair = std::make_unique<ot::DspPair>(dspRatio, dspIps, dspRt);
		if(dspRt && !dspPair->rtOk())
		{
			std::printf("dsp-rt     : cannot start the real-time mode: %s\n", dspPair->rtWhy().c_str());
			return 2;
		}
		if(dspRt)
		{
			std::printf("dsp-rt     : each core under the JIT on its own worker thread, up to a frame ahead of the ColdFire's booked due count, core 0's bank word fenced on the frame handler's end (O17b); the shared window aliased through the MMU (six views); rtstatus reports it\n");
			if(dspTrace || !dspPcWatch.empty() || !dspStopwatch.empty() || !dspWatch.empty() || !dspMap.empty() || !dspWrites.empty() || dspNoIdle)
				std::printf("dsp-rt     : note: --dsp-trace/--dsp-pcwatch/--dsp-stopwatch/--dsp-watch/--dsp-map/--dsp-writes/--dsp-no-idle are the interpreter's per-instruction instruments and do not observe the JIT workers (OT_RT_FF=0 is the rt fast-forward's switch)\n");
		}
		dspPair->setLog(!dspLog.empty());
		dspPair->setTrace(dspTrace);
		dspPair->setTraceFrom(dspTraceFrom);
		dspPair->setIdleSkip(!dspNoIdle);
		ot::DspPair::setVerbose(dspVerbose);
		dspPair->setAudioCapture(!audioOut.empty());
		dspPair->setActivityMap(!dspMap.empty());
		dspPair->setWriteMap(!dspWrites.empty());
		if(!dspStopwatch.empty())
		{
			int core = 0; unsigned a0 = 0, a1 = 0;
			if(std::sscanf(dspStopwatch.c_str(), "%d:%x:%x", &core, &a0, &a1) == 3)
				dspPair->setStopwatch(core, a0, a1);
		}
		if(!dspPcWatch.empty())
		{
			int core = 0; unsigned pc = 0; unsigned long long from = 0;
			if(std::sscanf(dspPcWatch.c_str(), "%d:%x:%llu", &core, &pc, &from) >= 2)
				dspPair->setPcWatch(core, pc, from);
		}
		if(!dspWatch.empty())
		{
			int core = 0; char space = 'X'; unsigned addr = 0;
			if(std::sscanf(dspWatch.c_str(), "%d:%c:%x", &core, &space, &addr) == 3)
				dspPair->setWriteWatch(core, space, addr);
		}
		if(audioIn == "tones")
			dspPair->setAudioTones(true);
		else if(!audioIn.empty())
		{
			std::vector<int32_t> pcm;
			uint32_t ch = 0, rate = 0;
			if(!ot::readWavPcm(audioIn, pcm, ch, rate))
			{
				std::printf("audio in   : %s is not a 16/24-bit PCM WAV\n", audioIn.c_str());
				return 1;
			}
			std::printf("audio in   : %s, %u channel(s) onto RX0 slots 0..%u, %zu frames at %u Hz (fed at 44100)\n",
				audioIn.c_str(), ch, ch - 1, pcm.size() / ch, rate);
			dspPair->setAudioInput(std::move(pcm), ch);
		}
		if(audioInFromBoot)
		{
			dspPair->setAudioInputFromBoot(true);
			std::printf("audio in   : fed from the DSP boot, not the first 0x8c (--audio-in-from-boot)\n");
		}
		m.setCoprocessor(dspPair.get());
		std::printf("dsp        : two cores behind the host port, %.2f instructions per ColdFire instruction, %.0f per sample\n",
			dspRatio, dspIps);
	}
	const auto stop = m.run(maxInstructions);

	static const char* const g_names[] = {"HANDOFF", "ILLEGAL", "BUDGET", "FAULT"};
	std::printf("stopped    : %s -- %s\n", g_names[static_cast<int>(stop)], m.why().c_str());
	std::printf("instructions: %llu (%llu supplied by the V4e layer)\n",
		static_cast<unsigned long long>(m.instructions()),
		static_cast<unsigned long long>(m.v4eExecuted()));
	if(dspPair)
	{
		std::printf("dsp        : after the boot\n%s", dspPair->report().c_str());
		// O16c: the boot above ran the pair tick by tick (no Rtos yet to
		// sync it, and the boot has no sample clock). From here the RTOS
		// runs it lazily -- the same schedule, replayed in chunks -- in
		// every mode unless --dsp-lazy 0 asks for the per-tick path.
		dspPair->setLazy(dspLazy);
	}

	if(profile)
	{
		std::vector<std::pair<uint32_t, uint64_t>> hot(m.profile().begin(), m.profile().end());
		std::sort(hot.begin(), hot.end(), [](const auto& _a, const auto& _b){ return _a.second > _b.second; });
		std::printf("hottest addresses (PC sampled every 64 instructions):\n");
		for(size_t i = 0; i < hot.size() && i < 16; ++i)
		{
			char buf[256] = {};
			m.disassemble(hot[i].first, buf);
			std::printf("   %#08x  %8llu  %s\n", hot[i].first,
				static_cast<unsigned long long>(hot[i].second), buf);
		}
	}

	// -- past the handoff: the RTOS itself (milestone O4) -------------------
	if(stop == ot::Machine::Stop::Handoff)
	{
		std::printf("vbr        : %#x (the firmware's own `movec %%a0,%%vbr` at 0x40000db6)\n", m.vbr());
		ot::Rtos rtos(m, ips, 264e6, frame);
		if(bootLogo)
		{
			ot::Rtos::Quirks q;
			q.skipBootLogo = false;
			rtos.setQuirks(q);
		}
		// The card is attached BEFORE install, as route A attaches it before
		// `Rtos.install()`: its four memory maps have to be in place before
		// anything runs, and the boot's replayed writes must not start a
		// transfer on a window that is about to change owner.
		std::unique_ptr<ot::AtaCard> card;
		if(!cardImage.empty())
		{
			std::ifstream cf(cardImage, std::ios::binary);
			if(!cf)
			{
				std::printf("card       : %s could not be opened\n", cardImage.c_str());
				return 1;
			}
			std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(cf)),
				std::istreambuf_iterator<char>());
			card = std::make_unique<ot::AtaCard>(std::move(bytes));
			rtos.attachCard(*card);
			rtos.setAtaTrace(!ataTrace.empty());
			std::printf("card       : %s, %u sectors\n", cardImage.c_str(), card->totalSectors());
			if(cardRw)
			{
				// O19: the file IS the card from here on -- every sector the
				// firmware writes lands in it as the WRITE completes.
				if(!card->setWriteBack(cardImage))
				{
					std::printf("card rw    : %s could not be opened for writing\n", cardImage.c_str());
					return 1;
				}
				std::printf("card rw    : write-back on -- WRITE SECTORS go through to %s (O19)\n", cardImage.c_str());
			}
		}
		else if(cardRw)
		{
			std::printf("card rw    : --card-rw needs --card\n");
			return 2;
		}
		rtos.dspi().setRtcClock(rtcMode, rtcEpoch);
		if(rtcMode != ot::Dspi::RtcClock::Off || !rtc.empty())
		{
			// Silent in the default batch (its stdout is diffed byte for byte).
			if(rtcMode == ot::Dspi::RtcClock::Off)
				std::printf("rtc        : off (DSPI chip-select 2 is the loopback: the 2000-00-00 dialog)\n");
			else if(rtcMode == ot::Dspi::RtcClock::Host)
				std::printf("rtc        : host clock (DSPI chip-select 2, DS1390 registers, local time)\n");
			else
				std::printf("rtc        : pinned at %lld s since 1970 (UTC, frozen)\n", static_cast<long long>(rtcEpoch));
		}
		rtos.install();
		rtos.setBlockLog(!blockLog.empty());
		if(!blockDump.empty())
			rtos.setBlockDump(blockDump);
		if(dspDrainPaced)
			rtos.setDspDrainPacing(true);
		if(dspPair && !frameTimer)
		{
			rtos.setFrameFromDsp(true);
			std::printf("frame edge : the DSP's bank word (core 0's host port outside a pull); --frame-timer restores the 16-sample timer\n");
		}
		std::ofstream edmaOut;
		if(!edmaLog.empty())
		{
			edmaOut.open(edmaLog);
			rtos.edma().setTransferHook([&](const uint32_t _ch, const bool _paced)
			{
				const auto& e = rtos.edma();
				char line[256];
				std::snprintf(line, sizeof line,
					"kick ch %2u %s sample %.1f saddr %08x soff %d attr %04x nbytes %08x slast %d daddr %08x doff %d citer %04x dlast %d biter %04x csr %04x\n",
					_ch, _paced ? "paced" : "burst", rtos.sample(),
					e.tcdField(_ch, 0, 4), static_cast<int16_t>(e.tcdField(_ch, 4, 2)), e.tcdField(_ch, 6, 2),
					e.tcdField(_ch, 8, 4), static_cast<int32_t>(e.tcdField(_ch, 0xc, 4)), e.tcdField(_ch, 0x10, 4),
					static_cast<int16_t>(e.tcdField(_ch, 0x16, 2)), e.tcdField(_ch, 0x14, 2),
					static_cast<int32_t>(e.tcdField(_ch, 0x18, 4)), e.tcdField(_ch, 0x1c, 2), e.tcdField(_ch, 0x1e, 2));
				edmaOut << line;
			});
		}
		if(!watchMem.empty())
		{
			const auto comma = watchMem.find(',');
			const auto wa = static_cast<uint32_t>(std::strtoul(watchMem.c_str(), nullptr, 0));
			const auto wl = comma == std::string::npos ? 4u
				: static_cast<uint32_t>(std::strtoul(watchMem.c_str() + comma + 1, nullptr, 0));
			rtos.watchMem(wa, wl);
			std::printf("watch-mem  : %#x..%#x\n", wa, wa + wl - 1);
		}
		const auto rs = rtos.run(runMs);
		static const char* const g_rtosNames[] = {"GATE", "TIME", "FAULT", "ILLEGAL"};
		std::printf("rtos       : %s -- %s\n", g_rtosNames[static_cast<int>(rs)], rtos.why().c_str());
		std::printf("             %.2f ms, %zu tasks created, %zu dispatches, %zu ran, "
			"PIT0 fired %llu, %llu idle skips, seeded from %zu boot writes\n",
			rtos.ms(), rtos.created().size(), rtos.dispatches().size(), rtos.ran().size(),
			static_cast<unsigned long long>(rtos.pit0Fired()),
			static_cast<unsigned long long>(rtos.idleSkips()), rtos.seeded());
		std::printf("             DMA timers: DTIM1 (the LED countdown, 8.33 ms) fired %llu, DTIM2 (soft timers, 1 s) fired %llu; "
			"DTIM3 %s\n",
			static_cast<unsigned long long>(rtos.dtimFired(1)), static_cast<unsigned long long>(rtos.dtimFired(2)),
			rtos.quirks().skipBootLogo ? "reads 2.8 s ahead (the boot logo skipped; --boot-logo runs it)" : "counts from zero (the boot logo ran)");
		std::printf("frame      : %s -- %llu frame interrupt(s) taken, %llu eDMA transfer(s) started\n",
			frame ? "on" : "off (route A's default: main unmasks source 1 unconditionally)",
			static_cast<unsigned long long>(rtos.frameCount()),
			static_cast<unsigned long long>(rtos.edmaStarted()));
		std::vector<std::string> problems;
		const bool ok = rtos.gate(&problems);
		std::printf("M6a gate   : %s\n", ok ? "PASS" : "FAIL");
		for(const auto& p : problems)
			std::printf("   - %s\n", p.c_str());
		// THE MOUNT. Reaching the M6a gate is not enough: the card case runs
		// in the SYS task, so the machine has to be parked at main's spin
		// before the request can be posted, and then run on so SYS can do it.
		if(mount && card)
		{
			ot::Rtos::LoadResult load;
			{
				if(pcRing)
					rtos.armPcRing(4096, pcRing);
				const auto forces0 = rtos.forces();
				const auto disp0 = rtos.dispatches().size();
				m.setPeriphTrace(!periphTrace.empty());
				load = rtos.loadProjectLive(setName, projectName, loadMs, 3000.0, namesEarly);
				const auto& r = load;
				m.setPeriphTrace(false);
				std::printf("             card ready: %#x, LOAD PROJECT posted: %s, "
					"PART_PTR: %#x, %.1f ms emulated%s%s\n",
					r.ready, r.posted ? "yes" : "no", r.partPtr, r.ms,
					r.postWhy.empty() ? "" : " | post: ", r.postWhy.c_str());
				// ⚠️ PART_PTR reads bank A's blob base BEFORE any load, so it
				// is not on its own evidence that a project loaded (O7). The
				// bank the engine PARSED is: it comes from the write the
				// BANK= parse makes, and it is the number route A reports.
				std::printf("             names %s the mount; sys's media case %s before the name\n",
					namesEarly ? "BEFORE (--names-early: expect a second load)" : "after",
					r.mediaCaseSeen ? "ran" : "did NOT run");
				std::printf("             saved_bank: %d, final bank: %u%s\n",
					r.savedBank, r.finalBank,
					r.savedBank >= 0 && r.finalBank != static_cast<uint32_t>(r.savedBank)
						? "  (sys applied the engine's own reset-time 'select bank 0' "
						  "after the BANK= parse -- RTOS_FORK.md section 7)" : "");
				{
					static const char* const g_loadStop[] = {"GATE", "TIME", "FAULT", "ILLEGAL"};
					std::printf("             load run ended: %s%s%s\n",
						g_loadStop[static_cast<int>(r.stop)],
						r.stopWhy.empty() ? "" : " -- ", r.stopWhy.c_str());
				}
				std::printf("             forces %llu, dispatches %zu over the load; now in %s at pc %#x\n",
					static_cast<unsigned long long>(rtos.forces() - forces0),
					rtos.dispatches().size() - disp0, ot::taskName(rtos.currentTcb()), m.pc());
				std::printf("             dispatch tail:\n");
				const auto& ds = rtos.dispatches();
				for(size_t i = ds.size() > 14 ? ds.size() - 14 : 0; i < ds.size(); ++i)
					std::printf("               [%9.1f] %-10s pc=%#x\n", ds[i].sample, ot::taskName(ds[i].tcb), ds[i].pc);
				{
					// Vectors by (vector, slot): how many times each was taken,
					// and the tail in order. A slot holding the kernel's
					// trampoline 0x40000d74 is an interrupt nobody claimed.
					std::map<std::pair<uint32_t, uint32_t>, uint64_t> byVec;
					for(const auto& k : rtos.acks())
						++byVec[{k.vector, k.slot}];
					std::printf("             vectors acknowledged (%zu):", rtos.acks().size());
					for(const auto& [key, cnt] : byVec)
						std::printf(" v%#x->%#x x%llu", key.first, key.second, static_cast<unsigned long long>(cnt));
					std::printf("\n             ack tail:\n");
					const auto& ks = rtos.acks();
					for(size_t i = ks.size() > 12 ? ks.size() - 12 : 0; i < ks.size(); ++i)
						std::printf("               [%9.1f] v%#04x lvl %u in %-10s at pc %#x -> slot %#x\n",
							ks[i].sample, ks[i].vector, ks[i].level, ot::taskName(ks[i].tcb), ks[i].pc, ks[i].slot);
				}
				if(pcRing && rtos.pcRingArmed())
				{
					const auto& ring = rtos.pcRing();
					const auto pos = rtos.pcRingPos();
					std::printf("             pc ring (last %zu of %zu instructions since the first ATA command):\n",
						std::min(ring.size(), pos), pos);
					const size_t n = std::min(ring.size(), pos);
					uint32_t last = 0; uint64_t runlen = 0;
					for(size_t i = 0; i < n; ++i)
					{
						const auto v = ring[(pos - n + i) % ring.size()];
						if(v == last) { ++runlen; continue; }
						if(runlen > 1) std::printf(" (x%llu)", static_cast<unsigned long long>(runlen));
						if(i) std::printf("\n");
						std::printf("               %#010x", v);
						last = v; runlen = 1;
					}
					if(runlen > 1) std::printf(" (x%llu)", static_cast<unsigned long long>(runlen));
					std::printf("\n");
				}
				if(!peeks.empty())
				{
					std::printf("             peek:");
					size_t p = 0;
					while(p < peeks.size())
					{
						auto q = peeks.find(',', p);
						if(q == std::string::npos) q = peeks.size();
						const auto addr = static_cast<uint32_t>(std::strtoul(peeks.substr(p, q - p).c_str(), nullptr, 16));
						std::printf(" [%#x]=%#x", addr, m.peek32(addr));
						p = q + 1;
					}
					std::printf("\n");
				}
				if(!periphTrace.empty())
				{
					std::ofstream t(periphTrace);
					for(const auto& e : m.periphTrace())
					{
						char line[64];
						std::snprintf(line, sizeof line, "%c %08x %u %08x %08x", e.kind == 'R' ? 'R' : 'W', e.addr, e.size, e.val, e.pc);
						t << line << '\n';
					}
					std::printf("             periph trace: %s (%zu accesses)\n", periphTrace.c_str(), m.periphTrace().size());
				}
			}
			size_t reads = 0, identifies = 0;
			for(const auto& e : card->log())
			{
				if(e.what == "READ")
					++reads;
				else if(e.what == "IDENTIFY")
					++identifies;
			}
			std::printf("             ATA interrupts taken: %llu; line still asserted: %s\n",
				static_cast<unsigned long long>(rtos.ataInterrupts()),
				rtos.ataLineAsserted() ? "YES" : "no");
			std::printf("             %zu ATA command(s): %zu IDENTIFY, %zu READ, "
				"%llu sector(s) read, %llu written\n",
				card->log().size(), identifies, reads,
				static_cast<unsigned long long>(card->sectorsRead()),
				static_cast<unsigned long long>(card->sectorsWritten()));
			if(!ataTrace.empty())
			{
				std::ofstream t(ataTrace);
				for(const auto& l : rtos.ataTrace())
					t << l << '\n';
				std::printf("             ATA trace: %s (%zu accesses)\n", ataTrace.c_str(), rtos.ataTrace().size());
			}
			// The whole command sequence, in route A's own log order, so the
			// two can be diffed: the FIRST divergence names the defect.
			if(!cmdLog.empty())
			{
				std::ofstream t(cmdLog);
				size_t n = 0;
				for(const auto& e : card->log())
				{
					char line[160];
					// ⚠️ THE FIRST FIELD GROUP MUST STAY BYTE-COMPATIBLE with route
					// A's dump, because diffing the two logs is what proved
					// the port's first 1,407 commands were route A's (O7).
					// Everything O7b needs goes after a `|`, so
					// `cut -d'|' -f1` still reproduces the old form exactly.
					std::snprintf(line, sizeof line, "%s %u %u | #%zu pc %#010x %s",
						e.what.c_str(), e.lba, e.count, n++, e.pc, ot::taskName(e.tcb));
					t << line << '\n';
				}
				std::printf("             cmd log: %s (%zu commands)\n",
					cmdLog.c_str(), card->log().size());
			}
			for(size_t i = 0; i < card->log().size() && i < 12; ++i)
				std::printf("             %-16s lba %-8u count %u\n", card->log()[i].what.c_str(),
					card->log()[i].lba, card->log()[i].count);

			// -- M6c: the sequencer, for real (milestone O6) -----------------
			// Route A's `--sequencer` branch, step for step. The order is
			// load-bearing and every step of it is compensation or detour that
			// route A documents: the bank switch and the sequencer re-select
			// compensate for an emulator ordering defect (the unit comes up on
			// the saved bank and plays it), and the transport start is the
			// "M5 detour" RTOS_FORK.md §5 allows for M6c.
			if(sequencer)
			{
				const int bank = bankOverride >= 0 ? bankOverride : load.savedBank;
				uint32_t finalBank = load.finalBank;
				if(bank >= 0 && finalBank != static_cast<uint32_t>(bank))
					finalBank = rtos.selectBankLive(static_cast<uint32_t>(bank));
				if(mainLevel >= 0)
				{
					const auto g = rtos.setMainLevelLive(static_cast<uint32_t>(mainLevel));
					std::printf("main level : sys command %u posted with %d -> gain table[0] = %#x%s (bit 0 of 0x8000004a = %u)\n",
						ot::g_setMainLevelCase, mainLevel, g, g ? "" : " -- NOT FILLED", m.read8(0x8000004a) & 1);
				}
				const auto pattern = m.peek32(ot::g_curPattern) >> 24;
				const auto seq = rtos.seqSelectLive(finalBank, pattern);
				if(internalClock)
					std::printf("midi byte  : %#04x -> clock receive cleared\n", rtos.internalClock());
				// The frame clock and the exact instruction clock come on
				// HERE, after the boot and the load, exactly as route A turns
				// them on: on hardware the frame exchange runs from boot, and
				// nothing the trig test reads depends on it having done so.
				rtos.setFrame(true);
				// O14: the sentence above was true for the trig tests and FALSE
				// for a recorder armed on step 1: with the DSP started cold at
				// the same instant as the transport, the core-1 read-back the
				// recorder mixes from is zero for its first ~9 frames, and the
				// recorder keeps that silence as the buffer's first ~96
				// samples -- replayed at every retrigger as RTOS_FORK
				// 10.43-10.46's "retrigger gap". Hardware's DSP has been
				// exchanging frames since boot. --pre-roll runs the frame
				// engine N frames before play; the default (0) keeps every
				// earlier report bit-identical.
				if(preRoll > 0)
				{
					const auto f0 = rtos.frameCount();
					// O15e: the frame count moves only in the ack hook, which
					// wakes the burst loop -- an event condition.
					const auto rsp = rtos.runUntil(preRoll * ot::g_framePeriod / ot::g_sampleHz * 1000.0 * 5 + 2000.0,
						[&] { return rtos.frameCount() >= f0 + static_cast<uint64_t>(preRoll); }, ot::Rtos::Changes::OnEvent);
					std::printf("pre-roll   : %llu frame(s) of the frame engine before the transport start (%s)\n",
						static_cast<unsigned long long>(rtos.frameCount() - f0),
						rsp == ot::Rtos::Stop::Gate ? "REACHED" : rtos.why().c_str());
				}
				if(!rtos.startTransportLive())
					std::printf("transport  : FAILED -- %s\n", rtos.why().c_str());
				if(pokeTrig)
					std::printf("poke trig  : track 1 step %d -> mask byte 7 = %#04x\n",
						pokeTrig, rtos.pokeTrig(static_cast<uint32_t>(pokeTrig)));
				if(!pokeAfterLoad.empty())
				{
					size_t q = 0;
					while(q < pokeAfterLoad.size())
					{
						auto e = pokeAfterLoad.find(';', q); if(e == std::string::npos) e = pokeAfterLoad.size();
						const auto one = pokeAfterLoad.substr(q, e - q); q = e + 1;
						const auto eq = one.find('='); if(eq == std::string::npos) continue;
						const auto addr = static_cast<uint32_t>(std::strtoul(one.c_str(), nullptr, 0));
						const auto val = static_cast<uint32_t>(std::strtoul(one.c_str() + eq + 1, nullptr, 0));
						m.write8(addr, static_cast<uint8_t>(val));
						std::printf("poke       : %#x <- %#x (after the load)\n", addr, val);
					}
				}
				rtos.installTrigLog();
				if(!coverage.empty())
					m.setProfile(1);		// every PC from here: the coverage of the frames phase
				// Frame 0 = the first frame delivered after the transport
				// start returned, which is what the cold tool calls frame 0:
				// the two reports compare directly.
				const auto frame0 = rtos.frameCount() + 1;
				const auto ticks0 = rtos.ticks();
				const auto ackTail = rtos.acks().size();
				if(pcRing)
					rtos.armPcRingNow(pcRing);
				const auto target = frame0 + static_cast<uint64_t>(frames);
				const auto rs2 = rtos.runUntil(frames * ot::g_framePeriod / ot::g_sampleHz * 1000.0 * 5 + 2000.0,
					[&] { return rtos.frameCount() >= target; }, ot::Rtos::Changes::OnEvent);	// O15e: the ack hook wakes
				static const char* const g_seqStop[] = {"REACHED", "TIME", "FAULT", "ILLEGAL"};
				std::printf("sequencer  : playing bank %u pattern %u "
					"(re-selected through the load's own last step)\n", seq.first, seq.second);
				std::printf("frames run : %llu since transport start (target %d), run ended %s%s%s\n",
					static_cast<unsigned long long>(rtos.frameCount() - frame0), frames,
					g_seqStop[static_cast<int>(rs2)],
					rs2 == ot::Rtos::Stop::Gate ? "" : " -- ", rs2 == ot::Rtos::Stop::Gate ? "" : rtos.why().c_str());
				// ⚠️ THREE CAUSES, ONE SYMPTOM. A frame that never arrives is a
				// masked source, a source installed at level 0, or a line that
				// is not asserting -- and the eDMA count says whether the
				// handler that did run got as far as kicking its chain.
				std::printf("             INTC0 src 1 (frame): masked %d, icr %u, asserting %d, latch %d; "
					"src 32 (tick): icr %u\n",
					rtos.intc0().masked(1), rtos.intc0().icr(1), rtos.intc0().assertedSource(1),
					rtos.framePending(), rtos.intc0().icr(32));
				{
					std::map<std::pair<uint32_t, uint32_t>, uint64_t> byVec;
					const auto& ks = rtos.acks();
					for(size_t i = ackTail; i < ks.size(); ++i)
						++byVec[{ks[i].vector, ks[i].slot}];
					std::printf("             vectors acknowledged since the transport start (%zu):",
						ks.size() - ackTail);
					for(const auto& [key, cnt] : byVec)
						std::printf(" v%#x->%#x x%llu", key.first, key.second, static_cast<unsigned long long>(cnt));
					std::printf("\n");
					for(size_t i = ks.size() > 8 ? ks.size() - 8 : 0; i < ks.size(); ++i)
						std::printf("               [%9.1f] v%#04x lvl %u in %-10s at pc %#x -> slot %#x\n",
							ks[i].sample, ks[i].vector, ks[i].level, ot::taskName(ks[i].tcb), ks[i].pc, ks[i].slot);
				}
				if(dspPair)
			std::printf("             host port: %llu blocks / %llu words to the DSPs (%llu NON-ZERO), %llu blocks / %llu words back (%llu NON-ZERO, %llu not in time); %llu ticks a burst waited for the DSP to drain\n",
				static_cast<unsigned long long>(rtos.hostBlocksOut()), static_cast<unsigned long long>(rtos.hostWordsOut()),
				static_cast<unsigned long long>(rtos.hostNonZeroOut()),
				static_cast<unsigned long long>(rtos.hostBlocksIn()), static_cast<unsigned long long>(rtos.hostWordsIn()),
				static_cast<unsigned long long>(rtos.hostNonZeroIn()),
				static_cast<unsigned long long>(rtos.hostWordsShort()), static_cast<unsigned long long>(rtos.edma().gatedWaits()));
		if(!blockLog.empty())
		{
			std::ofstream b(blockLog);
			for(const auto& l : rtos.blockLog())
				b << l << '\n';
			std::printf("block log  : %s (%zu blocks)\n", blockLog.c_str(), rtos.blockLog().size());
		}
		std::printf("             ticks %llu, eDMA transfers %llu\n",
					static_cast<unsigned long long>(rtos.ticks() - ticks0),
					static_cast<unsigned long long>(rtos.edmaStarted()));
				if(pcRing && rtos.pcRingArmed())
				{
					const auto& ring = rtos.pcRing();
					const auto pos = rtos.pcRingPos();
					const size_t n = std::min(ring.size(), pos);
					std::printf("             pc ring (last %zu of %zu instructions since the transport start):\n",
						n, pos);
					uint32_t last = 0; uint64_t runlen = 0;
					for(size_t i = 0; i < n; ++i)
					{
						const auto v = ring[(pos - n + i) % ring.size()];
						if(v == last) { ++runlen; continue; }
						if(runlen > 1) std::printf(" (x%llu)", static_cast<unsigned long long>(runlen));
						if(i) std::printf("\n");
						std::printf("               %#010x", v);
						last = v; runlen = 1;
					}
					if(runlen > 1) std::printf(" (x%llu)", static_cast<unsigned long long>(runlen));
					std::printf("\n");
				}
				std::printf("FW_LIVE_NIBBLE (%#x) writes (%zu), frames since transport start:\n",
					ot::g_fwLiveNibble, rtos.liveNibbleLog().size());
				for(const auto& w : rtos.liveNibbleLog())
					std::printf("   frame %5lld track %u byte %#04x  nibble %x  flags %#04x  at pc %#x\n",
						static_cast<long long>(w.frame - frame0), w.index, w.value,
						w.value & 0xf, w.value & 0xf0, w.pc);
				std::printf("FW_TRIG_WORDS (%#x) nonzero writes (%zu)\n",
					ot::g_fwTrigWords, rtos.trigWordsLog().size());
				if(!m6cGolden.empty())
				{
					ot::Rtos::M6c f;
					f.frame0 = frame0;
					f.ticks0 = ticks0;
					f.ticks = rtos.ticks();
					f.frames = rtos.frameCount() - frame0;
					f.savedBank = load.savedBank;
					f.finalBank = finalBank;
					f.seqBank = seq.first;
					f.seqPattern = seq.second;
					rtos.writeM6cJson(m6cGolden, f);
					std::printf("m6c golden : %s\n", m6cGolden.c_str());
				}
			}
		}
		// The boot, the load and (if asked) the sequencer run exactly as
		// above; from here the machine is the client's. `quit` exits here,
		// before the batch reports.
		if(interactive)
		{
			// SET MAIN LEVEL where the batch posts it (after the load, before
			// anything plays) unless --sequencer already did: without it the
			// gain table 0x80003c60 is zero and every voice renders silent
			// (O9b). Costs up to 200 ms emulated before `ready`; the line is
			// in the boot log, above `ready`, as in the batch.
			if(mainLevel >= 0 && !sequencer)
			{
				const auto g = rtos.setMainLevelLive(static_cast<uint32_t>(mainLevel));
				std::printf("main level : sys command %u posted with %d -> gain table[0] = %#x%s (bit 0 of 0x8000004a = %u)\n",
					ot::g_setMainLevelCase, mainLevel, g, g ? "" : " -- NOT FILLED", m.read8(0x8000004a) & 1);
			}
			return serveInteractive(m, rtos, dspPair.get(), card.get());
		}

		if(!watchPc.empty())
		{
			std::printf("watch-pc   : %zu hit(s) (the timestamp is the instruction count: "
				"the BOOT has no sample clock, and a watch that could not see the boot "
				"reported 0 for a boot address whether it ran or not)\n", m.pcHits().size());
			for(const auto& h : m.pcHits())
				std::printf("   [%12llu] at %#x d0=%#x d1=%#x a0=%#x a1=%#x "
					"[sp %#x: %#x %#x %#x %#x %#x] d2-7 %#x %#x %#x %#x %#x %#x a2-6 %#x %#x %#x %#x %#x\n",
					static_cast<unsigned long long>(h.instruction), h.pc, h.d0, h.d1, h.a0, h.a1,
					h.sp, h.stack[0], h.stack[1], h.stack[2], h.stack[3], h.stack[4],
					h.d[2], h.d[3], h.d[4], h.d[5], h.d[6], h.d[7], h.a[2], h.a[3], h.a[4], h.a[5], h.a[6]);
		}
		if(!watchMem.empty())
		{
			// ⚠️ PRINT THEM ALL (capped only against a flood). A watch that
			// hides its hits is the silent-instrument trap route A records
			// twice over: an address that never fired and one that fired every
			// frame have to look different.
			std::printf("watch-mem  : %zu write(s)\n", rtos.memWrites().size());
			for(const auto& w : rtos.memWrites())
				std::printf("   [%10.1f] [%#x] <- %#x (%u) at pc %#x in %s  i=%llu\n",
					w.sample, w.addr, w.val, w.size, w.pc, ot::taskName(w.tcb), static_cast<unsigned long long>(w.instr));
		}

		if(!serialOut.empty())
		{
			for(const auto& [suffix, tx] : {std::make_pair("a", &rtos.serialTxA()),
				std::make_pair("b", &rtos.serialTxB())})
			{
				std::ofstream o(serialOut + "." + suffix, std::ios::binary);
				o.write(reinterpret_cast<const char*>(tx->data()), static_cast<std::streamsize>(tx->size()));
			}
			std::printf("serial out : %s.a (%zu B), %s.b (%zu B)\n",
				serialOut.c_str(), rtos.serialTxA().size(), serialOut.c_str(), rtos.serialTxB().size());
		}
		if(!golden.empty())
		{
			rtos.writeGoldenJson(golden);
			std::printf("golden     : %s\n", golden.c_str());
		}
	}

	if(!coverage.empty())
	{
		std::ofstream t(coverage);
		std::vector<std::pair<uint32_t, uint64_t>> pcs(m.profile().begin(), m.profile().end());
		std::sort(pcs.begin(), pcs.end());
		for(const auto& e : pcs)
			t << std::hex << e.first << ' ' << std::dec << e.second << '\n';
		std::printf("coverage   : %s (%zu distinct PCs from the transport start)\n", coverage.c_str(), pcs.size());
	}
	if(dspPair)
	{
		std::printf("dsp        : at the end\n%s", dspPair->report().c_str());
		if(!dspStopwatch.empty())
		{
			const auto& w = dspPair->stopwatch();
			std::printf("dsp stopwatch: %s -- %llu pair(s), instructions per pair mean %.0f min %llu max %llu\n", dspStopwatch.c_str(),
				static_cast<unsigned long long>(w.n), w.n ? static_cast<double>(w.sum) / static_cast<double>(w.n) : 0.0,
				static_cast<unsigned long long>(w.n ? w.min : 0), static_cast<unsigned long long>(w.max));
			std::printf("             last 24:");
			for(size_t k = w.last.size() > 24 ? w.last.size() - 24 : 0; k < w.last.size(); ++k) std::printf(" %u", w.last[k]);
			std::printf("\n");
		}
		if(!dspPcWatch.empty())
		{
			std::printf("dsp pcwatch: %s, last %zu arrival(s): executed a1:a0 b1:b0 x0 x1 y0 y1 r0 r4 r6 n4 sp r2 m2 r1 n1 r7\n", dspPcWatch.c_str(), dspPair->pcWatchHits().size());
			for(const auto& h : dspPair->pcWatchHits())
				std::printf("             %llu %06x:%06x %06x:%06x %06x %06x %06x %06x %06x %06x %06x %06x %02x %06x %06x %06x %06x %06x\n", static_cast<unsigned long long>(h.executed),
					h.a1, h.a0, h.b1, h.b0, h.x0, h.x1, h.y0, h.y1, h.r0, h.r4, h.r6, h.n4, h.sp, h.r2, h.m2, h.r1, h.n1, h.r7);
		}
		if(!dspWatch.empty())
		{
			std::printf("dsp watch  : %s, last %zu writer(s):\n", dspWatch.c_str(), dspPair->writeWatchHits().size());
			for(const auto& h : dspPair->writeWatchHits())
				std::printf("             pc %#07x <- %06x at executed %llu; last pcs %06x %06x %06x %06x; r0 %06x r4 %06x r6 %06x area %u\n", h.pc, h.val, static_cast<unsigned long long>(h.executed),
					h.last[0], h.last[1], h.last[2], h.last[3], h.r0, h.r4, h.r6, h.area);
		}
		if(!dspWrites.empty())
		{
			std::ofstream t(dspWrites);
			for(const auto& l : dspPair->writeMap())
				t << l << '\n';
			std::printf("dsp writes : %s (%zu frame commands)\n", dspWrites.c_str(), dspPair->writeMap().size());
		}
		if(!dspMap.empty())
		{
			std::ofstream t(dspMap);
			for(const auto& l : dspPair->activityMap())
				t << l << '\n';
			std::printf("dsp map    : %s (%zu frame commands)\n", dspMap.c_str(), dspPair->activityMap().size());
		}
		if(!audioOut.empty())
		{
			for(int core = 0; core < 2; ++core)
			{
				const auto& pcm = dspPair->audioOut(core);
				if(pcm.empty())
					continue;
				const auto path = audioOut + "_core" + std::to_string(core) + ".wav";
				const bool ok = ot::writeWav24(path, pcm, ot::DspPair::g_audioSlots);
				std::printf("audio out  : %s%s, %zu frames x 8 slots (24-bit, 44100 Hz), transport start at frame %llu\n",
					path.c_str(), ok ? "" : " COULD NOT BE WRITTEN", pcm.size() / ot::DspPair::g_audioSlots,
					static_cast<unsigned long long>(dspPair->txAtFirstCommand(core)));
			}
		}
		size_t q = 0;
		while(q < dspPeek.size())
		{
			auto e = dspPeek.find(';', q);
			if(e == std::string::npos) e = dspPeek.size();
			const auto spec = dspPeek.substr(q, e - q);
			q = e + 1;
			int core = 0; char space = 'X'; unsigned addr = 0, len = 8;
			if(std::sscanf(spec.c_str(), "%d:%c:%x,%u", &core, &space, &addr, &len) < 3)
				continue;
			std::printf("             core %d %c:%#07x:", core, space, addr);
			for(unsigned k = 0; k < len; ++k)
			{
				const auto w = space == 'P' ? dspPair->peekP(core, addr + k)
					: space == 'Y' ? dspPair->peekY(core, addr + k) : dspPair->peekX(core, addr + k);
				std::printf(" %06x", w);
			}
			std::printf("\n");
		}
		for(const auto& t : dspPair->trace())
			std::printf("             %s\n", t.c_str());
		if(!dspLog.empty())
		{
			std::ofstream t(dspLog);
			for(const auto& e : dspPair->log())
			{
				char line[96];
				std::snprintf(line, sizeof line, "%-8s core %d %06x  due %llu\n", e.kind, e.core, e.val,
					static_cast<unsigned long long>(e.due));
				t << line;
			}
			std::printf("dsp log    : %s (%zu events)\n", dspLog.c_str(), dspPair->log().size());
		}
	}

	if(!hostPortLog.empty())
	{
		// The raw writes, and the 24-bit words reassembled from the
		// 0x14/0x18/0x1c triples the loader sends (high, mid, low -- the order
		// `0x40001d82`.. writes them). ❌ "0x81 to 0x20000000 is start the DSP"
		// (ARCHITECTURE.md §6) is retracted: the window is the HI08 host-side
		// register file, 0x81 is ICR INIT|RREQ, and 0x8c to 0x20000004 is a
		// host command (HC | vector 0x0c) -- see dsp.h (O8, 8 Sep 2026).
		std::ofstream t(hostPortLog);
		uint32_t w = 0; int have = 0; uint64_t words = 0;
		for(const auto& e : m.hostPortLog())
		{
			char line[128];
			std::snprintf(line, sizeof line, "W %08x %u %04x  pc %#010x  #%llu",
				e.addr, e.size, e.val & 0xffff, e.pc,
				static_cast<unsigned long long>(e.instruction));
			t << line;
			// ✅ THE BYTE LANES, from the loader's own code at 0x40001d74..:
			//   movel %d0,%d1 / swap %d1 / extl %d1 / movew %d1,0x20000014
			//   movel %d0,%d1 / asrl #8,%d1        / movew %d1,0x20000018
			//   movew %d0,0x2000001c
			// so only the LOW BYTE of each halfword matters, and it is bits
			// 23:16, 15:8 and 7:0 in that order. ⚠️ Getting the lanes backwards
			// made word 2 read 0x001003 instead of 0x031000 -- which is the
			// LOAD ADDRESS the loader was called with, and the thing that says
			// the decode is right.
			if(e.addr == 0x20000014)      { w = (w & 0x00ffff) | ((e.val & 0xff) << 16); have = 1; }
			else if(e.addr == 0x20000018) { w = (w & 0xff00ff) | ((e.val & 0xff) << 8); have |= 2; }
			else if(e.addr == 0x2000001c)
			{
				w = (w & 0xffff00) | (e.val & 0xff);
				if((have | 4) == 7)
				{
					char word[32];
					std::snprintf(word, sizeof word, "   word %06x", w);
					t << word;
					++words;
				}
				have = 0; w = 0;
			}
			t << '\n';
		}
		std::printf("hostport   : %s (%zu writes, %llu complete 24-bit words)\n",
			hostPortLog.c_str(), m.hostPortLog().size(), static_cast<unsigned long long>(words));
	}

	// ⚠️ Unmapped memory: route A FAULTS here and this machine answers
	// all-ones, so anything in this list is a place the two emulators can
	// disagree without either of them saying so. Grouped by address, with the
	// PC of the first touch, because the address is the work item.
	if(m.unmappedCount())
	{
		std::map<uint32_t, std::pair<uint64_t, ot::Machine::Unmapped>> byAddr;
		for(const auto& u : m.unmapped())
		{
			auto it = byAddr.find(u.addr);
			if(it == byAddr.end())
				byAddr.emplace(u.addr, std::make_pair(uint64_t(1), u));
			else
				++it->second.first;
		}
		std::printf("auto-mapped: %llu access(es) outside every declared region and window "
			"(%llu read, %llu written), %zu distinct address(es)%s\n",
			static_cast<unsigned long long>(m.unmappedCount()),
			static_cast<unsigned long long>(m.unmappedReads()),
			static_cast<unsigned long long>(m.unmappedWrites()), byAddr.size(),
			m.unmapped().size() < m.unmappedCount() ? " (log capped at 4096)" : "");
		std::printf("             %llu zero page(s) grown, %llu KB -- route A grows the same "
			"four spans (COLDFIRE_PORT.md O5)\n",
			static_cast<unsigned long long>(m.autoMappedPages()),
			static_cast<unsigned long long>(m.autoMappedPages() * 4));
		// By 64 KB page and by the PC doing it -- uncapped, so a loop that
		// runs millions of times is one line rather than a truncated list.
		// Contiguous 64 KB pages are COALESCED into one span: the interesting
		// number is how far a runaway clear reached, and 700 page lines hide it.
		std::map<uint32_t, uint64_t> pages(m.unmappedPages().begin(), m.unmappedPages().end());
		std::printf("             spans:");
		uint32_t runFirst = 0, runLast = 0;
		uint64_t runCount = 0;
		bool inRun = false;
		const auto flush = [&]()
		{
			if(!inRun)
				return;
			std::printf(" %#010x-%#010x x%llu", runFirst << 16, ((runLast + 1) << 16) - 1,
				static_cast<unsigned long long>(runCount));
		};
		for(const auto& [page, count] : pages)
		{
			if(inRun && page == runLast + 1)
			{
				runLast = page;
				runCount += count;
				continue;
			}
			flush();
			runFirst = runLast = page;
			runCount = count;
			inRun = true;
		}
		flush();
		std::printf("\n");
		std::vector<std::pair<uint32_t, uint64_t>> pcs(m.unmappedPcs().begin(), m.unmappedPcs().end());
		std::sort(pcs.begin(), pcs.end(), [](const auto& _a, const auto& _b) { return _a.second > _b.second; });
		std::printf("             pcs:");
		for(size_t i = 0; i < pcs.size() && i < 6; ++i)
			std::printf(" %#010x x%llu", pcs[i].first, static_cast<unsigned long long>(pcs[i].second));
		std::printf("%s\n", pcs.size() > 6 ? " ..." : "");
		std::vector<std::pair<uint32_t, uint64_t>> rpcs(m.unmappedReadPcs().begin(), m.unmappedReadPcs().end());
		std::sort(rpcs.begin(), rpcs.end(), [](const auto& _a, const auto& _b) { return _a.second > _b.second; });
		std::printf("             read pcs:");
		for(size_t i = 0; i < rpcs.size() && i < 6; ++i)
			std::printf(" %#010x x%llu", rpcs[i].first, static_cast<unsigned long long>(rpcs[i].second));
		std::printf("%s\n", rpcs.size() > 6 ? " ..." : "");
		size_t n = 0;
		for(const auto& [addr, e] : byAddr)
		{
			if(n++ == 8)
			{
				std::printf("   ... %zu more logged\n", byAddr.size() - 8);
				break;
			}
			std::printf("   %c%u %#010x  x%llu  first at pc %#010x -> %#x\n",
				e.second.kind, e.second.size, addr, static_cast<unsigned long long>(e.first),
				e.second.pc, e.second.val);
		}
	}

	if(!m.autoPokes().empty())
	{
		std::printf("auto-pokes (completion flags no model answers, %zu):\n", m.autoPokes().size());
		for(const auto& p : m.autoPokes())
			std::printf("   loop %#08x -> wrote %#x to %#08x\n", p.pc, p.value, p.addr);
	}

	if(showPeripherals)
	{
		std::printf("peripheral touches (%zu logged):\n", m.peripheralLog().size());
		size_t n = 0;
		for(const auto& a : m.peripheralLog())
		{
			if(++n > 60)
			{
				std::printf("   ... %zu more\n", m.peripheralLog().size() - 60);
				break;
			}
			std::printf("   %c %#08x size %u = %#x   (pc %#06x)\n", a.kind, a.addr, a.size, a.val, a.pc);
		}
	}
	if(!memDump.empty())
	{
		size_t q = 0;
		while(q < memDump.size())
		{
			auto e = memDump.find(';', q);
			if(e == std::string::npos) e = memDump.size();
			const auto spec = memDump.substr(q, e - q);
			q = e + 1;
			const auto eq = spec.find('=');
			if(eq == std::string::npos)
				continue;
			const auto range = spec.substr(0, eq);
			const auto path = spec.substr(eq + 1);
			const auto comma = range.find(',');
			if(comma == std::string::npos)
				continue;
			const auto addr = static_cast<uint32_t>(std::strtoul(range.c_str(), nullptr, 0));
			const auto len = static_cast<uint32_t>(std::strtoul(range.c_str() + comma + 1, nullptr, 0));
			std::vector<uint8_t> buf(len);
			for(uint32_t k = 0; k < len; ++k)
				buf[k] = m.read8(addr + k);
			std::ofstream f(path, std::ios::binary);
			f.write(reinterpret_cast<const char*>(buf.data()), static_cast<std::streamsize>(buf.size()));
			std::printf("mem dump   : %#x..%#x (%u bytes) -> %s\n", addr, addr + len - 1, len, path.c_str());
		}
	}

	return stop == ot::Machine::Stop::Handoff ? 0 : 1;
}
