#include "periph.h"

#include <ctime>

#include <algorithm>
#include <cmath>

namespace ot
{
	// ---- PIT ---------------------------------------------------------------
	double Pit::periodSamples() const
	{
		const uint32_t pre = (m_pcsr >> 8) & 0xf;
		return static_cast<double>(m_pmr + 1) * static_cast<double>(1u << pre) / m_clockHz * g_sampleHz;
	}

	void Pit::arm(const double _now)
	{
		if(m_pcsr & EN)
		{
			m_expiry = _now + periodSamples();
			m_armed = true;
		}
		else
			m_armed = false;
	}

	uint32_t Pit::read(const uint32_t _off, const uint32_t _size, const double _now) const
	{
		if(_off == 0)
			return m_pcsr;
		if(_off == 2)
			return m_pmr;
		if(_off == 4)
		{
			// PCNTR: what is left of the current period, scaled into the
			// counter's own units. Route A's shape exactly -- the firmware
			// only ever compares it against zero and against PMR.
			if(!m_armed)
				return m_pmr;
			const double period = std::max(periodSamples(), 1e-9);
			const double frac = std::max(0.0, m_expiry - _now) / period;
			return static_cast<uint32_t>(frac * static_cast<double>(m_pmr)) & 0xffff;
		}
		return (1u << (8 * _size)) - 1;
	}

	void Pit::write(const uint32_t _off, uint32_t, const uint32_t _val, const double _now)
	{
		if(_off == 0)
		{
			// PIF is WRITE-1-TO-CLEAR: the incoming bit clears the flag, it
			// never sets it.
			const bool wasEnabled = (m_pcsr & EN) != 0;
			const bool clearPif = (_val & PIF) != 0;
			m_pcsr = (_val & ~PIF) | (m_pcsr & PIF);
			if(clearPif)
				m_pcsr &= ~PIF;
			if((m_pcsr & EN) && !wasEnabled)
				arm(_now);
			else if(!(m_pcsr & EN))
				m_armed = false;
		}
		else if(_off == 2)
		{
			m_pmr = _val & 0xffff;
			// OVW: overwrite the running count immediately. Without it a new
			// period only takes effect at the next expiry.
			if((m_pcsr & OVW) || !m_armed)
				arm(_now);
		}
	}

	uint32_t Pit::advance(const double _now)
	{
		uint32_t n = 0;
		while(m_armed && _now >= m_expiry)
		{
			m_pcsr |= PIF;
			++m_fired;
			++n;
			if(m_pcsr & RLD)
				m_expiry += periodSamples();
			else
				m_armed = false;
		}
		return n;
	}

	void Pit::seed(const uint32_t _pcsr, const uint32_t _pmr, const double _now)
	{
		m_pcsr = _pcsr;
		m_pmr = _pmr;
		arm(_now);
	}

	// ---- DMA timers --------------------------------------------------------
	double DmaTimer::rate() const
	{
		if(!(m_dtmr & RST))
			return 0.0;
		double hz;
		switch((m_dtmr & CLK_MASK) >> CLK_SHIFT)
		{
		case 1:  hz = m_busHz; break;
		case 2:  hz = m_busHz / 16.0; break;
		default: return 0.0;		// 0 = stopped, 3 = the DTIN pin (unmodelled)
		}
		return hz / static_cast<double>(((m_dtmr >> 8) & 0xff) + 1) / g_sampleHz;
	}

	double DmaTimer::modulus() const
	{
		return (m_dtmr & FRR) ? static_cast<double>(m_dtrr) + 1.0 : 4294967296.0;
	}

	uint32_t DmaTimer::count(const double _now) const
	{
		const double c = std::fmod(m_count0 + std::max(0.0, _now - m_t0) * m_rate, modulus());
		return static_cast<uint32_t>(std::fmod(c + static_cast<double>(m_bias), 4294967296.0));
	}

	double DmaTimer::periodSamples() const
	{
		return m_rate > 0.0 ? modulus() / m_rate : 0.0;
	}

