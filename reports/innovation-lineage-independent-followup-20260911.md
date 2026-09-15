# Independent follow-up of historical exposure lineage

The proposed 266-record historical cohort is **not released**. This bounded local follow-up explains 475 of the 876 previously unlinked artifacts and resolves all 55 oversized-JSON context issues. Another 401 artifacts remain explicitly unclassified. Artifact counts are not counts of independent recordings or contaminated candidates.

No observation API calls, downloads, demodulation, or new-candidate signal inspection occurred. Existing fixture bytes were read only for checksum or exact test-marker verification: 82,570,240 bytes in the content pass. The cohort, prior selection, exposure auditor and receiver runner were not modified.

## Checks completed

The first Rust helper reads bounded metadata up to 64 MiB and joins **explicit source paths**, then exact declared content identities, against the pinned prior exposure inventory. It does not derive new observation IDs from directory names. All 55 formerly rejected reports are readable. Forty-nine join directly by source path; six old innovation-v1 reports refer to deleted temporary PCM paths whose declared SHA256 identities exactly match already-accounted source identities in separately hashed metadata. These six are metadata-provenance joins, not rehashes of deleted files.

Metadata checks resolve 285 of the 876 artifacts:

| Exact classification basis | Artifacts |
|---|---:|
| Seed-bearing exact input identities plus typed synthetic generation plans | 112 |
| Exact control rows in hash-bound synthetic manifests and execution plans | 64 |
| Explicit source paths already included in prior exposure inventory | 24 |
| Typed paired Brier-score bootstrap statistics, not receiver attempts | 56 |
| Weather-model fit records bound to their panel hashes | 22 |
| Explicitly synthetic experiment summaries | 6 |
| Receiver build-freeze record, not decoding | 1 |

The separate content helper resolves another 190 artifacts:

| Exact classification basis | Artifacts |
|---|---:|
| Current file equals stored Git blob; its latest content-change commit predates 2026-06-12 | 91 |
| Current SciPy test WAV equals exact installed wheel `RECORD` digest and byte size | 66 |
| Exact 64-byte fake-context fixture or documented input-drift mutation | 30 |
| Test result marker/normalization bound to the 64-zero-byte source by process receipt | 3 |

The Git reference corpus has HEAD `952ddfe53f62a150c53559249c83370630254cab` and a clean working tree. `koyo.wav` remains unclassified by the historical-date rule: its 2026-07-05 addition falls within the historical period. The other 91 files were checked against their actual Git blobs and each file's own last-change commit. This does not rely on inferring satellite identity from filenames. Stored commit timestamps are local provenance, not externally authenticated timestamps.

The fixture checks do not claim that every file near a package or test directory is synthetic. Unmatched contents remain unresolved. Two known input-drift patterns and mock output structures come from the preserved `tests/test_scientific_campaign_runner_v1.py`, whose identity is recorded in each applicable receipt.

## Exact remaining groups

All exact remaining paths are the rows with `resolution:null` in `lineage-content-review-v2.json`. The following inventory is a work queue, **not a claim that these artifacts actually contain unaccounted CANVAS signals**.

| Group under `work/` | Remaining |
|---|---:|
| blind-phase-confirmatory-v2 | 84 |
| decoder-readiness-20260911 | 68 |
| innovation-controls-20260910-audio-v1 | 33 |
| progressive-evaluation-smoke-20260910-v1 | 29 |
| rust-migration-20260908 | 20 |
| single-run-weather-v1 | 19 |
| tag15-front-end | 16 |
| satnogs-ogg-refinement-20260907 | 14 |
| golden | 12 |
| canvas-reference-audit-20260911 | 11 |
| prospective-v4h | 10 |
| adaptive-sequence-20260910-v1 | 8 |
| lossless-speed-20260910-v1 | 8 |
| progressive-synthetic-20260910-v2 | 8 |
| polyitan | 7 |
| mm-sweep | 6 |
| intelsat37e-gr4-positive-v1 | 5 |
| iq-dump-forensics | 5 |
| rml24-audit | 5 |
| intelsat37e-gr4-positive-v3 | 4 |
| soft-sync | 4 |
| innovation-field-20260910-v1 | 3 |
| innovation-benchmark-smoke-20260910-v1 | 2 |
| mm-review-20260911-v1 | 2 |
| parity-audit | 2 |
| production-qualification | 2 |
| progressive-evaluation-real-20260910-v1 | 2 |
| ab-satnogs-offline; antenna-policy-replan-v1; cli-smoke; cli-smoke-v2; deployment-qualification-blind-v1; innovation-pool-development-20260910-v2; positive-real-iq; public-iq; root-parity; satnogs-holdout-20260908-v1; satnogs-ogg-pilot-20260907; taurus-validation-20260911-v1 | 1 each |

