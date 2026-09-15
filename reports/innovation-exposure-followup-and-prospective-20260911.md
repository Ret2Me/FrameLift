# Exposure follow-up and prospective alternative

The historical 266-record proposal is **still not released**. The follow-up resolves the observed numeric-identifier exceptions but does not certify all remaining historical recording lineage. No new audio acquisition, waveform-content reading or DSP occurred.

## Historical audit outcome

`work/innovation-exposure-audit-20260911-v4/` is the latest inventory, produced by auditor v5. It adds `.raw` captures, recognizes stored `satnogs-observation-*.json` metadata, stops treating the 437250000-Hz frequency token as a public observation ID, and resolves hash-only result directories using explicitly scoped `unit.observation_id` or top-level `observation_id`. A generic nested ID or a metadata-only selection manifest is not allowed to create exposure. Each directory-context artifact is hash-bound.

This reduced unlinked artifacts from 25,945 to 1,004 without changing the proposed subset: **266 retained, 234 excluded, optional 67 internally separated**, still in original rank order with the unchanged original plan. The newly recognized `.raw` captures and synthetic control result indexes introduced further identifier exceptions; this is expanded coverage, not a new decoding result.

The separate hash-bound review `lineage-review-partial-v2.json` resolves **all 78** such exceptions:

- The saved CAMRAS index identifies older RSP-03, MTCube and URESAT recordings outside the CANVAS time/mission scope. For example, #12511021 and #12512778 are from 2025-10-06; #13168691 is from 2026-01-08. NORAD values were not invented from these name-only index entries.
- Result indexes 2–63 match exact source identities and indices in declared synthetic control manifests, not authenticated private SatNOGS observation identities. This checks the stored recipe/result relationship; synthetic waveform bytes were not rehashed.
- Two IDs are Zenodo record numbers: preserved SigMF descriptions identify Intelsat 37e and GPS material, not CANVAS observations.
- One numeric suffix belongs to an unrelated weather-metadata supervisor status file; its actual structured operational contract was inspected.

Of the remaining unlinked artifacts, 128 have explicit synthetic generation recipes. **876 are still retained as unresolved in the partial review**, with their exact paths, recorded sizes and timestamps. The review also retains all 55 bounded context-read exceptions and the filesystem coverage exceptions. These are broad artifact counts, not counts of independent recordings. Some are plausibly tests or non-decoding work, but no blanket root-name waiver was used.

Machine review:

```text
work/innovation-exposure-audit-20260911-v4/lineage-review-partial-v2.json
schema: innovation-lineage-review-v1
status: partial
complete_review: false
resolved_exception_count: 78
unresolved_key_count: 0
unresolved_unlinked_artifact_count: 876
evaluation_release_permitted: false
```

Exact remaining paths/contracts are in `unresolved_unlinked_artifacts`, `lineage_context_read_issues` and `filesystem_coverage_issues`. This separates a reproducible bounded local inventory from the impossible claim that every recording anywhere has never been seen. It does not require proof of global absence, but the currently unexplained local items still prevent a justified complete **local** review. The historical500/266 remain exploratory, not confirmatory.

Seven exposure-auditor tests and two lineage-review parser tests pass. Frozen source copies are archived in `executed-audit-sources-v1.tar.gz`; older exploratory inventories and binaries were preserved.

## Prospective alternative actually registered

At **2026-09-11 09:17:55 UTC**, `innovation_prospective_register_v1` rehashed the repaired native receiver (`e59e8dac…`) and repaired innovation receiver (`638066b9…`) against the runtime-repair build manifest and wrote an immutable local selection registration under `work/innovation-prospective-20260912-v1/`. This is a local timestamped registration, not an external journal/registry submission or a completed scientific trial.

The frozen future window is **2026-09-12 00:00 through 2026-09-19 00:00 UTC**, end exclusive. Eligibility uses published CANVAS NORAD 68635 and transmitter UUID `GCmN6RULea8dAT7Qoat8z2`, GMSK 9600, valid station/time metadata and an HTTPS audio URL. It never filters on decoding status, recovered frames or visible signal. Target maximum is 500 from this fixed week, station/day SHA256-ranked round-robin; fewer eligible recordings produce an explicit shortfall, not a silently extended period or outcome-dependent replacements. This is a pragmatic sample/window, not a power calculation.