	void DmaTimer::freeze(const double _now)
	{
		m_count0 = std::fmod(m_count0 + std::max(0.0, _now - m_t0) * m_rate, modulus());
		m_t0 = _now;
	}

	void DmaTimer::arm(const double _now)
	{
		const double r = m_rate;
		if(r <= 0.0)
		{
			m_armed = false;
			return;
		}
		// The next time the count equals DTRR: ahead of it in this cycle, or
		// after the wrap (restart: DTRR+1 counts; free-run: 2^32). Less than
		// half a count ahead is the match that just fired, seen through
		// floating point -- not a second one.
		const double c = std::fmod(m_count0 + std::max(0.0, _now - m_t0) * r, modulus());
		double ahead = static_cast<double>(m_dtrr) - c;
		if(ahead < 0.5)
			ahead += modulus();
		m_expiry = _now + ahead / r;
		m_armed = true;
	}

	uint32_t DmaTimer::advance(const double _now)
	{
		// The run loop is here after EVERY instruction: nothing but two
		// compares until a match is actually due.
		if(!m_armed || _now < m_expiry)
			return 0;
		uint32_t n = 0;
		const double r = m_rate;
		while(m_armed && r > 0.0 && _now >= m_expiry)
		{
			m_dter |= REF;
			++m_fired;
			++n;
			if(m_dtmr & FRR)
			{
				// The clock after the match restarts the count at 0.
				m_t0 = m_expiry + 1.0 / r;
				m_count0 = 0.0;
				m_expiry = m_t0 + static_cast<double>(m_dtrr) / r;
			}
			else
				m_expiry += 4294967296.0 / r;
			if(n > 100000)		// a reference of a few counts: fire, do not spin (the firmware programs none)
				break;
		}
		return n;
	}

	uint32_t DmaTimer::reg32(const uint32_t _off, const double _now) const
	{
		switch(_off & 0xc)
		{
		case 0x0: return (m_dtmr << 16) | (m_dtxmr << 8) | m_dter;
		case 0x4: return m_dtrr;
		case 0x8: return 0;				// DTCR: no capture input
		default:  return count(_now);	// DTCN
		}
	}

	uint32_t DmaTimer::read(const uint32_t _off, const uint32_t _size, const double _now) const
	{
		// Byte by byte, big-endian, so an access of any width sees the same
		// bytes: the firmware reads DTMR as a word, DTER as a byte, DTCN as a
		// long.
		uint32_t v = 0;
		for(uint32_t i = 0; i < _size; ++i)
		{
			const uint32_t a = (_off + i) & 0xf;
			v = (v << 8) | ((reg32(a, _now) >> (8 * (3 - (a & 3)))) & 0xff);
		}
		return v;
	}

	void DmaTimer::write(const uint32_t _off, const uint32_t _size, const uint32_t _val, const double _now)
	{
		// An acknowledgement (DTER) or a mode-extension write (DTXMR) touches
		// no timing: leave the count and the armed match alone.
		const uint32_t last = (_off + _size - 1) & 0xf;
		const bool timing = (_off & 0xf) < 2 || last >= 4;
		if(timing)
			freeze(_now);		// a DTMR rewrite changes the rate; the count it had carries over
		bool dtcn = false;
		for(uint32_t i = 0; i < _size; ++i)
		{
			const uint32_t a = (_off + i) & 0xf;
			const uint32_t b = (_val >> (8 * (_size - 1 - i))) & 0xff;
			switch(a)
			{
			case 0x0: m_dtmr = (m_dtmr & 0x00ff) | (b << 8); break;
			case 0x1: m_dtmr = (m_dtmr & 0xff00) | b; break;
			case 0x2: m_dtxmr = b; break;
			case 0x3: m_dter &= ~(b & (CAP | REF)); break;			// write-1-to-clear
			case 0x4: case 0x5: case 0x6: case 0x7:
				m_dtrr = (m_dtrr & ~(0xffu << (8 * (7 - a)))) | (b << (8 * (7 - a)));
				break;
			case 0xc: case 0xd: case 0xe: case 0xf:
				dtcn = true;											// any write clears the count
				break;
			default: break;												// DTCR is read-only
			}
		}
		if(!timing)
			return;
		m_rate = rate();
		if(!(m_dtmr & RST) || dtcn)
			m_count0 = 0.0;			// RST low holds the counter at 0; a DTCN write clears it
		m_t0 = _now;
		arm(_now);
	}

