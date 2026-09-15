# Independent audit: RML24 AMR reports

## Verdict: PASS

Both reports reproduce as bounded, post-development IQ-only AMR diagnostics.
No material arithmetic, split, leakage, model-selection or provenance mismatch
was found. This PASS does **not** promote either result to pristine confirmation
or evidence about real satellite IQ.

## Delta audit after provenance fixes

**PASS remains unchanged.** The row report now records the exact current
feature-implementation SHA-256
`e991d1a85cb93cbe217066258a536a2a3c1348e447e3e20806e6e28c37bb8037`,
and the recorded value matches the file. The ambiguous project-level
“untouched holdout” wording was removed.

The group plan and report now bind the posthoc evaluator SHA-256
`9115127f35cec2f272fb9da9c0c382b8d93b2e41b2d79fcf4550569b525cc8f4`.
An independently injected wrong evaluator hash was rejected before either
output file was created. The plan explicitly labels this hash as posthoc
reproducibility provenance and not preregistration evidence.

Both reported outcomes are numerically unchanged: row-disjoint top-1/top-3
remain 937/2,646 and 1,466/2,646; group-disjoint remain 154/546 and 271/546.
Focused tests still pass: **4 passed**.

## Independently reproduced results

The audit reused only the frozen 53-dimensional IQ feature extractor. It
independently reimplemented standardization, centroid/ridge fitting, model-bank
selection, prediction, confusion matrices, top-k and stratified metrics.

| Experiment | Holdout | Selected model | Top-1 | Top-3 | Macro recall |
|---|---:|---|---:|---:|---:|
| Row-disjoint | 2,646 | ridge λ=10 | 0.354119 | 0.554044 | 0.354119 |
| Group-disjoint | 546 | ridge λ=0.01 | 0.282051 | 0.496337 | 0.282051 |

For row-disjoint evaluation, 937 records are correct top-1 and 1,466 top-3.
For group transfer, the corresponding counts are 154 and 271. All nested
validation and holdout metrics match the JSON reports, including every element
of both 21×21 confusion matrices, per-class recall, predicted counts, and
accuracy by SNR and symbol rate.

The group-transfer validation has a top-1 tie between ridge λ=0.01 and λ=10
(both 68/252). The frozen bank-order rule correctly selects λ=0.01. In both
recalculations the holdout was loaded only after training, validation fitting
and model selection.

## Split integrity

The row experiment uses disjoint indices `0,37,101,211,337,503` for training,
`601,677` for validation, and `733,977` for holdout in every group. It is
balanced at 378/126/126 records per class, but all three partitions share the
same modulation/SNR/rate cells; it is a row transfer, not cell transfer.

The group experiment independently rederived the frozen SHA-256 ranking. Its
924/126/273 training/validation/holdout groups have zero pairwise overlap and
cover all 1,323 cells. Every class contributes exactly 44/6/13 groups and
176/12/26 records. SNR and rate counts are not exactly equal across splits,
which follows from hash ranking within class and is not claimed otherwise.

## Information boundary

`extract_iq_features` accepts one argument: `iq`. AST inspection found none of
the forbidden label, modulation, SNR, rate, bit-truth, filename or group
identifiers. Both data loaders open only IQ shards and pass only IQ rows into
the extractor. Modulation labels are used for training/validation/scoring;
SNR/rate are used to identify disjoint groups and report strata. They are not
predictor columns. Naturally, the IQ waveform itself may encode SNR and rate;
that is not metadata leakage.

## Source and arithmetic integrity

The manifest SHA matches both plans and reports. All 1,323 IQ shard files were
independently hashed and size-checked: 21,676,201,344 bytes, zero mismatches.
The group plan's frozen feature-code hash and prior row-report hash also match.
Temporary bad-manifest and bad-implementation plans were rejected before
output creation.

Confusion totals equal record counts, confusion rows equal the balanced class
counts, top-1 equals the diagonal sum, macro/per-class recall reproduce from
the rows, predicted counts reproduce from columns, and top-3/SNR/rate metrics
reproduce from independently generated score orderings.

## Prior exposure and claim boundary

The JSON claim boundaries are honest. They disclose that the full BER campaign
had processed all IQ rows, index 733 had already appeared in DSP work, and all
cells contributed other indices to the earlier row-level AMR experiment. The
group plan also explicitly records an earlier transfer execution followed by a
rerun after a metadata-only change. Therefore these are useful
post-development diagnostics, not one-shot or pristine confirmations.

One wording caution remains: the group plan says to score holdout once while
also disclosing the earlier
  execution and rerun. That prevents a one-shot-confirmation interpretation,
  but does not alter the independently reproduced metrics.

Neither artifact supports claims about signal detection, bit/frame recovery,
packet yield, SatNOGS comparison, or real satellite IQ generalization.

## Provenance limitations

- The row report now self-identifies the exact implementation hash, but the
  earlier row plan itself does not prospectively bind it. This improves
  reproducibility; it is not proof of a cryptographic preregistration freeze.
- The group plan now hashes both feature code and evaluator, and the evaluator
  validates both fail-closed. Its evaluator hash is explicitly posthoc, so it
  is reproducibility provenance rather than preregistration evidence.
- Runtime scripts verify the manifest hash, not each shard hash; this audit
  supplied the missing all-shard verification.
- Local mtimes are not an external trusted preregistration log.

Focused tests: **4 passed**. Full suite: **423 passed, 4 skipped, 21 subtests
passed**. The six warnings are existing Python multiprocessing deprecation
warnings, unrelated to AMR.
