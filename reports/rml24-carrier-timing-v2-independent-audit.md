# Independent audit: RML24 carrier/timing v2

## Verdict: PASS

No material discrepancy was found. The frozen `carrier_timing_v2` result is
reproducible on all 144 declared holdout records, waveform recovery is isolated
from truth, all reported arithmetic and bootstrap values reproduce, and the
production default remains `legacy`.

## Independent result

The audit recovered both legacy and v2 waveform candidates again from the 144
frozen IQ records, then scored them with an independent FFT-correlation scorer
rather than the evaluator's alignment loop. All 432 per-record comparisons
(legacy, v2, and v2 paired with the wrong record) matched the report, including
errors, overlap, edge erasures, winning shift, polarity, constellation symmetry
and OQPSK timing hypothesis.

Primary +20 dB BPSK/QPSK/OQPSK result:

| Metric | Legacy | carrier_timing_v2 |
|---|---:|---:|
| Records | 36 | 36 |
| Truth bits | 34,820 | 34,820 |
| Errors including edges | 8,206 | 5,194 |
| BER | 0.235669 | 0.149167 |
| Edge erasures | 2,215 | 2,281 |

The independently recomputed delta is `-0.08650201033888569`. The paired
10,000-resample bootstrap with seed `20260902` reproduces the interval
`[-0.13696482088078818, -0.042036003947367166]`. The v2 wrong-record control is
`0.461200459506031`, well separated from the correct-record result.

High-SNR deltas, v2 minus legacy:

| Modulation | Legacy BER | v2 BER | Delta |
|---|---:|---:|---:|
| BPSK | 0.348219 | 0.112292 | -0.235928 |
| QPSK | 0.097932 | 0.097932 | 0.000000 |
| OQPSK | 0.317131 | 0.218840 | -0.098291 |
| GMSK control | 0.210224 | 0.210224 | 0.000000 |

Across all 144 records, legacy BER is `0.3840146148445976`, v2 BER is
`0.3326233326951305`, and the wrong-record control is `0.4612531112387517`.

## Truth isolation

Static inspection shows that `demodulate_unaligned` reads only IQ, modulation,
nominal symbol rate and record count. It does not read `batch.bits` or
`batch.bit_lengths`; truth enters only the final declared scorer after waveform
candidates have been returned.

The audit also used a batch whose `bits` and `bit_lengths` properties throw an
exception on access. Recovery completed for both versions on all 144 records.
Before scoring, v2 consistently emitted one binary candidate for BPSK/GMSK,
one `common_iq_timing` candidate for QPSK, and all six fixed OQPSK
branch/boundary hypotheses. No truth-dependent runtime selector was found.

## Arithmetic and controls

- 144 unique record keys were present, with the exact preregistered indices
  `211,389,641,887` in 36 modulation/SNR/rate cells.
- All 108 variant/modulation/SNR/rate aggregates reproduced from record rows.
- For every record and aggregate, `truth = overlap + edge erasures` and
  `errors = overlap errors + edge erasures`.
- BER and overlap BER reproduce from integer counts.
- Wrong-record truth is the next frozen index cyclically within the same cell.
- The primary population filter, per-modulation guardrails, paired bootstrap
  and all four promotion flags reproduce exactly.
- The 36 primary truth rows have 36 distinct hashes and no identical-prefix
  pairs, so no obvious duplicated-truth artifact was found.

## Provenance and fail-closed behavior

The observable artifact order is plan → plugin/development → evaluator → frozen
config → holdout plan → result. All embedded SHA-256 links match current files.
The complete 20,506,729,517-byte source ZIP was hashed independently and matches
`daac87c...14c1d`. All 72 IQ/bit shard files used by the 36 selected cells were
also hashed and size-checked: 715,185,216 bytes, zero mismatches.

A deliberately altered plugin hash in a temporary holdout plan was rejected
before an output file was created. The frozen evaluator therefore fails closed
when a hashed dependency changes.

The plan-time full plugin hash cannot be reconstructed from the current source,
because adding the opt-in implementation necessarily changed the file. As a
behavioral no-regression check, the audit replayed all 72 records from the
earlier diagnostic created with plugin hash `a9c493...50a764`; every current
legacy candidate matched its previously recorded SHA-256 exactly.

This establishes internal reproducibility, not an external trusted timestamp.
The statement that holdout rows were never read earlier is consistent with the
artifact order and hash chain, but local mtimes and self-authored files cannot
cryptographically prove absence of prior access. The plan also correctly
discloses that earlier dataset-level aggregates were known; this is a
record-level holdout rather than a pristine new-dataset evaluation.

## Version routing and regression tests

The plugin constructor, shard CLI and HDF5 CLI all default to `legacy`.
`carrier_timing_v2` is accepted only through an explicit version argument/flag.
The existing bit-for-bit legacy regression test and the 72-record historical
candidate-hash replay both pass.

Focused audit tests: **28 passed**. Full repository suite: **423 passed, 4
skipped, 21 subtests passed**; the six warnings are pre-existing Python
`multiprocessing` fork deprecation warnings.

## Scope limit

The positive claim is limited to carrier/timing waveform recovery under the
declared benchmark. BER still uses truth to resolve a final ±1024-bit shift,
binary polarity, QPSK D4 symmetry, and the fixed OQPSK hypotheses. This is not
yet an end-to-end blind packet-yield or SatNOGS comparison.
