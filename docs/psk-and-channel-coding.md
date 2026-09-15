# Native PSK, packet framing and channel coding

This is an opt-in extension of the Rust **generic** receiver. It does not
replace the existing progressive FSK/AX.25 audio portfolio, alter its frozen
study binary, or automatically redecode station observations. No runtime
Python, external demodulator or FEC subprocess is required for these paths.

## What is implemented

| Layer | Native implementation | Qualification boundary |
|---|---|---|
| Modulation | `iq_bpsk`, `iq_qpsk`, `iq_oqpsk` to soft bits and existing protocol decoders | Experimental: repaired replay and fresh-seed high-SNR confirmation pass; QPSK engineering-transmitter hybrid passes with external FEC; orbital QPSK/OQPSK yield remains unqualified |
| PSK synchronization | Mth-power coarse CFO; nominal translation; rectangular/RRC filter; fixed and joint carrier/timing banks; extra cubic joint lane at ≤4 samples/symbol; optional BPSK linear-drift fit | Bounded CFO/clock/drift ranges; no blind baud discovery or universal waveform classifier |
| Ambiguities | BPSK polarity; QPSK I/Q swap and independent signs; OQPSK both delayed branches and adjacent-symbol pairings | Ordinary Cartesian/Gray QPSK mappings; not arbitrary differential-QPSK/8PSK/SOQPSK |
| Existing framing | AX.25 UI with received FCS, plain/G3RUH; existing TM/Space Packet/fixed-sync adapters | Protocol configuration remains independent of modulation |
| Added framing | CSP v1/v2; AOS Issue-5; non-truncated USLP and TFDF | Managed lengths/CRC scope required; no mission application decoding, packet reassembly or security processing |
| FEC | GF256 Reed–Solomon; explicit sparse-H soft LDPC | RS(255,223), TC(128,64), TC(512,256) presets; no universal CCSDS FEC claim |
| Randomizers | TM255, TM131071, TC255 | Explicit selection/reset at configured codeword start; TC and TM255 are different sequences |

Detailed format exclusions are in [space-link-support.md](space-link-support.md),
and code conventions/independent vectors in [fec-support.md](fec-support.md).

## Signal and framing contracts

For BPSK, `dsp.baud` is both the symbol rate and uncoded bit rate. For QPSK
and OQPSK it is the **complex-symbol rate**, i.e. half the serialized bit rate.
FEC further changes useful data rate. Do not configure a bit rate as QPSK baud.

PSK requires complex IQ (`ci16_le`, `cf32_le`, `cf64_le`, or validated raw/SigMF
metadata). The new path deliberately rejects mono FM-demodulated OGG/WAV:
that representation does not preserve the required complex PSK phase.
No automatic reinterpretation of audio as IQ is performed.

Use `decimation: 1`, no FSK `cutoff_hz`, and `dsp.bank: "full"`. Optional
`waveform.psk` selects `max_carrier_offset_hz`, `matched_filter`,
`loop_bandwidth`, `max_carrier_drift_hz_per_s`, `carrier_candidates` and
`differential_binary`. The last option is serialized
binary NRZ-M decoding, **not differential QPSK and not AX.25 NRZI**.
When omitted, settings are rectangular receive filtering, residual-CFO bound
0.2 times baud, loop bandwidth 0.01 radians/symbol, and no NRZ-M.
`carrier_hz` is an optional nominal translation before residual-CFO search.
RRC accepts `{"type":"root_raised_cosine","rolloff":0.35,"span_symbols":6}`.

`carrier_candidates` accepts1..3 and defaults to1, preserving the original
single-carrier bank and default serialized configuration. Opting into2 or3
retains that full bank first, then runs additional banks centered on separated
signal-ranked Mth-power spectral peaks. Candidate ranking uses only IQ, not
decoded bytes, CRC outcomes or reference bits. Peak separation is0.02 times
baud; each alternative's local residual bound is the smaller of0.01 times baud
and the distance to the original search boundary. Unavailable/boundary peaks
are skipped rather than replaced with guessed candidates. The entire coarse
search interval must lie strictly inside IQ Nyquist. Extra stream labels record
the peak rank and exact f64 offset bits; old stream labels remain unchanged.

