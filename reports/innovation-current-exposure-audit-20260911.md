# Current recording exposure and cross-station pass-proximity audit

Status: **bounded audit completed; proposed subsets produced; release NOT qualified**. This is metadata/filesystem work, not a decoding benchmark or publication result. No new waveform downloads or DSP were performed.

The machine snapshot ran on 2026-09-11 from 08:44:08 to 08:44:17 UTC. The original metadata500 and its ranking remain unchanged. New files are under `work/innovation-exposure-audit-20260911-v3/`; the earlier v1 directory contains a failed filesystem-read attempt and v2 an explicitly preliminary inventory. Neither is an evaluation release.

## Result

| Item | Count |
|---|---:|
| Original selected public CANVAS observations | 500 |
| Excluded for identified same-satellite temporal proximity | 234 |
| Proposed remaining observations, preserving original rank | 266 |
| Optional stricter subset, also internally separated in time | 67 |
| Exclusions with at least one private-instance exposure match | 77 |
| Exclusions with at least one historical public exposure match | 228 |

The last two rows overlap. All 234 exclusions are temporal-proximity exclusions rather than identical public observation IDs. Thus an old ID-only exposure exclusion is insufficient even when every proposed public ID is new.

Six original candidates have proximity matches to the recently investigated private CANVAS #5122: 14967350, 14967306, 14967338, 14966912, 14967320 and 14967271. This includes a deliberately broad two-hour margin; it does not assert all six are literally the same orbit or waveform.

Sensitivity counts, without choosing a threshold based on decoding yield: retained at 0/60/90/120/180 minute interval-gap thresholds are **352/277/267/266/264**. The declared primary threshold is 120 minutes. The optional 67-record subset greedily retains the earliest original-ranked candidate and excludes later-ranked candidates within the same threshold of an already retained same-NORAD interval; it is not a new optimized selection or replacement sample.

## What was and was not measured

The Rust auditor (`examples/innovation_exposure_audit.rs`) recursively inventories `work/`, records waveform-file or decoder-attempt-artifact presence, and joins observation identities to NORAD, start/end and station metadata. Waveform presence counts conservatively even if no decoder use is proven; recognized decoder artifacts count even if a waveform has since been removed. Metadata-only cohort/API inspection does **not** count as waveform exposure. Container metadata may contain outcome or demoddata fields, but only identity/NORAD/time/station are extracted and used; selection does not inspect recovered frames or rank by outcome.

The current snapshot contains 1,382 hash-bound metadata inputs, 6,728 instance-scoped catalog keys, 1,342 path-derived exposure keys and 1,851 resolved metadata variants. Variants are retained conservatively rather than silently selecting one conflicting timing/station value. The original user-supplied `satnogs-observations-no-sx.json` supplies older private observation times and satellite UUIDs; UUID-to-NORAD joins come from the current metadata catalog. No ambiguous UUID-to-NORAD mappings occurred. This resolved the previously missing older private IQ identities.

The cross-station exclusion ignores station and transmitter when NORAD is the same and intervals overlap or their gap is at most 120 minutes. This is a conservative temporal proxy for shared/nearby satellite passages, **not TLE propagation, orbit identification, or a proof of statistical independence**. The threshold was fixed before this inventory and before any new waveform acquisition or decoder outcome.

Waveform contents were not read or rehashed: exposure evidence records path, size and modification time. This is an exposure audit, not source-signal integrity qualification. The snapshot is live, not atomic; a new exposure log check is required at release if research continues concurrently.

## Why the release gate remains closed

Eight path-derived keys remain unresolved in the normalized observation catalog. Several are recognizable non-observation identifiers, but they have deliberately not been silently treated as validated exclusions or safe non-exposures:

- `public_satnogs:12511021`, `12512778`, `13168691`: older public IQ/development material requiring source date/NORAD reconciliation.
- `13371136`, `6394603`, `6511229`: paths associated with Zenodo/GPS/ESTAR material, illustrating that numeric path tokens are not universally public observation IDs.
- `437250000`: a frequency token in a public IQ filename.
- `13422078`: a token in an unrelated weather-run identifier.

There are also 25,945 unlinked candidate artifacts, heavily dominated by historical blind-phase campaign outputs. The count is a broad filename-based inventory, **not 25,945 independent recordings**. For example, inspected historical normalized-result provenance embeds a private observation identity under `unit.observation_id`, while its directory is a hash. Such lineage can be resolved by extending the parser, but the current tool does not claim this is finished. Other examples include synthetic controls, environment fixtures and old soft-sync derived streams. Root names alone are not sufficient evidence to label all of them synthetic or irrelevant.

One root-owned historical environment directory could not be read, and 7,828 symlinks were not followed. Many clearly concern runtimes/test aliases, but the report records all such coverage exceptions rather than converting them to absence of exposure. The walker completing its accessible inventory is **not complete research-history coverage**. It does not cover every deleted artifact, source recorded elsewhere, extension not recognized by the heuristic, or waveform copied under an unrecognized name. Public/private namespaces currently use an explicit numeric-ID heuristic, not authenticated service identities.

Accordingly, both proposed cohorts contain:

```json
{
  "schema": "innovation-benchmark-cohort-v2",
  "candidate_for_independent_evaluation": true,
  "identified_local_exposures_disjoint_at_declared_padding": true,
  "current_exposure_check_passed": false,
  "evaluation_release_permitted": false,
  "fresh_independent_holdout_qualified": false,
  "publication_ready": false
}
```

Here `identified_local_exposures_disjoint_at_declared_padding` is limited to the identified/normalized records in this manifest. It must not be substituted for the closed release gate.

## Downloader / runner contract

A future versioned waveform downloader must, **before its first audio fetch**, require an explicit hash-bound release receipt and verify all of the following:

1. Derived cohort schema, byte/hash identity, original source cohort identity, unchanged original `plan`, exact original-ranked subset rows, count and newline-separated ID digest (no trailing newline).
2. The referenced exposure manifest and separate amendment/review hashes, their declared scope and time, and zero unresolved **in-scope blocking** lineage findings in a completed review. A boolean copied into a cohort by itself is not sufficient evidence.
3. Both `current_exposure_check_passed == true` and `evaluation_release_permitted == true` in the reviewed release; **`candidate_for_independent_evaluation` alone is never permission to fetch**. Keep `fresh_independent_holdout_qualified` false unless a stronger, explicitly justified scientific qualification has actually occurred.
4. A contemporaneous post-snapshot exposure/pass-overlap check and a frozen receiver/executable/configuration protocol. New development on any included or nearby passage invalidates the release until the subset is re-audited, without replacing exclusions based on yield.
5. Missing artifacts, digest drift, malformed flags, unknown schema, unresolved review status and partial receipts fail closed. The current two proposed cohorts must therefore be rejected.

A release schema can be named `innovation-waveform-release-v1`, binding `cohort`, `exposure_manifest`, `lineage_review`, `receiver_freeze`, `current_exposure_check_passed`, `evaluation_release_permitted` and a timestamp. This is a proposed contract, **not a claim that an implemented or passing release receipt currently exists**. The parent task owns the downloader/runner implementation.

The original500 should not be downloaded under its older exposure snapshot. Two defensible next paths are (a) finish typed lineage resolution for the historical retained subset and refresh the snapshot, or (b) freeze the algorithm now and prospectively acquire observations starting after the freeze plus the declared passage margin, logging all intervening waveform access. The latter avoids asserting that incomplete historic paths prove non-exposure. It still requires same-mission dependence, repeated telemetry, station effects and matched-input comparator limitations to be addressed statistically.

## Verification and reproducibility

Six Rust tests pass: instance/date/hash-token ID parsing; interval boundaries and NORAD separation; metadata-only exclusion from decoder exposure; outcome-invariant metadata extraction retaining timing variants; invalid timestamps; and ambiguous UUID mappings retained conservatively. Direct checks on the real outputs confirm the original `plan` is exactly unchanged, each observation row exactly equals its indexed original row, rank order is preserved, and the schema remains compatible with `innovation-benchmark-cohort-v2`. These are engineering checks, not independent scientific validation.

Run command used:

```sh
rtk proxy work/innovation-benchmark-20260910-v2/bin/innovation_exposure_audit_v4 \
  --work work \
  --cohort work/innovation-benchmark-20260910-v2/acquisition-corrected-v4-final/cohort.json \
  --output work/innovation-exposure-audit-20260911-v3 \
  --supplemental-metadata /home/ubuntu/.codex/attachments/bdbabb0d-edd5-4f07-a813-36b0f392003d/satnogs-observations-no-sx.json
```

Use a new output directory for a new run; existing output directories are not overwritten. The threshold defaults to 120 minutes. The executable uses cached Rust dependencies; no Python implementation or waveform processing was introduced.

Frozen SHA256 identities:

| Artifact | SHA256 |
|---|---|
| Original500 cohort | `dc473c071d98974ddaba15d518a3048dcff87f46fe73e0088aeefd874e0abb1a` |
| Rust auditor source | `1b8c18b3abea5e6808d4aa9276b6bba71ea015ba8728a431a9b0706953ce6f56` |
| Auditor v4 executable | `1956143de1dada6f9e04ee4b2bf517c80b59777a700314c8bc2671542e381fae` |
| Six-test executable | `91a14b215da04560ab5115ba3e552df357e424205a631cc627ca63bf162eaf32` |
| v3 exposure manifest | `7739ee30c5bd8256b16e60b16f8e38e3e9a3a6d77e6b3d4cd7bbcca1988658a0` |
| v3 summary | `8262cc140c45f0c6bdc20b77e71aac5aa99cd7a9cdf6bd4ad1cfc0be0bdbaa00` |
| v3 proposed266 cohort | `b5a6fe8f8f63b19e3e8a15fd8f7c6ba998ce29c4b307727b98a053647ab0904a` |
| v3 internally separated67 cohort | `de6fbbfc85fcb4ab484c0559f6d731449d3de639730f3463883537f7daaaaab9` |
| v3 exclusions | `6448688bd85a03523e03dddcd3863a642c36c300027332cfc06e8f8641adce02` |

This report reports an important contamination check and a safely blocked evaluation release. It does **not** demonstrate an improved decoder, independent test success, publication readiness or final sample size.