	// ---- UART --------------------------------------------------------------
	uint32_t Uart::read(const uint32_t _off, const uint32_t _size)
	{
		// ⚠️ TXEMP (bit 3) as well as TXRDY (bit 2). This model consumes every
		// byte written to the transmit buffer immediately, so its transmitter
		// is always simultaneously READY and EMPTY; reporting TXRDY alone
		// describes a shift register with a byte stuck in it forever, a state
		// this model cannot be in. Route A carries the same gap and never
		// pays for it (see the MOV3Q note in v4e.cpp): the only firmware that
		// polls TXEMP is the EXCEPTION printer, which route A never reaches.
		// ✅ Measured 8 Sep 2026: with TXRDY alone the port spun forever at
		// 0x4003afa0 and the panic it was trying to print was invisible.
		if(_off == 0x04)
			return TXRDY | TXEMP | (m_rx.empty() ? 0 : RXRDY);
		if(_off == 0x0c)
		{
			if(m_rx.empty())
				return 0;
			const auto v = m_rx.front();
			m_rx.erase(m_rx.begin());
			return v;
		}
		if(_off == 0x14)
			return m_imr;
		const auto it = m_regs.find(_off);
		return it != m_regs.end() ? it->second : (1u << (8 * _size)) - 1;
	}

	void Uart::write(const uint32_t _off, uint32_t, const uint32_t _val, const bool _replay)
	{
		if(_off == 0x0c)
		{
			if(!_replay)
				m_tx.push_back(static_cast<uint8_t>(_val));
		}
		else if(_off == 0x14)
			m_imr = _val & 0xff;
		else
			m_regs[_off] = _val;
	}

	// ---- eDMA --------------------------------------------------------------
	uint32_t Edma::field(const uint32_t _ch, const uint32_t _off, const uint32_t _n) const
	{
		uint32_t v = 0;
		for(uint32_t i = 0; i < _n; ++i)
			v = (v << 8) | m_tcd[(_ch & 15) * 32 + _off + i];
		return v;
	}

	void Edma::setCsr(const uint32_t _ch, const uint16_t _v)
	{
		m_tcd[(_ch & 15) * 32 + 0x1e] = static_cast<uint8_t>(_v >> 8);
		m_tcd[(_ch & 15) * 32 + 0x1f] = static_cast<uint8_t>(_v);
	}

	// A host-port channel: either end of the transfer is inside the DSP's
	// window. Route A checks SADDR and DADDR, and nothing else.
	bool Edma::paced(const uint32_t _ch) const
	{
		for(const uint32_t off : {0u, 0x10u})
		{
			const auto a = field(_ch, off, 4);
			if(a >= g_hostPortLo && a < g_hostPortHi)
				return true;
		}
		return false;
	}

