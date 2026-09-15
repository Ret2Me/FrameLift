# Performance-only historical restart, 2026-09-12

User explicitly requested stopping the old campaign and restarting on the new
optimized executables. This is a disclosed repeat evaluation, NOT a fresh unseen
holdout. Prior partial results have been inspected and must remain archived.

The original 266 CANVAS recordings, their order, common PCM conversion, five
arms, external baseline binaries, complete-bank/FCS validation, 4 observation
workers, 2 native threads and 900-second per-arm cap are unchanged. No new
waveforms are downloaded. No old arm output is reused in the new campaign.

Progressive: cd2c8adcf9db34e5f177753357e7915ac29189816ce73e11e90814c0f3d96acf.
Innovation-v2: c406199366ecccef6ce14d4c13600d8cd6e3019ac9a69921430d80e59424960b.
Innovation-v1 and both external baselines remain the original comparison arms.

Changes are cache/buffer reuse, release-build optimization, and the tested
native-report-only reader increase from 64 to 256 MiB. No new receiver algorithm,
threshold, hypothesis pruning or cohort-dependent tuning is introduced here.
Existing qualification uses two previously used development recordings twice,
four progressive synthetic controls and four innovations synthetic controls.
Equality on these controls is measured, not a mathematical all-input proof.

Primary and secondary yield analyses, cluster bootstrap, missingness reporting,
deduplication and cost analysis retain the original historical registration at
work/publication-execution-20260912-v1/historical-analysis-registration-v1.json.
Non-complete outcomes are not zero frames. The existing 900s cap may still cause
incomplete observations; such outcomes remain visible and are not silently
replaced or excluded from reporting. Any later retry must be separately declared.

Full-bank results from already completed old observations may be used for a
separate equivalence audit, not for tuning, combining old/new results, or claiming
a new independent sample. Runtime comparisons occur on a shared host and refer
to the combined implementation/build changes, not an isolated single technique.

The new freeze, release and current check explicitly inherit the original local
lineage review but disclose prior evaluation exposure. No fresh global holdout,
independent review, publication readiness or universal modulation claim is made.
