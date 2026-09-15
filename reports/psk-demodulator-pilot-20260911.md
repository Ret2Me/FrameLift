# Native BPSK/QPSK/OQPSK qualification pilot — 2026-09-11

## Decision

The new Rust demodulators are **not ready for a final broad publication benchmark
or unattended production deployment**. The frozen receiver passes its existing
PSK regressions, but expanded tests reveal substantial OQPSK limitations. This
is a completed development qualification of the current implementation, not a
performance improvement, SatNOGS superiority claim, or independent holdout study.
On a positive real PicSat BPSK recording, gr-satellites recovered 57 unique
validated frames while the frozen native receiver recovered zero.

Receiver source and executables were not changed or tuned during this pilot.
The prospective CANVAS/FSK study and its frozen binaries were left untouched.
Test harnesses, plans, results and an independent audit are preserved under
`work/psk-pilot-20260911-v1/`.

## 1. Synthetic end-to-end frame recovery

Each positive case transmits one known 64-byte CSPv1 packet, protected by
shortened Reed–Solomon and received CRC-32C. Success requires the exact expected
bytes after native PSK demodulation, sync, FEC and packet validation.

| Mode | Exact recoveries | Missing | Fraction in this grid |
|---|---:|---:|---:|
| BPSK | 59/60 | 1 | 98.3% |
| QPSK | 44/60 | 16 | 73.3% |
| OQPSK | 32/60 | 28 | 53.3% |
| Combined positives | 135/180 | 45 | 75.0% |

All **18/18 no-transmission controls** were empty. There were zero unexpected
frames and zero execution errors. The controls are zero input, AWGN-only and
unmodulated tone, for every modulation/filter combination. Eighteen clean
controls do not establish a stringent population false-alarm rate.

The grid includes actual TX rectangular and RRC pulse shaping, Gaussian noise,
constant carrier error, linear carrier drift, off-grid clock offsets and both
OQPSK delayed branches. The five SNR labels are complex-sample SNR, **not Eb/N0**.
Each mode aggregates different channel conditions, not a representative sample
of station observations. Shared seeds/noise and just two packet byte strings
also make trials correlated. Do not interpret these percentages as field success
rates or differences in modulation energy efficiency.

The key failure is stressed OQPSK: only **2/10 rectangular and 0/10 RRC** cases
recovered their frame. At 8/16 dB, only **1/8** stressed OQPSK cases succeeded.
Several impairments change together, so this does not identify their individual
causes. The failures and non-monotonic results are retained without retuning.

The 135 recoveries equal 8,640 frame-instance bytes, but reuse **two synthetic
packets**. They are not 135 unique telemetry packets or newly recovered station
data. The separately coded transmitter is stronger than the older regression
fixture, but still belongs to the same project and uses its RS encoder.

Sources: [complete synthetic report](../work/psk-pilot-20260911-v1/synthetic/REPORT.md),
[all 198 case results](../work/psk-pilot-20260911-v1/synthetic/results.json).

## 2. Public RML24/Nature data

