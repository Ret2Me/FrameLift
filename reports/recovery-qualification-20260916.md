# Recovery extensions: engineering qualification

This is a development qualification of the fixed-frame IQ extensions, not an
orbital yield study or evidence of superiority to an external decoder. Results
from the separate real-recording campaign must not be pooled with these tests.

## Frozen cohorts

The harness is `examples/recovery_pipeline_benchmark.rs`. It writes the complete
plan and executable identity before decoding, preserves each cf32 input and its
SHA-256, and records every result, failure and negative control. The transmitter
independently implements K7 encoding, Reed–Solomon encoding and the waveform.
Payload truth is available only to transmission and post-decoding scoring.

The integration matrix has 26 modulation/coding combinations, each with a
clean valid frame, a frame whose final CRC bit was changed before encoding, and
noise only: 78 files. Baseline disables coherent CPM; the serial and parallel
extension arms enable it for FSK/GFSK/GMSK. These arms are **not** an earlier
FrameLift release, Dire Wolf or gr-satellites.

Each positive contains one eight-byte engineering frame (including its received
integrity trailer), in 8,192 samples at 24 ksample/s. These short fixed links
exercise implementation contracts, not realistic mission packet-size or pass-
duration distributions. Noise sigma is an explicitly generated waveform
parameter, not a calibrated receiver sensitivity or orbital SNR measurement.

The separately declared stress cohort crosses FSK/GFSK/GMSK, uncoded/K7/LDPC,
complex-noise component sigma 0.10/0.35/0.70, and four deterministic seeds:
108 positive files. It includes matched bad-CRC and noise-only controls,
bringing the total to 324 files. Noise is paired across the three groups in
each cell. No parameters or seeds are selected after seeing recovery outcomes.
Repeated noise-only waveforms across modulation/coding cells are not
independent negatives and must not support a binomial confidence interval.
There are only 12 unique noise-only waveforms in the stress grid, each replayed
through nine modulation/coding profiles. The complete 108 bad-CRC waveform
cases remain distinct from those noise-only controls.

## Development failures retained

The pre-boundary-fix run is retained at
`/home/ubuntu/framelift-recovery-pipeline-20260917.Ek6RRU/initial`.
Its baseline recovered 19/26 positives and accepted one negative; the extension
recovered 20/26 and accepted two negatives. This exposed omitted edge
observations in the uncoded BCJR and coherent CPM boundary models. The fix
retains the actual endpoint samples and marginalizes unknown exterior symbols;
it does not change the waveforms or remove failing cases.

The replacement qualification artifacts are stored at
`/home/ubuntu/framelift-recovery-qualification-20260916.uuMlua`.
The first completed fixed receiver run (`fixed-before`, before lossless
performance edits) recovers **26/26** positives in each arm, with **0 false
acceptances**, **0 errors**, and identical serial/parallel reports on **78/78**
files. All 78 input hashes match the earlier failing run, confirming that the
boundary correction was not tested on replacement waveforms.

The stress run (`stress-before`) recovers **67/108** positives in the baseline
and **72/108** in both coherent arms: **5 additional frames, none lost**
(+7.46% relative to baseline; +4.63 percentage points of recovery). There are
**0 false acceptances** in the 216 matched negative files and **0 errors**.
Serial and four-worker reports match exactly on **324/324** files.

| Stress subset | Baseline | Coherent union |
| --- | ---: | ---: |
| FSK, all three coding families, sigma <= 0.35 | 24/24 | 24/24 |
| GFSK, K7/LDPC, sigma <= 0.35 | 16/16 | 16/16 |
| GFSK, uncoded, sigma <= 0.35 | 5/8 | 8/8 |
| GMSK, K7/LDPC, sigma <= 0.35 | 16/16 | 16/16 |
| GMSK, uncoded, sigma <= 0.35 | 6/8 | 8/8 |
| All three modulations/codes, sigma = 0.70 | 0/36 | 0/36 |

All 36 highest-noise failures had zero acquired candidates: the configured
64-bit syncword permits only two hard bit errors. Coherent refinement cannot
recover a burst which never reaches its candidate stage. These misses are
retained; thresholds were not loosened after observing them. This is an
identified acquisition limitation, not evidence of universal recovery.

Frozen pre-optimization pipeline executable SHA-256:
`4ccfca81a92723d16dff83aa060bf0d450953ce9a67bc592272cbde49f23044c`.
Frozen pre-optimization kernel executable SHA-256:
`32d06b0d0a0f6332ff1b1f5d2be432a47ab3d4301945712ff90608ca3e8be686`.

