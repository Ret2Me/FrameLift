# RML24 blind-origin-v3 — independent audit

## Verdict: FAIL_CLOSED_PROVENANCE

The numerical result is reproducible, but the preregistered/frozen claim does
not pass a strict independent provenance gate. The current hash chain is
internally consistent; it does not bind a scorer/control/aggregation module
used by the evaluator, and there is no trusted external timestamp or commit.

| Gate | Status |
|---|---|
| hash_and_freeze_chronology | FAIL |
| split_disjointness_and_shards | PASS |
| runtime_truth_isolation | PASS |
| arithmetic_controls_and_deltas | PASS |
| complete_regeneration | PASS_WITH_POSTHOC_METADATA |
| production_default | PASS |
| preregistration_compliance | PASS_WITH_UNPROVEN_CHRONOLOGY |

## Independently recomputed holdout result

| Method | BER |
|---|---:|
| zero shift | 0.477585618897186 |
| acquisition v2 | 0.477738189259047 |
| blind origin v3 | 0.384143751196630 |
| truth-aided shift 8 | 0.456975157955198 |
| truth-aided shift 1024 | 0.166802783362052 |

Pooled, same-cohort deltas are `-0.093441867700555` versus zero
shift and `-0.093594438062416` versus acquisition v2.
The correct-minus-wrong-record delta is
`-0.107731129140341`. All published integer
counts, BER divisions, success thresholds and edge identities reproduce.

The published report does not persist per-record score rows or a paired
uncertainty statistic. The pooled deltas use the same records and denominator,
but a record-level paired analysis cannot be reconstructed from that JSON alone.

## Full regeneration

The frozen command was rerun over transfer and the complete 768-record holdout.
Every persisted holdout and transfer aggregate and the success object match
exactly. The complete JSON is not byte-identical because the published artifact
contains the post-hoc keys `date_field_correction` and `production_interface`,
which the frozen evaluator does not emit. Removing only those keys makes the
scientific core identical, including its canonical SHA-256.

## Findings

- **HIGH — FREEZE-UNBOUND-SCORER:** The evaluator imports scoring, aggregation, edge validation, RNG constants and random controls from work/rml24/diagnose_acquisition_v2.py, but the freeze lock does not hash that file. Changing it after freeze would not be rejected.
- **MEDIUM — CHRONOLOGY-NOT-EXTERNALLY-ANCHORED:** Local mtimes have the declared order, but they and the self-authored frozen_at value are mutable; there is no signed commit or trusted timestamp proving the artifact existed before holdout access.
- **MEDIUM — PUBLISHED-JSON-POSTHOC-MUTATION:** The published evaluation has two fields the frozen evaluator does not emit. Its scientific core regenerates exactly, but the full published file is not a direct byte-identical evaluator output.
- **LOW — NO-PERSISTED-RECORD-PAIRING:** Only aggregates are persisted. Pooled same-cohort deltas reproduce, but record-level paired deltas or uncertainty cannot be independently reconstructed from the report.
- **LOW — SAME-PROCESS-TRUTH:** Truth is loaded before recovery in evaluate_split. No forbidden data flow into recovery was found, but the experiment does not enforce process-level isolation.

## Truth isolation and scope

Static AST inspection found no truth, truth length, record index, SNR, filename
or group input in recovery or the v3 estimator. No forbidden keyword is passed
from the evaluator. Truth is nevertheless loaded before recovery in the same
Python process; the evidence proves interface/data-flow isolation, not process
isolation. The estimator is a frozen lookup by configured modulation and symbol
rate, not a universal blind synchronizer.

Development, transfer and holdout `(SNR, record-index)` keys are pairwise
disjoint. All manifest-selected IQ/bit shard hashes and sizes were checked. The
dataset is correctly described as previously exposed at aggregate level, not a
pristine external holdout.

## Production default

The production constants match the frozen config and the module is not routed
from any other production module. The feature remains opt-in; no default
production path was changed.