The native Rust demodulator processed **288 existing development IQ records**,
each through two predeclared receive filters: 576 completed outputs, zero
execution errors. This is the dataset associated with
[Cognitive Radio for Satellite TT & C System: A General Dataset Using Software-defined Radio](https://www.nature.com/articles/s41597-026-07182-7).
Its records come from hardware-in-loop laboratory RF and modeled channels,
not actual spacecraft passes. These selected records were already exposed in
earlier development and are not a fresh publication holdout.

Selection: BPSK/QPSK/OQPSK × SNR −10/0/10/20 dB × 100/250/500 ksymbol/s ×
record indices 0–7, at 1 Msample/s and 2,048 complex samples per record.
The IQ-only stage chooses a canonical stream separately within each fixed
filter and seals all decisions before a separate process opens reference bits.
No best-filter choice is made after observing the results.

This is a **truth-aided bit diagnostic**, not frame recovery. Scoring searches
integer alignment and permitted constellation ambiguity; missing reference bits
count as errors. The same alignment against a different record is the negative
control. The full generic framed receiver instead visits its configured bank
and validates received CRC; these experiments measure different things.

All +20 dB cells for the predeclared RRC arm, eight records each:

| Mode | ksymbol/s | Bit errors including missing | Errors on aligned overlap | Wrong-record control |
|---|---:|---:|---:|---:|
| BPSK | 100 | 43.54% | 42.45% | 43.17% |
| BPSK | 250 | 11.87% | 0.083% | 45.26% |
| BPSK | 500 | 5.04% | 0.218% | 46.45% |
| QPSK | 100 | 44.57% | 43.89% | 44.73% |
| QPSK | 250 | 11.22% | 2.350% | 46.06% |
| QPSK | 500 | 2.89% | 0.213% | 46.99% |
| OQPSK | 100 | 45.15% | 44.54% | 44.15% |
| OQPSK | 250 | 41.83% | 33.65% | 46.47% |
| OQPSK | 500 | 33.88% | 24.42% | 47.44% |

Thus BPSK/QPSK have useful conditional recovery in some faster/high-SNR cells,
but all 100 ksymbol/s cells are close to the wrong-record control. OQPSK remains
weak. Quoting only the small overlap BER while omitting missing edges would
overstate full-record recovery. The experiment alone does not distinguish
synchronization failures from pulse/profile or dataset alignment mismatch.

Sources: [method and all limits](../work/psk-pilot-20260911-v1/rml24/README.md),
[all 72 strata and 576 scored rows](../work/psk-pilot-20260911-v1/rml24/results.json).

## 3. Captured satellite signals

The real-signal branch tested one CAMRAS IQ capture and two PicSat passband WAVs.
These WAVs preserve a real PSK passband; they are **not FM-demodulated mono audio**.
Their explicit frequency translation and low-pass filtering produce derived IQ.
There is no automatic reinterpretation of ordinary SatNOGS OGG as PSK IQ.

| Input / path | Native unique validated frames | Independent reference |
|---|---:|---|
| CAMRAS/SatNOGS 9850315, BPSK 1200, four 30 s segments | 0 | Station-positive label, but no packet truth |
| PicSat 1200, translated real passband | 0 | No positive reference established in this pilot |
| PicSat 9600, translated real passband | 0 | gr-satellites: 57 unique frames |
| Same PicSat 9600, external frequency-translation/AGC intermediate IQ | 0 | Diagnostic input control |
| Same PicSat 9600, external post-Costas symbols repeated 5× | 57 | Exactly the same 57 frames; acquisition already performed externally |

The CAMRAS capture is interleaved int16 IQ; 48 ksample/s is an explicit archive
contract/sample-duration inference. Segments start at 0, 180, 330 and 480 s,
covering 120 distinct seconds. All 16 scheduled windows completed, with zero
failed windows; a station's `good`/`with-signal` label is not packet ground truth.
The zero result is not proof that the rest of the capture has no recoverable data.

PicSat files come from the versioned
[satellite-recordings repository](https://github.com/daniestevez/satellite-recordings),
checkout `952ddfe53f62a150c53559249c83370630254cab`, with an explicit upstream
BPSK/AX.25 G3RUH profile. The 1200 and 9600 files are separate samples, not two
post-hoc baud guesses on one recording. All native windows completed normally.

An independently written Rust G3RUH/HDLC/CRC-16-X.25/AX.25 UI check on the
external symbol stream found **57 distinct full received-FCS frames**. A root
audit verified that all 57 gr-satellites KISS data packets match these frames
after removing the two FCS bytes. Its 57 KISS timestamp records were excluded.
The native post-Costas diagnostic returned exactly the same complete frame set.
This is evidence that the framing path works for this sample after external
synchronization, **not 57 frames recovered by the native demodulator**.

Provenance correction: the first comparator KISS is 4,073 bytes / SHA-256
`67d5f4c0c2fb4dcb8b6a284ac9efbcf74f60ea0d8ad8a4898144b4e56870e15e`
and contains those 57 unique packets. A second, dump-enabled comparator run
wrote a 3,932-byte KISS / SHA-256
`328eb8a5ac094372cc90b81af9d6452a1448709fa18726e766b10f02bb5cb198`
with 55 unique packets, a strict subset missing two; its saved post-Costas stream
still independently validates all 57. The reason for that external-output
difference is uninvestigated. The first machine report accidentally paired the
first file's size with the second file's hash. It is retained unchanged, with a
[file-specific correction and checks](../work/psk-pilot-20260911-v1/review/real-kiss-identity-amendment.json).
Neither comparator result makes zero native acquisitions a positive qualification.

The failure is localized upstream of validated frame extraction, consistent with
carrier/timing acquisition or tracking limitations. These controls do not isolate
the exact defective algorithm. QPSK/OQPSK still lack a real-capture test with a
bound modulation/rate/framing/integrity profile in this bounded pilot.

Sources: [real-signal notes and reproduction](../work/psk-pilot-20260911-v1/real/README.md),
[input identities and machine-readable results](../work/psk-pilot-20260911-v1/real/real-pilot-result.json),
[independent frame-set equality audit](../work/psk-pilot-20260911-v1/review/real-frame-set-audit.json).

## 4. Verification and computational cost

- The frozen existing library's PSK subset passed **9/9 regressions** in 5.73 s.
- An independently written naive per-bit scorer exactly reproduced **1,152/1,152**
  saved RML24 scores: correct and wrong reference for each of 576 decisions.
  All rows were unique and complete; D4 transformations and an illegal odd-shift
  QPSK trap were checked. There were no duplicate truth rows within groups.
- The synthetic audit matched all 198 plan/result/case identities, full grid
  coverage, validation layers and all aggregate counts.
- Main-agent rerun of the independent audit passed unchanged in 6.15 s.
- Synthetic grid wall time: **12.35 s**, including 9.86 s summed decoding.
- RML24 IQ-stage wall time: **8.18 s**, including shard verification/output;
  summed native demodulation was about 3.68 s.

These are short records on a shared VM, not long-observation latency, an isolated
CPU benchmark, or a speed comparison with SatNOGS/gr-satellites. Two filter
outputs per RML24 record are paired results, not independent observations.

[Independent audit and caveats](../work/psk-pilot-20260911-v1/review/TEST_EVIDENCE_AUDIT.md).

## 5. What must happen next

1. Diagnose acquisition failures separately: off-grid timing/clock tracking,
   carrier estimation/tracking and OQPSK half-symbol alignment. Use independent
   transmitter fixtures and one changed impairment at a time, preserving this
   failed version as the baseline.
2. Require native recovery on an externally decoded positive captured signal;
   use intermediate external streams only to locate the failing layer.
3. Add diverse independent payloads, Eb/N0-calibrated sensitivity curves, long
   recordings, drift/phase-noise/burst tests and substantially more negative data.
4. Only then freeze the revised receiver and evaluate untouched, independently
   selected real satellite observations against matched external baselines.

Adding standard PSK modes is compatibility work, not by itself scientific
novelty. A publishable gain still needs a defined new method, controlled ablations,
independent test data and exact validated unique-frame comparisons.

## Frozen identities

SHA-256, rechecked at handoff:

- Native source `rust/psk.rs`:
  `ca566b7ed3daabda2ed599b8d676e61507f73d7cb55ce8a013f7b7020fd88353`.
- Native CLI `work/psk-support-20260911-v1/telemetry-yield-rs`:
  `7552fe9dcc6606eb95a2b8fd548d0a8cde6351c8ffc72e3b5e6e34661f3fd385`.
- Linked native release rlib:
  `2bc1ba955f4acb3232c47c1fbd3643b8551575a036e0f87f960593d102b77f3a`.
- Synthetic result:
  `24e3eeead096ff7218a7623a333c537adc3a6e0a5078038d628578630908b556`.
- RML24 result:
  `8e4e1267b9df6bd1beb3aa893845dc415493231bd4ca1f517bbd30c5b73e2e88`.
- Real-capture result:
  `5d4b04db79deae1fdac29b3b45e9dc485188b7ded1d415fc5378e576f8ebba63`.
- Existing CANVAS publication binary, unchanged:
  `e59e8dac6991984de1b7b1e7238835dd3ad740150ae5004070c215290ee2fda3`.
- Existing optimized progressive binary, unchanged:
  `28800c819c3a7862250a44d717edec8682a02a05f30c7a2cae5ee0aee2d2b9b4`.

Graphify located historical physical/RML24 references but not this new Rust
integration. The conclusions above are based on current source, immutable native
artifacts and executed tests, not inferred graph edges.