The most relevant remaining linkage work is concrete:

- Bind the CANVAS derivative artifacts in `canvas-reference-audit-20260911` and `decoder-readiness-20260911` to the already-known private #5122 IQ source through their conversion/control receipts. These directories mix real derivatives, synthetic controls and copied source fixtures; no single directory-wide label is valid.
- Connect the 33 innovation audio-control results to their **actual** source records in the separately generated control set; adjacent result files alone do not establish this link.
- Bind unlabelled native/legacy preprocessing dumps (`tag15-front-end`, `mm-sweep`, `golden/matrix-dumps`, `polyitan/postfreeze-gr-satellites-dump-4704`) to exact generating commands and upstream capture identities. Names suggest possible sources but are not used as evidence here.
- Review the remaining test/startup/normalization records under `blind-phase-confirmatory-v2`; several are intentional tamper or startup-barrier tests, distinct from the byte-exact fake-context cases already resolved.
- Distinguish the remaining planning/weather summaries and copied Rust fixtures through typed metadata and source manifests. A filename containing `decode` or `result` was enough for the broad original inventory, not enough to establish an RF decoding attempt.

## Filesystem coverage

All 7,828 saved symlink exceptions were resolved as far as their current link/target metadata allowed: 7,733 target regular files, 84 target directories, and 11 dangling links. Exact link and canonical target paths are retained in `lineage-group-review-v2.json`; none were silently waived as package data. The dangling links name specific absent library/linker targets in the stored conda package cache and are not evidence of current hidden recordings. Directory aliases still need deduplicated traversal against the inventory scope.

The one permission-denied directory, `work/golden/.env.guard-v3-x1ztap28`, was inspected read-only with `sudo -n`. It contains `backup`, `quarantined-original-hardlinks`, and `superseded-original` environment trees. An explicit no-ignore recursive search using the existing auditor's eight waveform suffixes returned no matching files. This resolves that narrow suffix-search failure, not proof that arbitrary or extensionless files cannot contain signals; no permissions or contents were changed. The original exception remains preserved rather than rewriting the old inventory.

## Durable artifacts and limitations

Metadata receipt: `work/innovation-exposure-audit-20260911-v4/lineage-group-review-v2.json`, SHA256 `85fbf675c15d147afad0d243f6cdf1eb0d90e7c70e6438fee5c605c427cf539b`.

Content receipt: `work/innovation-exposure-audit-20260911-v4/lineage-content-review-v2.json`, SHA256 `4ca5c0af7e1a7d7dd7403f0023af254199c8eca8dc9658672c7041c6222b719a`.

Helpers: `examples/exposure_lineage_group_review.rs` (three tests pass) and `examples/exposure_content_lineage.rs` (two tests pass). Frozen executables are `work/canvas-reference-audit-20260911/exposure_lineage_group_review_v2` and `exposure_content_lineage_v2`; SHA256 respectively `9fe20872701584fcb7ce422a7da4905b2bc86e581d210588855ab9386b74e745` and `83c3df6441ff4bcebc2ab7c2f09970396c80fa95830b35d097b758097fd14e28`. Earlier receipts and helpers were preserved.

The unresolved items and remaining deduplicated directory coverage prevent a complete local review. This is not a demand to prove global or deleted-history absence. The explicit-source joins found only already-accounted exposure keys; no new cohort selection or favorable-result resampling was performed. A refreshed current exposure review is still needed after closure, because the workspace is active and these checks are not an atomic snapshot.

Graphify supplied the required initial project lookup but its graph did not contain these latest lineage artifacts. This report is grounded in the directly checked metadata, fixture identities, stored source recipes, and exact-path inventories.