	void Edma::start(const uint32_t _ch, const bool _paced)
	{
		if(m_onTransfer)
			m_onTransfer(_ch, _paced);
		if(m_onKick)
			m_onKick(_ch);
		setCsr(_ch, static_cast<uint16_t>(csr(_ch) & ~DONE));
		++m_started;
		if(_paced && m_busPaced && m_canComplete)
		{
			// The chip's own number (periph.h): the burst occupies the
			// FlexBus for four clocks per 16-bit cycle, one cycle per DSP
			// word, and the drain gate holds the completion beyond that if
			// the DSP has not taken the words yet.
			const auto words = static_cast<double>(tcdField(_ch, 8, 4) * minorLoops(_ch)) / 2.0;
			m_due.emplace(_ch & 15, m_now + words * g_fbSamplesPerWord);
		}
		else if(_paced && !(m_drainPaced && m_canComplete))
			// The DSP delivers its frame on ITS clock, so the completion is
			// booked for the next 16-sample boundary -- NOT kick + 16, which
			// gave an 18.5-sample period and dropped every sixth frame, and
			// NOT at once, which re-raised source 15 before state 0 could ack
			// it and left the ISR spinning in state 6. `setdefault`: a channel
			// already booked keeps its EARLIER completion. (Without the
			// cores, that is: with them the DSP's drain IS the clock, and
			// the boundary held every burst a whole frame -- periph.h.)
			m_due.emplace(_ch & 15, m_boundary);
		else if(m_canComplete && !m_canComplete(_ch))
			m_due.emplace(_ch & 15, 0.0);		// due now, held by the gate
		else
			complete(_ch);
	}

	void Edma::complete(const uint32_t _ch)
	{
		if(m_onDone)
			m_onDone(_ch);
		const auto c = csr(_ch);
		setCsr(_ch, static_cast<uint16_t>((c & ~START) | DONE));
		if(c & INTMAJOR)
			m_irq[_ch & 15] = true;
		if(c & MAJORELINK)					// the linked channel is a BURST:
			start((c >> 8) & 0x1f, false);	// it completes with its parent
	}

	void Edma::advance(const double _now)
	{
		for(auto it = m_due.begin(); it != m_due.end();)
		{
			if(_now >= it->second && m_canComplete && !m_canComplete(it->first))
			{
				++m_gatedWaits;
				++it;
				continue;
			}
			if(_now >= it->second)
			{
				const auto ch = it->first;
				it = m_due.erase(it);
				complete(ch);
			}
			else
				++it;
		}
	}

	uint32_t Edma::read(const uint32_t _addr, const uint32_t _size) const
	{
		if(_addr >= g_tcd && _addr < g_tcd + m_tcd.size())
		{
			const auto off = _addr - g_tcd;
			uint32_t v = 0;
			for(uint32_t i = 0; i < _size && off + i < m_tcd.size(); ++i)
				v = (v << 8) | m_tcd[off + i];
			return v;
		}
		const auto it = m_regs.find(_addr);
		return it != m_regs.end() ? it->second : 0;
	}

	void Edma::write(const uint32_t _addr, const uint32_t _size, const uint32_t _val, const bool _replay)
	{
		if(_addr >= g_tcd && _addr < g_tcd + m_tcd.size())
		{
			const auto off = _addr - g_tcd;
			for(uint32_t i = 0; i < _size && off + i < m_tcd.size(); ++i)
				m_tcd[off + i] = static_cast<uint8_t>(_val >> (8 * (_size - 1 - i)));
			// A write that REACHES the CSR and carries START kicks the
			// channel -- route A's `off % 32 + size > 0x1e and (val & START)`.
			if(!_replay && (off % 32) + _size > 0x1e && (_val & START))
			{
				const auto ch = static_cast<uint32_t>(off / 32);
				start(ch, paced(ch));
			}
			return;
		}
		const auto off = _addr - g_base;
		if(off == SSRT && _size == 1)
		{
			if(!_replay)
				start(_val & 0x0f, false);	// a control transfer: bus speed
		}
		else if(off == CINT && _size == 1)
		{
			if(_val & 0x40)
				m_irq = {};
			else
				m_irq[_val & 0x0f] = false;
		}
		else if(off == CDNE && _size == 1)
		{
			if(_val & 0x40)
				for(uint32_t c = 0; c < 16; ++c)
					setCsr(c, static_cast<uint16_t>(csr(c) & ~DONE));
			else
				setCsr(_val & 0x0f, static_cast<uint16_t>(csr(_val & 0x0f) & ~DONE));
		}
		else
			m_regs[_addr] = _val;
	}

