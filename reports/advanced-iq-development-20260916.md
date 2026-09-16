# Integrated IQ recovery: development qualification

This report covers the optional native Rust rectangular-BPSK IQ pipeline.
It is separate from the frozen scheduler qualification and the historical paper.
The [implementation contract](../docs/advanced-iq-receiver.md) states supported
waveforms, identity rules and unimplemented generalizations.

## Protocol

Fixed seed `0x20260916ab11`. Retain all 112 generated cf32 recordings and all
three arm results. Positive groups: 16 clean, 16 three-tap ISI, 32 two-packet
collisions and 16 four-copy complementary-erasure cases (80 recordings, 112
distinct transmitted frame identities counted within recordings). Negative
groups: 16 noise-only and 16 parity-valid but received-CRC-invalid recordings.
These are short 4 ksample/s fixtures: 2,048 samples (0.512 s) normally and 8,192
samples (2.048 s) for repeated-copy cases, not full satellite passes.

Arms: single BCJR/LDPC; fixed plus varying-channel turbo; and the full union
including checked SIC and protected-key repeat combining. Header parsing stays
identical when combining is disabled. Acquisition, profile and input bytes are
matched. Full transmitted bytes establish correctness externally; the receiver
does not receive them or the true channel parameters. This is an engineering
cohort, not a held-out mission or representative population of satellite signals.
The single arm is a matched control inside this new pipeline, **not** Dire Wolf,
gr-satellites, SatDump or the complete previous FrameLift portfolio.

The development generator uses published TC128 generator rows independently
of the decoder parity matrix. Its CSP/test framing is not attributed to an
actual satellite. The exact noise/amplitude/offset/erasure mixture is frozen in
the pre-run plan. No failed case is removed from the report.

## Results

The complete fixed cohort finished, with every case retained:

| Group | Recordings | Expected frames | Single pass | Turbo/joint | All features |
|---|---:|---:|---:|---:|---:|
| Clean | 16 | 16 | 16 | 16 | 16 |
| Three-tap ISI | 16 | 16 | 16 | 16 | 16 |
| Two-packet collisions | 32 | 64 | 32 | 32 | 64 |
| Four complementary repeated copies | 16 | 16 | 0 | 0 | 16 |
| Wrong received CRC, valid FEC | 16 | 0 | 0 | 0 | 0 |
| Noise only | 16 | 0 | 0 | 0 | 0 |
| Total | 112 | 112 | 64 | 64 | 112 |

All counts require exact agreement with transmitter bytes. Paired comparison
adds **48 correct frames and loses zero**: 32 from cancellation and 16 from
combining. No incorrect accepted frame occurred in any arm, including all three
arms on the 32 negative recordings. This small negative cohort does not bound
operational false-acceptance risk. Repeated transmissions count once per packet
within a recording; the 112 expected frames are not 112 globally distinct byte
strings across the entire cohort.

The 75% relative gain (112 versus 64) belongs to this deliberately constructed
engineering mixture. It is **not** an orbital gain estimate. In particular,
clean/ISI controls were already decodable: this cohort shows no extra gain from
the varying-channel branch. Collision and erasure conditions are chosen to
exercise the implemented mechanisms, not to model the frequency of such events
in a ground-station archive.

Summed decode-only wall times over all recordings were 1.5869 s (single),
1.8182 s (turbo/joint) and 3.0638 s (all). They exclude the CLI provenance/file
publication overhead and were measured on a shared host. These are diagnostics,
not an equal-compute study, throughput guarantee or three-second file SLA.

Local artifacts, including actual cf32 inputs, profiles, separate transmitter
truth, pre-run plan and every result:
`/home/ubuntu/framelift-advanced-iq-20260916.rTiwhD/cohort/`.
The executable copies under its parent directory have these SHA-256 identities:

- IQ benchmark: `0b7f141a2d9288a4b7709a3523ef32b904f57747b1b02a85489ba838a1eaf041`.
- Receiver: `c8c6ea12cc347b3565b2f240f471d03e2e200cc1ffaafbbf0aee6d1b182a551a`.
- Earlier block benchmark replay: `77b1498adbe7cd3f5292d97b536d469967dae712125c96ada0ebca440a192f54`.

`oracle-block-regression/report.json` reproduces the preceding 256-case block
benchmark exactly, including every per-pass decision and all 64 negative
controls, excluding timings. Its result remains 138 versus 128 exact frames;
this is separate from the new estimated-channel IQ cohort.

Independent invocations of the frozen receiver CLI on `collision-00`,
`repetition-00` and `isi-00` produced reports exactly equal to their library
benchmark counterparts: two, one and one accepted frames, respectively.
Those file-based replays are retained as `cli-collision-00/`,
`cli-repetition-00/` and `cli-isi-00/` under the same artifact root.

## Engineering verification

All 24 CLI integration tests passed, including actual IQ-file decoding,
overwrite refusal and explicit CUDA rejection for this CPU-only path. The final
release build, formatting, generated Rust documentation, strict production
library/binary and both new benchmark-example Clippy passed. The final full
library rerun passed **525 tests, zero failed, six explicitly ignored**
(manual/data-dependent checks and isolated-process helpers), in 636.61 s.
Together with the CLI suite this is 549 passing tests. Host compilation with the existing optional
`cuda` feature also passed; no new GPU kernels or GPU qualification are claimed.
An additional exploratory
`--tests -D warnings` lint run exposed pre-existing test-only style warnings in
unchanged modules; this must not be represented as a clean all-target lint pass.
Existing default decoder paths and frozen experiment binaries were not replaced.

The first full library run had 524 passes, six ignores and one failed new
test. Its artificial pilot had a rank-three Gram matrix for a four-parameter
channel fit; the receiver correctly rejected it. The test was corrected to use
a full-rank sync pattern. No receiver algorithm or benchmark waveform changed.
This initial failure is retained here rather than described as an initial
clean regression pass. The corrected targeted test and the full subsequent
rerun both passed. Release benchmark artifacts contain the same receiver
implementation; the only later source correction was inside the test fixture.

## Interpretation

This increment implements usable mechanisms, not a claim of scientific novelty
for BCJR, iterative equalization, Chase combining or cancellation themselves.
A publication claim requires appropriate real mission profiles, held-out raw IQ,
matched complete receiver baselines, substantially larger false-acceptance
controls and isolated compute-budget measurements. Shared-host timings here
are diagnostic only. The new path remains optional and experimental.