All requested banks, optional BPSK drift work and peak selection are included
in admission limits. A previously admitted large single-bank window can therefore
be rejected when this option is enabled: shorten windows explicitly, never
silently prune hypotheses. This opt-in integration passes the scoped development
checks below; its exposed K2SAT hybrid success is not a universal recovery gain. The pinned10
integration passes exact-default/retained-bank checks on all144 configured
development cases and recovers88/88 reference packets in its actual in-program
matched-window K2SAT test; all24 fresh noise controls remain empty.

`max_carrier_drift_hz_per_s` defaults to `null` (off) and is BPSK-only. An explicit
positive value enables an additional signal-only linear-frequency-drift fit;
both fitted endpoint frequencies must lie inside `max_carrier_offset_hz`.
The complete constant-CFO bank stays enabled even if a bounded drift fit cannot
be formed. The fit uses at most 96 fixed-position 128-symbol square-law spectra
and coherence-weighted regression, never decoded bytes or CRC feedback. It is
not a general nonlinear Doppler model. This component remains under qualification.

With nonzero `loop_bandwidth`, the original fixed and linear-joint paths stay
available; ≤4 samples/symbol additionally enables four-point Lagrange joint
interpolation. Near record endpoints that lane uses the exact linear fallback.
All low-SPS PSK modes additionally try a separate cubic lane with twice the timing
bandwidth (capped at 0.2) while retaining the original carrier bandwidth. The
lane is omitted when both bandwidths would already be 0.2. Its additional work
is counted in all stream/frontend admission bounds; no previous lane is removed.
Zero loop bandwidth disables all joint lanes, not the nominal matched filter.

The new `coded_sync` protocol follows this exact order:

```
soft bits → configured sync → derandomize codeword → RS/LDPC (optional)
          → decoded frame bytes → structure + received CRC/FECF → result
```

The sync marker is **outside** both the randomized region and FEC codeword.
Frame length includes the received CRC/FECF but excludes sync and parity.
Bytes are MSB-first; positive soft values mean ONE. FEC always sees soft
values centered on the demodulator's threshold, not arbitrary ±1 hard decisions
(RS itself is a hard symbol decoder). TC512's 512 bits decode to 32 bytes;
TC128 decodes to 8 bytes. These presets alone do not implement full TC CLTU
framing/tails. The generic sync wrapper can be configured for a mission's
actual layout; an example combination is not evidence a satellite uses it.

Link CRC/FECF is mandatory in `coded_sync`; the standalone `parse-space-link`
command can also parse structural-only configurations and labels them as such.
Valid FEC syndrome, a found sync marker or a plausible header alone are never
sufficient to accept a frame. Wrong-CRC data encoded into a valid RS/LDPC
codeword remains rejected. Neither CRC nor FEC is cryptographic authentication.

## Running

The checked-in [QPSK/CSP/RS example](../configs/native/qpsk-csp-rs-example.json)
is an explicit **illustrative link**, not a satellite catalogue profile.
Its rate, frame length, sync, coding, randomizer and CRC scope must match the
transmitter. CSP is a packet/network format and does not define one universal
radio sync marker or modulation.

```sh
rtk proxy target/release/telemetry-yield-rs decode --input /absolute/capture.cf32 --plan configs/native/qpsk-csp-rs-example.json --output /absolute/new-session --threads 4
rtk proxy target/release/telemetry-yield-rs decode --input /absolute/capture.cf32 --plan configs/native/qpsk-csp-rs-example.json --output /absolute/new-session --threads 4 --resume
rtk proxy target/release/telemetry-yield-rs fec-profile --profile ccsds-rs255-223 --shortening 191
rtk proxy target/release/telemetry-yield-rs fec-profile --profile ccsds-tc128
rtk proxy target/release/telemetry-yield-rs fec-profile --profile ccsds-tc512
```

