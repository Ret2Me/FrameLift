# Acquisition and HDLC recovery qualification — 2026-09-19

## Result

The optional soft-marker path recovered **78 of 108** positive synthetic cases,
against **72 of 108** with the previous hard-marker configuration: six extra
successful cases, zero lost cases. After the duplicate-candidate fix, none of the
216 negative-control executions emitted a frame. All 324 disabled-feature reports
exactly match the frozen reference reports, and all 324 enabled serial/parallel
report pairs are identical.

The real-IQ pilot has a **null incremental result**: two complete CANVAS recordings,
34 shared windows and four arms produced the same nine unique PDUs. No arm lost a
baseline PDU. Channel memory cost more computation without improving this cohort.

These are **exposed development results**, not a held-out publication test, a
universal false-alarm bound or a demonstrated orbital gain from the new features.

## Implemented scope

| Extension | Entry point | Acceptance / limitation |
|---|---|---|
| Soft marker acquisition | `decode-advanced-iq`, `recovery.soft_acquisition` | Signal-only correlation and bounded search; hard candidates retain priority; received integrity still required |
| Causal channel memory and blind bootstrap | `decode-recovery-hdlc-memory` | Only prior disjoint baseline anchors enter memory; inferred-label fitting and validation regions are excluded from blind acceptance |
| Coherent variable-length HDLC | `decode-recovery-hdlc`, `coherent_cpm` | Repeated flags train an explicit integer-SPS CPFSK/GMSK model; NRZI/G3RUH and received AX.25 FCS; no HDLC SIC |
| FEC-assisted acquisition | Generic `fec_assisted_sync` protocol | Present but damaged marker, RS/LDPC codeword evidence and separate received CRC/FECF; not markerless or K7 acquisition |

All are opt-in. Existing qualified campaign executables and previous paper results
were not replaced. See [soft acquisition](../docs/soft-acquisition.md),
[channel memory](../docs/hdlc-channel-memory.md), [HDLC CPM](../docs/hdlc-cpm.md)
and [FEC-assisted acquisition](../docs/fec-assisted-sync.md) for precise contracts.

## Synthetic paired replay

The replay consumes the **original recorded CF32 inputs**, profiles and expected
results of the existing `recovery_pipeline_benchmark --stress` grid. It verifies
their hashes; no waveform is regenerated for this comparison. The grid is FSK,
GFSK and GMSK × uncoded/K7/LDPC × component-noise sigma 0.10/0.35/0.70 × four
seeds, with a positive, a deliberately wrong-CRC transmission and a noise-only
control at each cell. Reference payloads enter scoring, not the receiver.

The reference arm is the **same fixed-frame IQ pipeline with hard acquisition and
coherent CPM enabled**, not the full public progressive audio receiver and not
Dire Wolf or gr-satellites.

| Arm | Correct positive cases / 108 | Missed | False acceptances / 216 controls | Errors | Summed decode wall time |
|---|---:|---:|---:|---:|---:|
| Hard marker, coherent CPM | 72 | 36 | 0 | 0 | 2.640 s |
| Soft marker, one worker | 78 | 30 | 0 | 0 | 2.998 s |
| Soft marker, four workers | 78 | 30 | 0 | 0 | 3.145 s |

All six additions are GFSK at sigma 0.70, seeds 0 and 3, in each of the three
coding families. The grid transmits the **same eight-byte payload** repeatedly:
six improved cases are not six globally unique telemetry messages. The 108
noise-only executions reuse **12 distinct noise waveforms** across profiles.
Do not treat the 324 cells as independent orbital observations or infer a
population false-accept probability from zero accepted controls.

Times are single-run decoder-call sums on a shared VM, excluding input/report I/O.
They are not isolated performance measurements. Parallelism is not automatically
faster for these small cases. The new and reference arms do not have equal work.

### Failure found and retained

