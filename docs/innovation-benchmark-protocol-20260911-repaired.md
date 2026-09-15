# Repaired-receiver evaluation amendment, 2026-09-11

This is a new pre-waveform/pre-DSP protocol amendment for the next independent-candidate cohort, not a retrospective change to historical experiments. No new-cohort decoder outcome has been inspected. The base method remains [the five-arm protocol](innovation-benchmark-protocol-20260910.md), except for the explicitly listed software identities and the new exposure/pass exclusion gate below. This does not make the study universally confirmatory or publication-ready.

## Fixed receiver choices

- `progressive_v3` denotes the progressive algorithm lineage; for this run its exact executable is `/home/ubuntu/telemetry-yield/work/decoder-runtime-repair-20260911-v1/bin/telemetry-yield-rs`, SHA-256 `e59e8dac6991984de1b7b1e7238835dd3ad740150ae5004070c215290ee2fda3`. This is the repaired normalization build, not historical executable `633f1f4e…`. Full mode retains the same timing, blind and multi-anchor search policy. Known-packet/public-task regressions and negative controls precede this cohort.
- The `innovation_v2` arm uses `/home/ubuntu/telemetry-yield/work/decoder-runtime-repair-20260911-v1/bin/innovation_audio_probe`, SHA-256 `638066b9c3edee6227373aa7919c4ee8d9705ca4ad89426f30398987b6e5042a`, with the previously specified pooled-codec/original-OGG side information and exact common-PCM16 equality check.
- The historical no-codec innovation-v1, Dire Wolf and gr-satellites arms retain their base-protocol configurations. Each actual binary/profile identity is frozen in the manifest. This is not the historical production SatNOGS IQ chain.
- Experimental M&M probes, the post-hoc CANVAS bit locator and the 5122 reference packet are not benchmark inputs or new default receiver stages.

The runner supports paired **compile-time** `TELEMETRY_BENCHMARK_PROGRESSIVE_PATH` and `TELEMETRY_BENCHMARK_PROGRESSIVE_SHA256` bindings, plus `TELEMETRY_BENCHMARK_PROTOCOL_PATH` for this document. The compiled runner is copied to an immutable versioned path and hashed before use. Runtime environment changes cannot switch the receiver of a saved session. Old defaults and old binaries remain preserved. The manifest records the exact progressive binding in addition to its historical arm label.

## Data qualification before any new waveform access

The recovered 500-row metadata selection is not, by itself, an independent test set. A new timestamped audit must inventory actual earlier waveform/decoder exposure and reject direct observations and conservatively overlapping same-satellite passes, including other stations and private namespaces. Metadata-only pagination is not waveform exposure. Any selected subset preserves the original relative order, documents exclusions, and does not replace excluded rows based on receiver outcome.

**Do not launch new waveform acquisition or DSP until that audit has an explicit machine-readable successful local-exposure decision and the derived cohort/input identities are frozen.** Incomplete inventory, unresolved identities or ambiguous timing remain explicit limitations or exclusions, not silent proof of nonexposure. Local inventory cannot prove global independence from all operator or earlier human exposure. The evaluated scope remains CANVAS/FSK9600/AX.25 G3RUH, not all missions or protocols.

## Analysis and operational constraints

Keep the base five-arm metrics, common PCM16 representation, codec side-information distinction, rotating arm order, no repair in Dire Wolf, full-search completion requirement, retained failures and pass-cluster analysis. Four observation workers and two DSP threads each are the maximum. The 900-second receiver bound is a safety policy; incomplete full searches must not be reported as zero. Resume uses frozen identities. Operational milestones are not outcome-dependent stopping rules.

Both added and missed exact observation–PDU pairs must be reported. A union including a baseline is not intrinsic decoder noninferiority. Archive novelty requires a separate comparison with archived payloads and is not implied by comparator absence. No results, significance, effect size, archive gain or journal readiness are asserted in this pre-run document.