`parse-space-link` reads JSON `{config,frame_hex}` from stdin and returns
parsed header/payload, validation layers and explicit integrity status.
`decode-codeword` reads `{code,soft,decoded_bytes}` and returns decoded bytes,
correction/iteration diagnostics and `telemetry_validated:false`: it does not
run a packet validator. `fec-profile` prints a validated JSON configuration.

Generic checkpoint contracts bind waveform/PSK configuration, protocol/FEC,
source and executable identity. Completed windows can be resumed with the
same configuration. Changing a code matrix, randomizer, loop parameter or
binary requires a new session. PSK provenance records the actual phase/rate,
branch/pairing and bit-mapping variant. Parallel workers process independent
windows; one window's PSK variants are visited serially to avoid retaining
many full soft streams in memory.

## Limits and non-claims

- This extension does not make `decode-progressive` a PSK/CCSDS portfolio;
  use generic `decode` / `decode-metadata` with explicit plans.
- PSK permits 2–128 samples/symbol, 4–128 phases, ±10,000 ppm configured
  clock errors and at most 32,768 streams per waveform. Each waveform/window
  has a conservative 200-million soft-bit-visit and 500-million frontend-CPU-work
  bound, including FIR, FFT and tracking; aggregate PSK
  hypotheses have a one-billion bound per planned window. These are not wall
  deadlines or bounds on subsequent FEC work. Excess configurations fail;
  no hypotheses are silently removed. Total observation cost still scales
  with the explicit window schedule.
- Coded sync is 32–128 bits, with at most two mismatches. Candidate limits
  bound per-stream work, not false-alarm probability. Reaching a limit fails
  the attempt rather than publishing a partial successful result.
  The product of matched candidates and configured FEC effort also has a
  one-billion conservative-work-unit cap per soft stream. This is not a wall
  deadline. Generic frame output retains syndrome validation provenance;
  detailed correction counts/iterations are currently exposed by
  `decode-codeword`, not by each generic frame record.
- Repeated searches multiply chances of accidental CRC matches. CRC16 is not
  proof of spacecraft identity or a calibrated statistical confidence level.
  Generic protocol labels do not assign a NORAD/mission identity. Mission
  metadata and false-alarm/held-out controls are needed for scientific yield
  claims. No zero-false-positive guarantee is made.
- Convolutional/Viterbi channel coding, puncturing, AR4JA/DVB-S2 presets,
  SDLS, truncated USLP and cross-frame service reassembly are not implemented.
  AOS Issue-5 10-bit SCID interpretation is explicit; it is not silently
  treated as the older eight-bit field.
- New PSK results are verified on deterministic synthetic IQ, including CFO,
  fractional timing, clock error, noise, sign/conjugation ambiguity and damaged
  codewords. RRC receive-filter coverage does not establish sensitivity on all
  real pulse-shaped transmissions. The historical pilot below failed real
  acquisition; the subsequent repair and whole-file admission checks are tracked
  separately and must not be confused with independent final qualification.

## Repair qualification — 2026-09-12, ongoing

The joint-loop repair improves the original synthetic pilot from 135 to 146
exact recoveries out of 180, and the fresh-seed expanded development grid from
1115 to 1200 out of 1440 with no lost frames. Candidate01 recovers the exact 57
PicSat frames under the original plan. Subsequent honest work accounting exposed
a whole-window admission regression; candidate05's common 500-million CPU cap
fixes it: the exact original file/plan now completes both windows without failures
and recovers the same 57 full-frame bytes. Its full library regression passes
370 tests with zero failures and six deliberately ignored tests.

