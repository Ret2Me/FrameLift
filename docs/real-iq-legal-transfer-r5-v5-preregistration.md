# R5-v5 informational null follow-up

Status: **implemented as a draft; independent review is required; not frozen;
not executed**.

R5-v5 is an explicitly labelled follow-up motivated by the investigator-reported
marginal R5-v4 sensitivity failure. It does not alter the frozen R5-v4 endpoint.
The canonical machine-readable draft is
`configs/real-iq-legal-transfer-r5-v5-preregistration-draft.json`.

## Frozen scientific intent

The proposed schedule has exactly three realistic, nonzero informational nulls:

1. Block FFT random-phase surrogate. Each block retains its FFT magnitudes and
   Parseval power before a deterministic, recorded anti-clipping gain and CI16
   rounding; random cross-bin phases destroy the ordered time-domain hypothesis.
2. Circular Q delay. I is byte-exact and Q is globally circularly shifted by a
   deterministic nonzero delay, preserving the exact I and Q marginals while
   breaking their simultaneous trajectory.
3. Block phase-increment permutation. Each block retains the amplitude-envelope
   shape and exact multiset of wrapped adjacent phase increments before a
   deterministic, recorded anti-clipping gain and CI16 rounding, while destroying
   increment order.

Every seed is `SHA256(domain || observation_id || transform)[:8]`. Decoder
outputs, results, ground truth, and input content hashes are prohibited seed
inputs. The runner exposes no single-transform campaign mode: it schedules all
three transforms over all 27 inherited cohort rows and never parses decoder
outcomes. It may stop only on an operational integrity failure; resumption must
complete the same 81-pair schedule.

## Post-81 validity, claims, and exposure

All three transforms and all 81 pairs run regardless of intermediate results.
Only after the fixed schedule is complete, the independent auditor applies the
predeclared destruction diagnostics. FFT random phase must satisfy per-pair
source/output correlation plus exact local-spectrum and Parseval tolerances.
Phase-increment permutation must satisfy per-pair correlation plus aggregate
fixed-index and displacement tolerances. A failure excludes that entire
transform, across both receivers, from qualified FAR; individual pairs are never
selected or excluded.

Circular Q-delay is always an IQ-coupling diagnostic and is never FAR-eligible:
its I channel is byte-exact, so an I-only decoder can legitimately retain
telemetry. Its Q decorrelation and minimum one-eighth-capture circular distance
are still checked after all 81 pairs. Every strict positive from every transform
is reported as `LEAKAGE_OR_FALSE_ACCEPT`; attempted exposure and events remain
visible even when a transform is not FAR-qualified.

The preregistration requires separate attempted descriptive rates for every
transform and receiver and receiver-combined reporting. Qualified FAR is shown
separately for every eligible whole transform that passes its diagnostics, plus
a combination over only those qualified transforms. The frozen R5-v4 cohort metadata implies
4.2723952517361115 decoder-hours per transform per receiver,
12.817185755208335 hours across three transforms per receiver, and
25.63437151041667 receiver-system hours in the grand combination.

These are decoder-exposure system-hours. They are not natural-negative signal
hours or unique-signal hours. R5-v5 is sensitivity evidence only and cannot by
itself establish positive telemetry yield.

## Review and execution boundary

`r5_v5_review.py` requires an out-of-band SHA-256 for an approval that binds the
protocol and the complete implementation file set, including the dynamically
loaded R5-v3 builder/runner, the legacy auditor, and their transitive scripts.
The reviewed V4 runtime manifest is also byte-bound through the frozen plan and
the V5 runtime construction record, so a later replacement fails audit.
The pending template cannot unlock any action. The runtime builder, plan freezer,
generator/runner, and auditor all check the same approval. The runtime adds only
the null generator to the predecessor code and changes the isolated
gr-satellites HOME configuration to `submit_tlm=no`; core decoder sources remain
unchanged.

Before freeze, before a run, and during audit, the live runtime must have exact
path-set equality with its manifest, including directory and file modes, symlink
targets, external symlink targets, every discovered ELF dependency, and hashes.
Any added path or dependency fails closed. Generator/receiver process groups have
fixed time, stdout, stderr, and artifact limits. The independent parsers have
fixed artifact, record, frame, and read-chunk bounds. A limit failure kills the
whole process group, removes only size- and bounded-prefix-hash-recorded partial artifacts, emits one of a
bounded number of immutable operational receipts, and resumes at the same frozen
pair; it is not interpreted as a scientific result.

After an independent reviewer fills and signs a copy of the template, the
authorized order is:

1. Use `r5_v5_review.py --prepare-output reports/<name>.json` to create a
   byte-complete but non-approving review packet. An independent reviewer must
   make the decision and communicate the final file SHA-256 separately.
2. Run `build_legal_transfer_r5_v5_runtime.py` with the approval path and its
   separately communicated SHA-256.
3. Review the immutable runtime manifest, then run
   `freeze_legal_transfer_r5_v5_plan.py` with the same approval binding and the
   separately communicated expected runtime-manifest SHA-256.
4. Review the frozen plan hash. Only then may `run_legal_transfer_r5_v5.py` be
   invoked; `--dry-run` validates and prints the full schedule without opening IQ.
5. Run `audit_legal_transfer_r5_v5.py` after all 81 pairs exist. It independently
   regenerates transform hashes and invariants, performs the fixed post-81
   validity classification, and emits attempted exposure/events separately from
   qualified FAR.

No command in this sequence has been run as part of preparing this draft.