	// ---- DSPI --------------------------------------------------------------
	uint32_t Dspi::read(const uint32_t _off, const uint32_t _size)
	{
		if(_off == SR)
			return 0x82000000u | (static_cast<uint32_t>(std::min<size_t>(m_rx.size(), 15)) << 4);
		if(_off == POPR)
		{
			if(m_rx.empty())
				return 0;
			const auto v = m_rx.front();
			m_rx.erase(m_rx.begin());
			return v;
		}
		const auto it = m_regs.find(_off);
		return it != m_regs.end() ? it->second : (1u << (8 * _size)) - 1;
	}

	uint8_t Dspi::rtcRegister(const uint8_t _reg) const
	{
		const auto reg = _reg & 0x1f;
		if(m_rtcHasWrite[reg])
			return m_rtcWritten[reg];
		const auto bcd = [](const int _n) { return static_cast<uint8_t>(((_n / 10) << 4) | (_n % 10)); };
		std::tm tm = {};
		if(m_rtcMode == RtcClock::Fixed)
		{
			// Pinned: UTC, so the same epoch draws the same dialog on every
			// machine (the host mode is local time, as the user's wall clock).
			std::time_t t = static_cast<std::time_t>(m_rtcEpoch);
			gmtime_r(&t, &tm);
		}
		else
		{
			std::time_t t = std::time(nullptr);
			localtime_r(&t, &tm);
		}
		switch(reg)
		{
		case 0x01: return bcd(tm.tm_sec);
		case 0x02: return bcd(tm.tm_min);
		case 0x03: return bcd(tm.tm_hour);
		case 0x04: return static_cast<uint8_t>((tm.tm_wday + 6) % 7 + 1);	// 1 = Monday
		case 0x05: return bcd(tm.tm_mday);
		case 0x06: return bcd(tm.tm_mon + 1);
		case 0x07: return bcd(tm.tm_year % 100);
		default:   return 0;		// 0x00 hundredths, 0x0e status, anything unread
		}
	}

	void Dspi::write(const uint32_t _off, uint32_t, const uint32_t _val, const bool _replay)
	{
		if(_off == PUSHR)
		{
			if(_replay)
				return;
			// PUSHR: CONT bit 31, PCS bits 21-16, the frame's data bits 15-0
			// (the firmware's frames are 8 bits: the low byte).
			const bool cont = (_val >> 31) & 1;
			const auto pcs = (_val >> 16) & 0x3f;
			const auto data = static_cast<uint8_t>(_val);
			uint32_t reply = 0;
			if(m_rtcMode == RtcClock::Off)
			{
				// The loopback, exactly as before the RTC: every chip select
				// answers 0 and nothing is remembered (the batch's default --
				// goldens and serial captures stay reproducible, and agree
				// with route A's stock Dspi).
				m_rx.push_back(0);
				++m_pushed;
				return;
			}
			if(!m_inTx)
			{
				m_inTx = true;
				m_txPcs = pcs;
				m_txFirst = data;
				m_txFrames = 0;
				// Route A answers the first frame of a read with the register
				// too; the firmware takes the value from the second frame's
				// POPR, so this only has to not matter.
				if(pcs == g_rtcPcs && !(data & 0x80))
					reply = rtcRegister(data);
			}
			else
			{
				++m_txFrames;
				const auto reg = static_cast<uint8_t>((m_txFirst & 0x7f) + m_txFrames - 1);
				if(m_txPcs == g_rtcPcs)
				{
					if(m_txFirst & 0x80)
					{
						m_rtcWritten[reg & 0x1f] = data;
						m_rtcHasWrite[reg & 0x1f] = true;
						++m_rtcWrites;
					}
					else
					{
						reply = rtcRegister(reg);
						++m_rtcReads;
					}
				}
			}
			m_rx.push_back(reply);
			++m_pushed;
			if(!cont)
				m_inTx = false;
		}
		else if(_off != SR)
			m_regs[_off] = _val;
	}

	// ---- INTC --------------------------------------------------------------
	uint64_t Intc::asserted() const
	{
		uint64_t a = m_intfrc;
		for(const auto& l : m_lines)
			if(l.second())
				a |= 1ull << l.first;
		return a;
	}

