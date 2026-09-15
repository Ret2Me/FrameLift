# Independent bounded benchmark-gate review — 2026-09-11

Recorded 2026-09-11T09:50:25Z. Reviewer: `/root/journal_readiness`.

## Decision and scope

No remaining actionable **admission-gate blocker** was found in the reviewed snapshots after the owners applied the findings below. Final no-DSP tests independently passed. This is a bounded source/receipt review, **not** a released evaluation cohort, a complete registered analysis report, a global independence certificate or publication readiness.

The follow-up registration check found reporting gaps and one UTC-day edge case; they are recorded below and were sent to the main task before final reporting freeze. No receiver source, frozen method, protocol or statistical method was edited by the reviewer. Only this report and its machine-readable companion are new reviewer artifacts.

## Actually executed tests

| Independent command target | Result |
| --- | --- |
| `innovation_benchmark_repaired_tests_v3 --test-threads 2` | 34 passed, 0 failed, 1 intentionally ignored |
| Same binary, `--ignored --exact tests::existing_repaired_development_receipts_score_without_redecode --nocapture` | 1 passed; existing development receipts only, no re-decoding |
| `innovation_waveform_acquire_tests_v5 --test-threads 2` | 19 passed, 0 failed |
| `paired_yield_analyze_tests_v4 --test-threads 2` | 15 passed, 0 failed |

These are actual executions observed around 09:47 UTC, not planned tests. Shared helper tests appear in several binaries and are not 69 distinct tests. Exact commands, durations, byte counts and hashes are in [the machine receipt](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/benchmark-gate-independent-review-v1.json). Console results were observed through tool output; no separately retained raw console log is asserted.

## Findings fixed and rechecked

- **selected_runtime_freeze:** A new invocation could fingerprint changed selected gr-satellites modules without contradicting the old receiver freeze. Stable selected-runtime digest is required in freeze; only the generated profile path is normalized. Actual installed module paths, hashes and byte counts remain bound.
- **synthetic_profile_bypass:** synthetic_fixture=true previously bypassed the real transmitter check. Production bypass removed; test cohorts use the supported CANVAS transmitter.
- **cohort_read_identity_race:** Parsed observations could precede the identity later admitted. Runner carries the checked first-read identity; launcher checked-reads and compares the staged document and identities before admission and before transport.
- **relocated_cohort_identity:** An exact staged copy was rejected because its path differed from the registered canonical cohort. Both canonical and staged exact identities are checked; their bytes/content must match, and current checks remain bound to canonical registration.
- **empty_preflight_manifest:** A hash-matching empty manifest could previously satisfy the wrapper's shallow preflight proof. Wrapper now checks manifest schema, runner/improved/release/resources/runtime and a linked successful admission record, including zero waveform reads and decoder runs.
- **download_receipt_reuse:** Identity-only or relabelled old download receipts could be reused without proving a complete transport/probe result. Shared typed validation checks exact observation/URL, HTTP200 process evidence, Ogg magic, actual identities and expected ffprobe invocation/output.
- **prospective_selection_binding:** Future-window dates alone did not prove the registered outcome-blind selector or true metadata EOF. Prospective admission now checks registration/amendment/source plan, exact original-ranked subset, reviewed EOF/selection replay and linked evidence.
- **external_payload_score_and_scope:** Descriptive analyzer trusted baseline scored sets and overstated whole-cohort absence when using complete cases. It reparses hashed Dire Wolf hex and gr-satellites KISS outputs; absence fields explicitly refer to all-five-complete cases only.

The main task additionally ran a real, already exposed public #14967362 transport control. The independently read/hash-checked result `825a13736d417520a32cfb6e403ec88796b5a1d5e10b9db8a1e6fe2756bbc738` records a successful first validation and reread, 11 rejected mutations, one known-development refetch, zero new-candidate fetches and zero decoder runs. The reviewer did not rerun that network smoke test.

## Registration versus implementation

The core predeclared procedure agrees: progressive versus Dire Wolf ∪ gr-satellites; paired exact gain and loss sets; all-five-complete comparisons; grouping from the entire selected metadata cohort before complete-case filtering; primary gap **7200 s**, with **0/1800 s** sensitivities; **10** contributing-component minimum; **10,000** fixed-seed resamples (`d4514bca7f922026`); no relative interval when any bootstrap denominator is zero; and descriptive-only seven-day sensitivity. Runner's legacy 0-second interval is not the primary conservative interval.

The following remain limitations of the reviewed reporting snapshots, not permission to change an already frozen scientific method:

- **registered_report_is_more_than_analyzer_json:** Analyzer v4 emits gain/loss observation counts, but the registration additionally requests either/neither fractions and explicit seven UTC-day strata. Per-record rows permit derivation, but the frozen v4 output does not itself produce the complete registered report.
- **failed_and_separate_cpu_costs:** Runner summary aggregates each arm's completed wall, combined user+system CPU and peak RSS. Registration additionally requests separate user/system CPU and failed/timed-out costs. Those must be assembled from retained exact attempt/process receipts; analyzer v4 does not provide a substitute.
- **utc_day_normalization:** Analyzer v4 obtains day from start[0..10] although RFC3339 offsets are accepted. Non-Z input can be assigned to the wrong UTC day. Existing SatNOGS Z dates are unaffected; normalize timestamps to UTC or explicitly reject non-UTC dates before day summaries.
- **transport_minimum_duration:** Inherited base/runner audio geometry accepts 0.2–1800 s, whereas typed waveform transport accepts 1–1800 s. Any 0.2–1 s candidate is an explicit acquisition-format exclusion, not evidence of equal geometry coverage.