Known prior same-NORAD exposure within a 120-minute interval gap excludes candidates across stations and transmitters. Intervening local waveform/decoder access must be logged and reviewed. Historical unknown files already present before a genuinely future observation are not themselves evidence that the future waveform has been used, but concurrent research on new observations can still contaminate the future sample. The release therefore still requires actual current exposure review, not just a future date.

The five-arm runner defaults were registered as four workers, two threads and 900 seconds per decoder. **Complete runner/comparator/profile/legacy-arm and analysis-protocol freezing remains required before 2026-09-11 22:00 UTC** (two hours before the window). The main task owns these artifacts. If that deadline is missed, this registration cannot silently move its window or waive the condition.

Five registration tests pass: temporal guard, interval boundaries, invalid time, exact transmitter metadata and invariance to outcome fields. The helper only registers the plan and tests a pure metadata predicate; it does not fetch observations, acquire audio or execute receivers. The final cohort does not yet exist.

## Explicit pre-window statistical amendment

The frozen v1 wording proposed connected components using a 120-minute gap for evaluation dependence. Such components can transitively join successive passages into one large component; the rule must not be called independent-pass estimation. The original registration was **not edited**.

`pass-dependence-amendment-v1.json`, declared at 09:23:42 UTC, retains the 120-minute prior-exposure exclusion unchanged and records this limitation explicitly. Conservative 120-minute evaluation components remain primary; 0-gap overlap and 30-minute grouping are predeclared sensitivities, not alternatives selected after seeing a favorable confidence interval. Fewer than ten groups means descriptive paired counts/effects only, without inferential intervals or significance claims. Seven UTC-day blocks therefore provide descriptive sensitivity, not high-confidence inference. The main task's separate analysis registration owns the primary endpoint and exact inference implementation. The amendment also specifies the station/day key encoding as `decimal_station:YYYY-MM-DD`.

All registration and amendment files retain `evaluation_release_permitted:false` and `fresh_independent_holdout_qualified:false`. Neither historical candidates nor this future registration is currently an acquisition admission receipt.

## Agreed fail-closed admission contract

The runner agent implements `innovation-waveform-release-v1`, requiring complete, hash-bound `innovation-lineage-review-v1`, `innovation-current-exposure-check-v1` and `innovation-receiver-freeze-v1` records, exact cohort/source/rank/digest bindings, no blocking findings, and both release/cohort success flags. A fresh exposure check is required at invocation and can be refreshed on resume without mutating the frozen release. The gate's metadata-only preflight is intended for the future downloader before its first audio fetch. The current partial lineage review must be rejected. Prospective admission must additionally verify the registered window and pre-window full-freeze deadline.

Key SHA256 identities:

| Artifact | SHA256 |
|---|---|
| Latest exposure manifest | `1d835ff644836ac9812c2a2f01887118f7fd485e6e4272693f916be6e4bca7b0` |
| Latest proposed266 cohort | `b084a2b91e61729f23370b0cc6a9e5061a3180c9c58b72c0b0496ee9d6cf4613` |
| Partial lineage review v2 | `8e54109ae6f3965ed438460f9f68fc350932c913b486633ca60326b7a2b5d531` |
| Prospective method freeze | `000d0f337396c7a186c939c5b98a7f685fefb92d4b8acb9515e2a17aad03b433` |
| Prospective selection protocol | `c5ed56104a2c2b558449d0ed907470dfb89e913d6913b72c560d8f63bb556e82` |
| Prospective registration receipt | `dc6b6d009bb70713ac1f283cb458be613c87d5b157feda0d2688d502926080b8` |
| Pass-dependence amendment | `9aafa3026b27063871de105c2f0e87cdf617be572325128edec1b1241d47c8b3` |

None of this establishes improved telemetry yield, deployment readiness, adequate statistical power or publication readiness. It prevents an exposure-contaminated or only partly checked sample from being mislabeled as an independent final benchmark.