The first replay already recovered 78 positives, but accepted three wrong-CRC
controls: GFSK uncoded (sigma 0.10 seed 2 and sigma 0.35 seed 1), and GMSK uncoded
(sigma 0.10 seed 2). Those original reports remain intact.

Each had one hard-acquired burst and an extra soft timing candidate only 2–5
samples away, at eight samples per symbol. The alternative decoded the deliberately
transmitted invalid trailer `...f3` as `...f2`, restoring a valid checksum through
a **demodulation error**. The decoder did not rewrite CRC bits or search for a
CRC correction. This is not a random 32-bit CRC collision.

The fix applies the **existing signal-only burst identity rule across hard and
soft candidates**, before any decoding: starts less than two candidate symbol
periods apart, and carrier separation below 1% of symbol rate. Hard candidates
retain priority. A weaker same-burst soft candidate cannot trigger a timing retry
merely because the hard candidate will later fail integrity. Distinct carrier
and sufficiently separated timing hypotheses remain eligible.

The original six gains had no hard candidate, so they remain after the fix. The
full unchanged 324-cell grid was rerun, not just the three failing cases. New unit
tests check these failures and carrier/time diversity. This is a **development-set
fix** and requires an untouched cohort before making a publication efficacy claim.
It deliberately gives up soft timing rescue of already acquired bursts; a spurious
hard candidate can suppress nearby soft acquisition. CRC acceptance remains
probabilistic on arbitrary noisy inputs.

The first harness's `added_frames: 9` counted raw additions, including the three
false acceptances. Its per-arm correct count was already 78. The revised harness
retains raw additions but computes headline gain from expected-correct additions
only: **six**, never nine. The old report is preserved rather than edited.

## Complete real-IQ pilot

Sources are historical SatNOGS CANVAS observations 14115025 and 14366383, the same
two original CI16 files and the same 16-second windows/15-second hop as the earlier
pilot. All 17 windows per file were used. Receiver profiles, source hashes and
the complete worklist were frozen before decoding. Original recordings were not
changed, cropped according to outcomes or duplicated.

| Arm | PDUs (14115025 / 14366383) | Added / missed vs local | Wall seconds | CPU seconds |
|---|---:|---:|---:|---:|
| Local sequence | 8 / 1 | 0 / 0 | 11.400 | 12.188 |
| Causal memory | 8 / 1 | 0 / 0 | 32.772 | 33.436 |
| Causal memory + blind bootstrap | 8 / 1 | 0 / 0 | 35.249 | 36.008 |
| Local sequence + coherent CPM | 8 / 1 | 0 / 0 | 15.070 | 15.506 |

All 136 window-arm executions and all eight observation-arms completed without
errors. Frame comparisons deduplicate AX.25 UI PDUs **within each observation**,
exclude the received FCS from payload identity, and independently revalidate the
full received frame's FCS and UI structure. Overlapping windows are not independent
samples. Both observations come from the same station; this is not a representative
multi-station or multi-mission study.

**Confirmed-signal subgroup:** both source files have prior received-FCS/UI
evidence on identical bytes, established before this new replay. Its result is
therefore also nine PDUs, zero additions and zero losses. This evidence definition
is not an independent waterfall annotation and must not be relabelled as one.

All source/runtime/profile identities were checked before and after the run.
Timings are fixed-order measurements on a shared VM. Memory currently repeats the
frontend and tries bounded retained models; that cost is charged and visible.
The CPM arm uses an explicitly larger work allowance. No speed superiority is
claimed. The latest fixed-frame marker deduplication does not enter this separate
HDLC path, whose executable was already frozen before the fix.

## Engineering checks

- Initial complete library suite: **634 passed, six ignored, zero failed**.
- New fix: all 12 acquisition-filter tests passed; direct carrier/time-diversity
  regression passed.
- Final complete library suite: **636 passed, six ignored, zero failed** in
  634.63 seconds, using the frozen final test executable.
- Coherent HDLC: 13 tests passed, including a controlled independent-waveform
  phase-excursion case where baseline 0 becomes one exact 118-byte received-FCS
  frame. This example is not an orbital recovery rate.