The analyzer intentionally does not repeat complete native-bank/runtime admission; that remains the runner/control audit's responsibility. Its output alone is not the full report required by the registration. Failure/timeout/missing artifacts remain missing, never zero telemetry.

## Exact reviewed snapshots

Production executable hashes below were independently read; compiler reproducibility or a complete runtime closure is not claimed. Rebuilding a path later does not extend this review to the new bytes.

| Project-relative artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| examples/innovation_benchmark_run.rs | 121716 | `d2c94283ec3f18fdfa48e836855e5b08aa3416d73426eb5b7353d0b31f47d9b1` |
| examples/innovation_waveform_acquire.rs | 16220 | `93507cfc5da874315682a302d671e0cc5e9fa9ffed44c7c768d28553525e0d22` |
| examples/support/innovation_download_receipt.rs | 5299 | `4037b4c37e8346df3cf274229b4c817eba7d2c644fe6f3ec9858c347b4270323` |
| examples/paired_yield_analyze.rs | 20327 | `206232673e25c1273e5a787d7dc2539c3bc0d6a873aa4c1f30309fd7cf236fbe` |
| examples/innovation_benchmark_acquire_v4.rs | 60234 | `de413e66ec94bb37df34969b1c802410b678aba9f035f08451e1d2e127732d2a` |
| examples/support/today20_baselines.rs | 15831 | `cd820920502960dee3d0b8c1edc774c2a72ce2c49423abec435cc356f4e5d939` |
| examples/support/holdout_run_io.rs | 22061 | `ff5df58b8aca1ddd4ce80a5f4b03cbb1415f49c50dcb60a79555d97924ea2ff2` |
| work/decoder-readiness-20260911/innovation_benchmark_repaired_tests_v3 | 9526024 | `e59154686a1e3abdcaf9fddf096ac85cd169688109e9b583f2056849555765bc` |
| work/decoder-readiness-20260911/innovation_waveform_acquire_tests_v5 | 8969040 | `91b59df9ff366a54e1a8508e15ddb41730271dc786b33b2bff4e9011cfea0e2f` |
| work/decoder-readiness-20260911/paired_yield_analyze_tests_v4 | 7652984 | `60dc7e4a3bb176b51cb97c9bd933fcea5f3043d72a15e18f7da6f06ea8b0d76a` |
| work/decoder-readiness-20260911/innovation_benchmark_repaired_v3 | 9603376 | `868316592ba71f8cc7049ca7be0207ae33f2e79c3d0e8b1c92990905093b833a` |
| work/decoder-readiness-20260911/innovation_waveform_acquire_v5 | 9076376 | `228b3d1ccd0c34236c1377ea575a6d5de856c667f4ba7d6af83386b87e30af2e` |
| work/decoder-readiness-20260911/paired_yield_analyze_v4 | 8750912 | `6e98d454ae6d7baaa1477f490f467b40b76cfa4c0a9cf3a762b229aa0f64a753` |
| docs/innovation-prospective-analysis-20260911.md | 6428 | `479b6cfae4d252db53b9defa65eff3777f5ba34ebf58c4f993b4c9b9546b0e3d` |
| docs/innovation-benchmark-protocol-20260911-repaired.md | 4210 | `8e7fb32f1d22c6b31558ea3206301d7b4bb32923f95954bc34c81ccf110aa617` |
| docs/innovation-benchmark-protocol-20260910.md | 7348 | `eea3d74c7bb81f2f8c9dfda5576a27d31351e67fcc2226312f27d2056b9fc77a` |
| work/innovation-prospective-20260912-v1/pass-dependence-amendment-v1.json | 2841 | `9aafa3026b27063871de105c2f0e87cdf617be572325128edec1b1241d47c8b3` |
| work/decoder-readiness-20260911/known-development-transport-control-v1/result.json | 1234 | `825a13736d417520a32cfb6e403ec88796b5a1d5e10b9db8a1e6fe2756bbc738` |

## Limits

- No historical or prospective evaluation cohort is released by this review.
- The historical 266-row proposed subset remains blocked by incomplete local exposure/lineage qualification; future prospective metadata and release do not yet exist.
- Selected runtime hashing is not a complete OS/shared-library closure or cryptographic execution authentication.
- The paired analyzer is not receiver admission, full task coverage qualification, failure-cost accounting, or global independence certification.
- Historical waveform transport remains bound to the historical plan; a prospective metadata selector and authorized downloader are additional work.
- Unit tests and a known-development transport control do not establish telemetry gain, false-alarm rates, scientific novelty, production readiness or publication readiness.
- The mutable source files may acquire a later version; this receipt applies only to the exact snapshots listed.

Reviewer RF DSP runs: **0**. Reviewer waveform downloads: **0**. Test fixtures are metadata and small owned process checks; the explicit development scoring test reads retained results. No cohort, receiver change or journal submission is authorized by this report.