	std::vector<std::pair<uint32_t, uint32_t>> Intc::pending() const
	{
		uint64_t a = asserted() & ~m_imr;
		if(m_imr & 1)			// MASKALL
			a = 0;
		a |= m_intfrc;			// ... but a forced source is delivered anyway

		std::vector<std::pair<uint32_t, uint32_t>> out;
		for(uint32_t s = 1; s < 64; ++s)
			if((a >> s) & 1)
				if(const auto level = m_icr[s])		// ICR 0 is never delivered
					out.emplace_back(level, s);
		std::sort(out.begin(), out.end(), std::greater<>());
		return out;
	}

	bool Intc::top(uint32_t& _level, uint32_t& _source) const
	{
		uint64_t a = asserted() & ~m_imr;
		if(m_imr & 1)
			a = 0;
		a |= m_intfrc;
		uint32_t bestLevel = 0, bestSource = 0;
		while(a)
		{
			const auto s = static_cast<uint32_t>(__builtin_ctzll(a));
			a &= a - 1;
			if(!s)
				continue;
			if(const auto level = m_icr[s]; level > bestLevel)
			{
				bestLevel = level;
				bestSource = s;
			}
		}
		_level = bestLevel;
		_source = bestSource;
		return bestLevel != 0;
	}

	uint32_t Intc::read(const uint32_t _off, const uint32_t _size) const
	{
		if(_off < 0x18 && _size == 4)
		{
			const auto a = asserted();
			switch(_off)
			{
			case 0x00: return static_cast<uint32_t>(a >> 32);
			case 0x04: return static_cast<uint32_t>(a);
			case 0x08: return static_cast<uint32_t>(m_imr >> 32);
			case 0x0c: return static_cast<uint32_t>(m_imr);
			case 0x10: return static_cast<uint32_t>(m_intfrc >> 32);
			case 0x14: return static_cast<uint32_t>(m_intfrc);
			default:   return 0;
			}
		}
		if(_off >= 0x40 && _off < 0x80 && _size == 1)
			return m_icr[_off - 0x40];
		return (1u << (8 * _size)) - 1;
	}

	void Intc::write(const uint32_t _off, const uint32_t _size, const uint32_t _val)
	{
		if(_off == 0x08 && _size == 4)
			m_imr = (static_cast<uint64_t>(_val) << 32) | (m_imr & 0xffffffffull);
		else if(_off == 0x0c && _size == 4)
			m_imr = (m_imr & ~0xffffffffull) | _val;
		else if((_off == 0x10 || _off == 0x14) && _size == 4)
		{
			const auto before = m_intfrc;
			if(_off == 0x10)
				m_intfrc = (static_cast<uint64_t>(_val) << 32) | (m_intfrc & 0xffffffffull);
			else
				m_intfrc = (m_intfrc & ~0xffffffffull) | _val;
			// A force is a RESCHEDULE REQUEST: the run loop has to see it
			// inside the instruction that made it, not at the end of a burst.
			if((m_intfrc & ~before) && m_onForce)
				m_onForce(m_intfrc & ~before);
		}
		else if(_off == 0x1c && _size == 1)			// SIMR: set mask
			m_imr = (_val & 0x40) ? ~0ull : (m_imr | (1ull << (_val & 0x3f)));
		else if(_off == 0x1d && _size == 1)			// CIMR: clear mask
		{
			// ...and MASKALL (IMRL bit 0) with it. 🟡 INFERRED, and route A
			// says why: nothing in the image ever writes IMRH/IMRL (a literal
			// scan found no site), the firmware unmasks only through CIMR, and
			// the unit plainly takes interrupts -- so CIMR must clear MASKALL
			// too or nothing would ever be delivered.
			m_imr = (_val & 0x40) ? 0ull : (m_imr & ~((1ull << (_val & 0x3f)) | 1ull));
		}
		else if(_off >= 0x40 && _off < 0x80 && _size == 1)
			m_icr[_off - 0x40] = _val & 7;
	}
}
