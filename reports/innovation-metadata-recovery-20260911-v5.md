# Bounded CANVAS metadata recovery and corrected local selection

Status at 2026-09-11 02:41:48 UTC: **metadata recovery and checked local selection complete; no waveform download or DSP; not an independent held-out test release**.

## Outcome

The stopped acquisition had reached 200 pages / 5,000 rows with a live next cursor, not EOF. Its last page still covered 2026-08-12, short of the frozen primary lower bound of 2026-08-11. An explicit pre-waveform amendment increased only the pagination bounds from 200 to 300 pages per interval (600 to 900 total), preserving dates, target, rank prefixes, prior-exposure exclusions and outcome blindness. The previously specified `ground_station` correction remains separate from this administrative bound change.

The recovery reused all 59 successful original pages and 141 pages from the earlier resumed run. Only 16 missing pages were fetched. The complete primary interval contains **216 pages and 5,389 rows**, ending in a genuine final response-header EOF. Earlier date extensions were unnecessary because 4,647 records were eligible under the preserved exclusions. “Eligible under preserved exclusions” is not a claim of current nonexposure.

The final checked, metadata-only replay selected **500 observations, 215 stations and 500 station/day strata**. The exact selected observations and ID-order digest match the earlier checked replay; subsequent validation hardening changed no selected row. No original snapshot or earlier result was overwritten. No decoder was launched, no OGG/IQ waveform was downloaded by this subtask, and no new telemetry is claimed.

## Independent review and corrections

The independent artifact reviewer checked all 216 HTTP receipts and final headers, 648 artifact hashes and byte sizes, exact 59+141+16 ancestry, cursor continuity, final HTTP 200 status, matching header filenames and final EOF. All requested, result, final and hop URLs retained the exact frozen cursor and filters. Joined raw-page rows equal the aggregate metadata; there were no duplicate/conflicting observation IDs. All 30 recorded historical cooldowns had expired.

The review found a real fail-closed gap in the first recovery executable: it checked requested URLs but did not require identical final/hop URLs after a transport redirect. It also identified replay-validator hardening opportunities for exact header/hop correspondence, strict exposure-ID parsing, per-tier window binding, post-parse artifact checks, and propagation of the holdout caveat. These were fixed in the checked replay and corrected v6 recovery implementation. **The actual 216-page audit found no data affected by the redirect gap.** Executed v5 sources and binary remain preserved; it is not retroactively claimed that v5 enforced the later checks.

The final validator rejects changed filters/cursors, redirects, missing hops/final URLs, failed final HTTP status, mismatched final-header status or filename, invalid hop/attempt bounds, symlink evidence, malformed exposure IDs, changed hashes/byte counts, inconsistent aggregate rows, page discontinuities, and a capped prefix lacking EOF. It rechecks artifacts after parsing and repeats complete validation before committing a corrected cohort. Tests include valid alternate-header misattribution and out-of-range hop cases. **16 integrated tests passed**, independently repeated by another agent. The corrected v6 recovery also re-audited the real 200-page preserved prefix without network access. Review and test execution were AI-agent-assisted, not a claim of completed human review.

Historical metadata headers had not originally been hash-bound. The recovery hashes them at the new freeze and preserves this limitation; retrospective hashing is not proof that a header has never changed since acquisition. Hash consistency and a successful local replay are not cryptographic execution attestation.

## Scientific hold: do not start a confirmatory benchmark yet

Both the final cohort and its plan explicitly set `fresh_independent_holdout_qualified=false` and require `current_exposure_and_cross_station_pass_overlap_audit_required_before_dsp=true`.

The preserved exclusion snapshot predates later CANVAS private-IQ/OGG development, including observation #5122 around 2026-09-09 22:50 UTC. Different public observation IDs can represent overlapping reception of the same satellite pass. The next step is a **new audit of actual waveform/decoder exposure and cross-station pass overlap**, retaining the original selection as an immutable record and documenting any ensuing exclusion/amendment. Merely having paginated or inspected metadata is not itself decoder tuning exposure; a new audit must distinguish those activities. Neither 500 selected IDs nor 215 stations establish independent generalization, and the resulting cohort remains single-mission.

## Reproduction and handoff

The metadata fetch has already finished. **Do not rerun acquisition merely to reproduce the selection.** To validate and reproduce selection into a new, nonexistent output directory, use the final checked selector with `--metadata-only` and the preserved full metadata root:

```bash
rtk proxy /home/ubuntu/telemetry-yield/work/innovation-benchmark-20260910-v2/bin/innovation_benchmark_acquire_v4_checked_final \
  --output /home/ubuntu/telemetry-yield/work/innovation-benchmark-20260910-v2/another-new-local-replay \
  --replay-frozen-metadata /home/ubuntu/telemetry-yield/work/innovation-benchmark-20260910-v2/acquisition-resumed-v5 \
  --metadata-only
```

The checked selector refuses waveform mode. The historical metadata service `telemetry-innovation-metadata-20260911-v5` completed with exit 0 and has no continuing download/DSP chain. Its invocation ID was `c237b69e8125414a942c02016e93502f`. Source-level builds use the cached Rust dependency set, edition 2024, `-O`, and rustc 1.98.1; dependency source/full system packaging still requires normal release preparation.

| Artifact | SHA-256 |
|---|---|
| [Complete immutable metadata](/home/ubuntu/telemetry-yield/work/innovation-benchmark-20260910-v2/acquisition-resumed-v5/metadata-all.json) | `f3430f385b11b15c2d2931e2e2ffb44687f39b5ceb3ef3916e356c3b322c079c` |
| [Independent 216-page audit](/home/ubuntu/telemetry-yield/work/canvas-reference-audit-20260911/metadata-216-independent-audit.json) | `c94b2dfaae6f76fc5d919dcbdce551eb8feb463cbf3a4e049fc4cde8d0ff44fa` |
| [Final checked 500-observation metadata cohort](/home/ubuntu/telemetry-yield/work/innovation-benchmark-20260910-v2/acquisition-corrected-v4-final/cohort.json) | `dc473c071d98974ddaba15d518a3048dcff87f46fe73e0088aeefd874e0abb1a` |
| Selection ID-order digest | `50fb971a6061e819e2ac592a7c4007f42cd2edd4b9706a1ead18026b1ece603b` |
| [Executed v5 source archive](/home/ubuntu/telemetry-yield/work/innovation-benchmark-20260910-v2/metadata-recovery-v5-executed-sources.tar.gz) | `d4c6f752b826d1068631b84f2e21a3aa18869036c4cfd4ef91c21c76612e15df` |
| [Checked final source archive](/home/ubuntu/telemetry-yield/work/innovation-benchmark-20260910-v2/metadata-recovery-checked-final-sources.tar.gz) | `48ac98b4048f3b1a6548bb8e62d1507767fe57883260b35e906793b7c88acc59` |

The [final machine handoff receipt](/home/ubuntu/telemetry-yield/work/innovation-benchmark-20260910-v2/metadata-recovery-final-receipt-20260911.json) records source/binary identities, test counts, the original and amended bounds, preservation evidence and the remaining scientific hold. Source files are [the checked selector](/home/ubuntu/telemetry-yield/examples/innovation_benchmark_acquire_v4.rs) and [the corrected recovery implementation](/home/ubuntu/telemetry-yield/examples/innovation_metadata_recover_v6.rs). Graphify's available graph did not cover these new Rust tools; direct source and artifact auditing governed this work.