## Final exact-output qualification

Final pipeline executable SHA-256:
`c183a704046dd29b30697bf8240e845353c1371326f4d3b11ee265a44fbd4a5e`.
Final kernel executable SHA-256:
`200e8e239cdd70341c8f462a21ef642572800f370ee6637259cafed2b08bccc7`.

The final implementation retains one pilot fit/metric preparation and reuses
the backward-recursion scratch buffer. An experimental unreachable-state skip
was removed after mixed kernel timings; no such skip is part of the final
performance claim. No likelihood approximation or reduced search was adopted.

All **1,206 distinct per-arm scientific reports** (402 files, three arms) match
the pre-optimization reports byte for byte. Three before/final timing pairs
repeat this comparison: **3,618/3,618 comparisons identical**. All 2,412 file
replays have the canonical input hashes and unchanged counts; serial/parallel
results are identical throughout. These repeats are not additional independent
data. The three complete floating-point kernel output hashes also match the
pre-optimization binary, including posterior and extrinsic LLRs.

The coherent CPM unit suite passes **8/8**, including independent waveform
enumeration and exact single-fit/two-stage metric and output parity. Both
benchmark examples pass strict Clippy; touched Rust files pass rustfmt. The
repository-wide suite and real-recording qualification are reported separately.

## Performance scope

`examples/recovery_kernels_benchmark.rs` measures prepared CPM recursion and
repeated validated FEC-dimension queries, retaining complete floating-point
result hashes. Kernel timings on a shared VM are not end-to-end receiver speed
claims. Parallel scientific-output equality is checked independently of timing.

The first stress run takes 0.58 s baseline, 4.28 s extended serial and 4.43 s
extended four-worker CPU wall time, summed over the cohort and excluding
artifact writing. Four workers do not accelerate these tiny per-file jobs.
Those exploratory VM timings are not a fair comparison against the previous
full release, nor evidence that the added search is free.

Validated LDPC dimension queries measured approximately 1.3–1.6 microseconds
each (10,000 calls). Their public validation was deliberately preserved; no
unchecked dimensions API was added merely to save this small cost.

### Paired final pipeline timing

Three sequential before/final pairs used rotated ordering (before/final,
final/before, before/final), the same release binaries and numeric inputs, and
the same RAM-backed artifact filesystem. Times below are medians of the summed
per-file decode wall time; generation, file writing and program startup are
outside the timed region. Other VM workloads were not suspended, so these are
descriptive engineering measurements, not isolated-hardware performance claims.

| Cohort / arm | Before | Final | Ratio before/final |
| --- | ---: | ---: | ---: |
| 78-file matrix, unchanged baseline control | 1.627 s | 1.604 s | 1.01 |
| 78-file matrix, extended serial | 2.039 s | 1.839 s | 1.11 |
| 78-file matrix, extended four-worker | 2.066 s | 1.949 s | 1.06 |
| 324-file stress, unchanged baseline control | 0.631 s | 0.619 s | 1.02 |
| 324-file stress, extended serial | 4.323 s | 2.852 s | 1.52 |
| 324-file stress, extended four-worker | 4.599 s | 2.983 s | 1.54 |

The serial receiver uses **9.8% less decode wall time** on the integration
matrix and **34.0% less** on the CPM stress grid, with identical scientific
output. This is a measured improvement on these short synthetic cohorts only.
Four-worker execution still has no throughput advantage on these tiny files.

The single-fit preparation removes an entire redundant channel fit and branch-
metric construction. Final three-run kernel preparation ratios were 2.05 for
FSK, 3.06 for GFSK and 2.03 for GMSK. The GFSK ratio varied with shared-host
load (an earlier five-pair measurement was 2.04); describe the optimization as
roughly halving preparation work, not as a guaranteed threefold acceleration.

Persistent `timing-{fixed,stress}-{before,final}-{1,2,3}-{plan,report}.json`
artifacts retain the complete per-case input hashes, outcomes and timings.
The repeated cf32 files and per-case reports are in the temporary RAM-backed
directory `/dev/shm/framelift-recovery-timing.qG7taZ`; they can be reconstructed
from the frozen executable. Canonical inputs and scientific reports remain in
`fixed-before` and `stress-before` on disk. Temporary RAM data is not counted
as durable provenance.

No NVIDIA runtime measurement is available on this host: `nvidia-smi` is not
installed. CPU qualification must not be described as CUDA qualification.