- FEC-assisted synchronization: 12 tests passed, including independently generated
  gr-satellites RS words and actual CF32 BPSK file/checkpoint replay; nine legacy
  coded tests also passed. TC fixtures are explicit synthetic envelopes, not a
  claim of standards-compliant mission framing.
- Memory tests cover causal/overlap/source isolation, failed-window rollback,
  expiry, worker parity, negative control and training/validation exclusion.
  The supplied-channel ISI unit test is not an end-to-end learned-channel gain.
- Strict production CPU and CUDA-feature Clippy, including both benchmark examples
  for CPU, passed. Strict library documentation passed. No GPU hardware run occurred.
- CLI integration: **28 receiver, nine research and five archive tests passed**.
- The field benchmark's three unit tests passed; the acquisition benchmark also
  builds as a test target but currently contains no standalone unit tests.

No ignored GPU test is counted as passed and no unchecked GitHub status is
described as green CI. A [compact machine-readable receipt](acquisition-recovery-20260919.json)
retains the counts, scope and artifact identities.

## Reproduction and retained evidence

```sh
cargo build --release --example recovery_pipeline_benchmark \
  --example acquisition_benchmark --example hdlc_memory_benchmark
target/release/examples/recovery_pipeline_benchmark --stress --output original-grid
target/release/examples/acquisition_benchmark \
  --dataset original-grid --output soft-replay
target/release/examples/hdlc_memory_benchmark \
  --source-freeze /path/to/the/original/iq-run-v2/freeze.json \
  --include-cpm --output field-replay
```

A newly generated grid can exercise the code, but exact historical parity requires
the original frozen grid reports. The field runner requires both original IQ files,
the original mission profile, `freeze.json` and the companion `study.json`. Its
default path is laboratory-specific; explicitly supply `--source-freeze` elsewhere.
The repository's compact report does not substitute for an archival dataset DOI.

Laboratory artifacts are under
`/home/ubuntu/framelift-acquisition-20260916.fc1dvz/`: `stress-soft` (initial failure),
`stress-soft-v2` (corrected replay), `field-memory-v1` and test logs. Runtime snapshots
in `/dev/shm` are volatile; compressed retained copies preserve executable bytes.
No original IQ/OGG was removed. Two completed test executables were losslessly
compressed; decompression restores the original bytes.

| Artifact | SHA-256 |
|---|---|
| Initial stress report | `590019d0941c46c2fc5ef65f3e134dbd4347e7f9b0692f160d84a090ad5b300f` |
| Corrected stress report | `1f9fc4ae399070de3f720a5995718fbcd58d6d8132c90f4a4c393ab09578f48d` |
| Corrected stress runner | `8a49e218df231c98a95ae6bac443724b6db2ea1f1ea5c17a9c75acb7ca9562f8` |
| Current receiver | `22915c90a70707e8cb897ae46c9c55dd9369884d89bd78b81918207851ec6eac` |
| Field runner | `aa48574b01f999788ffd0b4cc35edf73ed944363a50a32ffe7a563287360e625` |
| Field freeze | `5aea93c43de42055178f780a6771404fb374058b8257b97a0803c5396550f9e6` |
| Field study | `37c48b399cdf60d44433cf10c4b1676c9faede5aa3fc961801e84fd41b00b8d0` |
| Field comparison | `b5d5b50c3ef8aec9d68933e39bdba1e7b0a4514a63ac441551c9ed2a9e152b72` |
| Final library test executable | `83c03d6602c06d94f2acd621a8fa7c189aee10f63c757dccd9443e45639d2198` |
| Final library test log | `369d7c44cedf43df0e9222b4252eca3da72c2e7b60e2dca0fcd0f21fe4e2d776` |

The appropriate next scientific step is a frozen, independently selected larger
cohort with mission-matched profiles and separate confirmed-signal reporting.
These results justify continuing engineering, not claiming a universally superior
or publication-qualified receiver.
