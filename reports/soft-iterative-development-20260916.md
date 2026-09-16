# Soft/iterative receiver: September 16 development results

This is an engineering experiment, not a new publication cohort or a claim
that FrameLift universally outperforms other receivers. The original paper
and the running 96-observation scheduler qualification remain unchanged.

## Implemented scope

Native CPU log-MAP sequence detection with ONE-positive posterior/extrinsic
LLRs; an LDPC soft-output API; CRC-gated iterative equalization of explicit
symbol blocks; and an opt-in BCJR/MLSE mono-audio comparison command.
The [protocol and limitations](../docs/soft-iterative-receiver.md) distinguish
these from future joint synchronization/channel inference, IQ cancellation,
repeated-packet combining and additional mission coding profiles.

## Controlled coded-channel experiment

The fixed-seed executable generated 256 CRC-protected 32-byte TM test frames,
encoded with the published TC512 generator. This is a synthetic test link,
not a claim that a particular satellite transmits this combination. Every
receiver sees identical samples and the same **true** three-tap channel/noise
parameters. The frozen pre-run protocol specifies four noise levels, 64 frames
each, a fixed interleaver and damping 0.7. All cases are retained.

| Noise variance | BCJR once + LDPC up to 12 | BCJR once + LDPC up to 48 | Turbo up to 4 × 12 | Added / lost vs single-48 |
|---|---:|---:|---:|---:|
| 0.16 | 64/64 | 64/64 | 64/64 | 0 / 0 |
| 0.36 | 60/64 | 63/64 | 64/64 | 1 / 0 |
| 0.64 | 0/64 | 1/64 | 10/64 | 9 / 0 |
| 1.00 | 0/64 | 0/64 | 0/64 | 0 / 0 |
| Total | 124/256 | 128/256 | 138/256 | 10 / 0 |

Counts require **exact equality to independently retained transmitter bytes**,
not just CRC acceptance. No incorrectly accepted frame appeared in any positive
arm. The new iterative arm also accepted zero out of 64 negative cases: 32
noise-only blocks and 32 deliberately CRC-invalid, validly encoded frames.
This small negative set is not a field false-acceptance bound.

The iterative arm adds 10 frames over the stronger one-pass control: 7.8125%
relative to its 128 successes, or 3.90625 percentage points of the 256 cases.
This percentage is conditional on this chosen synthetic channel/noise mixture,
not an expected operational gain. It is also **not** an A/B against the whole
previous FrameLift release: both synthetic controls already use the new BCJR.

The stronger control and iterative arm have the same maximum 48 LDPC
iterations, but the iterative arm performs extra BCJR passes. They are not
strictly equal-compute or equal-wall-time trials. Summed observed arm wall times
were 0.1657 s, 0.3668 s and 0.4733 s, respectively, on a shared host; these are
diagnostics only, not a throughput or publication speedup result.

Local experiment root (separate frozen executable copies):
`/home/ubuntu/framelift-soft-iterative-20260916.8R7dlV/`.
`synthetic/plan.json` was written before outcomes; `synthetic/report.json`
contains every case, exact expected bytes and per-iteration decisions.

Executable SHA-256 identities for the initial replay:

- Receiver: `0927d8d0621b6bea5df00078fb722f2ad30b3576112f8be45e899a1d11f9114d`.
- Synthetic benchmark: `8fccdeab12f3c2cf71c607c5fb53f19f4e16669b1118087e81690eca8da21661`.

After a style-only iterator change and test-name correction, the final release
was rebuilt. `synthetic-final/report.json` reproduces every positive-case result,
iteration diagnostic and negative control exactly, excluding wall times.
Final executable identities:

- Receiver: `63dce578e02b6085080439748869387661f8eee70395e1defce93f1a86172876`.
- Synthetic benchmark: `c5994c1ad514c610229b9ad8e134c7bc3a0d71694e5c82631f63731eec46fb60`.

## Real-audio controls

Both previously inspected CANVAS controls completed, with no added or lost
frames. The frame sets, not just the counts, are equal.

| Exposed development crop | Adaptive/innovations reference | Matched MLSE | BCJR | Added / lost vs MLSE |
|---|---:|---:|---:|---:|
| 14936372, first 90 s | 9 | 9 | 9 | 0 / 0 |
| 14936407, preserved 120 s crop | 16 | 16 | 16 | 0 / 0 |

For 14936372, the nine received-FCS frames also match the archived **full
progressive** result byte for byte. For 14936407, the 16 frames match the old
matched-point development result byte for byte; that old result is not a full
progressive control. These are two development recordings from the same
satellite, not a representative sample or part of the 96-observation holdout.

The first replay used 18 source fits and 5,568 paired trials; the second used
32 source fits and 7,488 paired trials. Neither had rejected sources or skipped
trials. Reports, source/executable/crop hashes and exact frame sets are retained
in `canvas14936372/` and `canvas14936407/` under the experiment root.

Total audio decode times were 167.56 s and 225.55 s, including the reference
and both paired lanes. On the first replay, the summed MLSE lane time was
5.58 s and the BCJR lane time was 72.43 s. These are shared-host diagnostics,
not whole-decoder speed comparisons, but they do not support a speedup claim.

The audio path does not apply LDPC to uncoded AX.25 and does not infer discarded
IQ phase from FM-demodulated audio. BCJR optimizes bit marginals under its
assumed model, whereas MLSE selects a most likely sequence; better frame yield
from hard BCJR decisions alone is not guaranteed. This result provides no
evidence of an orbital yield improvement.

## Engineering checks

The final stable CPU library run completed with **512 passed, zero failed and
six explicitly ignored tests** (manual/data-dependent checks or subprocess
helpers). This includes five BCJR mathematical/invalid-input tests, four turbo
tests and eight LDPC tests, including published generator checks. All **23 CLI
integration tests** passed, including two new black-box tests. Strict CPU
library/binary/new-example Clippy, formatting, the final release build and exact
synthetic replay parity also passed. CUDA qualification was not run for this
CPU-only increment.

The first full-library run was invalidated by concurrent recompilation of its
own test executable: lifecycle tests resolving or re-executing `current_exe`
failed with missing-file errors. The same affected zero-capture lifecycle test
passed on the stable executable. The complete library rerun then passed without
replacing that executable (912.87 s); this incident must not be represented as
a clean initial pass. The CLI suite completed in 345.81 s.

## Decision

Keep this increment opt-in and experimental. The synthetic feedback result
justifies testing explicitly coded real IQ with the correct mission profile
and channel estimates. It does not justify enabling the more expensive BCJR
audio lane by default or claiming a new state-of-the-art receiver. Before a
publication claim, freeze a new protocol, test estimated rather than oracle
channels, use unseen recordings and larger negative controls, and measure
gain against the complete previous receiver at matched total compute budgets.
