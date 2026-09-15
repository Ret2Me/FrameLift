# Additional signal-confirmed yield metric

Added at user request during the optimized historical benchmark. This is an
explicitly **post-hoc secondary analysis**, not a retroactive change to the
registered primary endpoint. Receiver binaries, cohort order and current decoder
jobs are unchanged.

Main subgroup: exact `waterfall_status == "with-signal"` in the frozen acquired
cohort metadata. There are 24 such recordings out of 266. This is an archived
assessment, not independently verified signal truth or transmitter identity.
`unknown` (239 recordings) is not treated as signal absent. The three explicit
`without-signal` rows have a separate category.

Two overlapping sensitivity categories are also retained: `status == "good"`
(167 recordings) and nonempty archival `demoddata` (151 recordings). Neither is
silently substituted for the explicit waterfall label; archived data presence
alone does not certify a valid received CRC. No subgroup is defined by success
of our new decoder. All cohort labels remain frozen; no API refresh is needed.

For each subgroup, report selected, all-five-complete and pending/incomplete
recordings. Use the complete cases only for decoder yield. Deduplicate exact
FCS-stripped PDU per observation, union DireWolf and gr-satellites, and report:

- Our PDU count and the external-union count on the same recordings.
- Additional and missed PDU counts separately, plus net count.
- Additional and missed PDU bytes (including AX.25 header, excluding FCS).
- Net percentage = 100 × (ours − external union) / external union.
- Counts of observations with gains, losses, and any recovery by our decoder.

Percentage is null when the denominator is zero; empty or incomplete groups
are not evidence of zero yield for their entire selected population. Ratios are
ratios of summed frame counts, not means of per-observation percentages. PDU
repetitions in different observations remain separate observation–PDU pairs;
these figures do not establish global archive novelty.

Tracking implementation: `examples/signal_yield_track.rs` (Rust, nine tests).
Live output:
`work/publication-speed-restart-20260912-v1/signal-strata-v1/latest.json`.
Every changed snapshot is retained. Intermediate scores rely on the running
benchmark's validation and are explicitly provisional. When the final paired
analysis becomes available, the tracker checks its observation coverage and
primary/innovation gains, losses and baselines before marking the output final.
The main registered statistics and confidence intervals remain unchanged; this
new descriptive subgroup analysis has no new confirmatory significance claim.