The fresh low-SPS GNU Radio transmitter suite for candidate04 recovers 848/864
positives and rejects all 864 paired bad-CRC controls. Sixteen stressed OQPSK
cases fail, so **candidate04's strict qualification criterion fails**. With the
separate faster timing-loop lane, candidate07 passes the entire 864-positive/
864-control replay, plus all 192 earlier cases. The balanced 216-case audit
confirms that every retained old soft output is bit-identical. Fresh confirmation
on 6912 cases then finds 3450/3456 positives and 3456/3456 clean bad-CRC controls.
Six stressed low-SPS QPSK cases fail, so candidate07 does not pass that strict
all-cases criterion. These development campaigns are not a publication holdout.
An exact independent mirror reproduces all six QPSK failures. Increasing only
the timing-loop bandwidth recovers all six, while increasing only the carrier
bandwidth recovers none; all paired bad-CRC controls remain rejected. Candidate08
therefore generalizes the appended faster-timing lane to low-SPS BPSK/QPSK as
well as OQPSK, with matching work/stream accounting and a new resume identity.
Its full v3 replay passes all 3456 positives and 3456 controls; a 432-case
independent audit preserves 255744 prior streams and 685791618 soft values.
Its separately frozen fresh v4 confirmation also passes all 3456 positives
and 3456 paired bad-CRC controls. The independent audit reconstructs every IQ
hash and verifies 32 new packet contents and 3456 new noise seeds, disjoint
from v3. This repeats the declared high-SNR impairment envelope, not a new
operational population. All 375 library and 17 release CLI tests
pass, with six library tests explicitly ignored.
RML24 maximum-eye-selected bit results are mixed and worsen overall after the
extra cubic lane; they are not full-portfolio validated frame counts.

On GR01, blind carrier correction recovers the exact external received-FCS
packet, but its malformed AX.25 address-extension bits remain rejected by the
strict UI validator. CRC recovery and AX.25 semantic acceptance are separate
outcomes; no header repair or relaxed validation is silently applied.

See [repair report and immutable evidence](../reports/psk-demodulator-repair-20260912.md).

Candidate07's full library regression passed 375 tests (zero failures, six
ignored), and all 17 CLI tests passed against the frozen release executable.
The rolling coded-sync optimization preserves the independently checked full
result/error oracle; one paired complete IQ-to-frame benchmark reduced elapsed
time by 7.43%. This is a generated BPSK workload, not a universal speed claim.

The independently reconciled nine-case real BPSK expansion gives candidate07
five strict UI frames versus five for gr-satellites, but **different sets**:
one additional received-FCS-valid PW-Sat2 frame and one missed MYSAT-1 frame.
The PW-Sat2 difference repeats on unchanged inputs. MYSAT-1 is recovered by
the unchanged native engine with a wider CFO bound and loop bandwidth 0.05,
so that is a configuration result, not evidence of a new algorithm. Whole
capture and five-second schedules both recover its exact comparator PDU.
The original cohort result remains unchanged; a full-cohort configuration
ablation and matched-schedule controls are separate development experiments.
The completed full-cohort configuration ablation and candidate08 combined-plan
integration recover six strict frames versus the original continuous comparator
five, without missing any comparator frame. Retaining both loop bandwidths is
essential: changing all attempts to 0.05 loses two other recordings' frames.
This is a small post-result development gain, not a population yield estimate.
The completed matched-schedule control explains the extra PW-Sat2 frame:
both engines recover three with five-second resets and two with continuous
whole-record processing. Candidate08's combined plan and window-matched
gr-satellites have **exactly the same six-frame sets on all nine cases**.
Thus the original apparent surplus is not an algorithm-superiority claim.
Eight whole-record cases are eligible (seven exact equal, MYSAT still missed
under the original 0.01 plan); one oversize PW-Sat2 window is ineligible due
to the declared work cap, not a zero-frame success.

Five further predeclared archived BPSK recordings yield three strict PDUs with
candidate08 and two with gr, including one native-only Zhou beacon PDU.
That small positive survives exact-IQ repeats, the comparator's original-WAV
route and two fixed trailing-zero EOF controls. A stronger fixed comparator
acquisition portfolio was checked separately as described below; this is not a population
result or evidence of broad PSK superiority.

That fixed four-arm comparator portfolio has now completed without an extra
strict-UI PDU. Its raw-FCS-attested output also includes nonconformant headers,
so the strict endpoint is not a count of all recovered telemetry. A separate
symmetrical native raw-FCS diagnostic observes both gains and misses; see the
current repair report rather than interpreting strict9/gr7as a general advantage.

Candidate09 keeps candidate08's PSK source unchanged and makes the RS basis
tables compile-time immutable. All 376 library tests (6 deliberately ignored)
and 17 CLI tests pass, together with a byte-exact independent coded-output
oracle. Small paired performance gains depend on workload; no universal
whole-receiver speedup is established.

The frozen single-pass K2SAT engineering-transmitter hybrid gives native0/
reference88. A subsequent audited signal-ranked three-pass component test
recovers all88 exact reference payloads, with237 eligible cells and all24 noise
controls empty. The actual optional in-program carrier portfolio also passes
a separate matched-window test: default1=0,portfolio3=88/88,590 eligible cells,
179872 unique packet-body bytes and all24 fresh noise controls empty. Its
measured native-plus-external-grading cost is about2.96 times the single pass;
this is not a pure native-versus-gr-satellites speed comparison.
The successful hybrid still uses external Viterbi/differential/V.35 grading;
it is not native protocol support or orbital-observation evidence.

A fresh-seed 83520-call sensitivity study is independently audited: candidate06
9689/20736 positive cases versus candidate09 9862/20736, +173 recoveries with
zero losses or unrelated packets. These are simulated cases, not unique real
telemetry frames; 32 payload groups are reused across the SNR/channel grid.
Both receivers accept0/288 AWGN-only cases. All1728 positive24dB SPS2/4 cases
pass, but99 SPS8 high-SNR failures remain in both versions. The full curves
and control qualifications are in the current repair report. A separately
audited, outcome-selected SPS8 ablation confirms complementary timing/carrier
effects: additional cubic+fast timing rescues70/99, while a post-hoc union of
all declared interventions covers83/99. No combined repair has been qualified;
broader-SPS final readiness is not established.

## Qualification pilot — 2026-09-11

The frozen native receiver was tested without algorithm changes:

- Exact synthetic frame recovery: BPSK 59/60, QPSK 44/60, OQPSK 32/60;
  18/18 no-transmission controls clean. These are conditional test-grid counts,
  not expected station success rates. The positives reuse two packet byte strings.
- RML24: 288 already-exposed laboratory IQ records, two fixed receive filters,
  576 completed bit-diagnostic outputs. OQPSK and all tested 100 ksymbol/s cells
  remain weak; truth-aided alignment is not validated telemetry recovery.
- Real PicSat BPSK 9600: gr-satellites recovered 57 unique validated frames,
  native demodulation zero. Native framing recovered the same 57 frames only
  after external carrier/timing synchronization, a diagnostic excluded from
  native acquisition qualification. Real QPSK/OQPSK remain unqualified.

The expanded tests reject a broad production-readiness claim. Repair and retest
carrier/timing acquisition and stressed OQPSK before freezing these modes for a
final independent publication benchmark. The existing frozen progressive FSK
campaign is separate and was not changed.

See [full pilot report, controls and immutable artifacts](../reports/psk-demodulator-pilot-20260911.md).

## Sources and novelty

The carrier/timing/ambiguity approach uses established communications methods;
see the [GNU Radio PSK tutorial](https://wiki.gnuradio.org/index.php?redirect=no&title=Guided_Tutorial_PSK_Demodulation)
and [Costas-loop description](https://wiki.gnuradio.org/index.php?title=Costas_Loop).
No claim is made that adding these standard demodulators or FEC algorithms is
a novel scientific contribution.

The coding/randomizer ordering and constants were checked against
[CCSDS 131.0-B-5](https://ccsds.org/Pubs/131x0b5.pdf) and
[CCSDS 231.0-B-4](https://ccsds.org/Pubs/231x0b4e1.pdf).
Graphify located the older Python receiver/PSK boundaries, but its graph did
not describe this new Rust integration; actual Rust source and primary
standards were used for the implementation and coverage claims.
