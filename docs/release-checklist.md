# Receiver release checklist

A passing build is not a commercial deployment or a publishable experiment.
Use this checklist to record a specific release decision, not to imply one.

## Source and build

- Run `cargo xtask check` and `cargo xtask research-check`; retain the logs and
  counts of passed, failed and ignored tests. Include hardware reasons.
- Record source revision (or a complete source-file hash inventory), toolchain,
  lockfile, feature flags, platform and executable hashes. This workspace has
  no root Git history; do not invent a commit identifier for it.
- Copy release executables to a new immutable directory before running any
  benchmark. Preserve previous qualified executables and sessions.
- Distribute explicit product inputs: maintained Rust sources and fixtures,
  Cargo manifests/lockfile, toolchain, `tools/xtask`, native configuration
  examples, operating docs and attribution. If distributing research examples,
  include their required fixtures and label their qualification separately.
  Do not archive the entire laboratory workspace or include credentials,
  `work/`, raw recordings, `target/` or private metadata.

## Correctness and compatibility

- Replay same-input golden tests and at least one complete known recording
  against the previous frozen release. Compare full frames, task coverage,
  timing ranks, channel models and provenance—not just counts.
- Record intentional differences with a versioned scientific justification.
  Refactors/acceleration must not silently alter numerical behavior.
- Test malformed input, changed input during processing, restart, cancellation,
  partial output, path attacks and disk/resource failures. Never bypass identity
  checks to resume a different build.
- Run actual GPU parity tests on every supported hardware/driver family before
  qualifying CUDA. Include multi-stream/tile-boundary cases and complete CPU/GPU
  paired runs. A host-only CUDA check cannot close this gate.

## Publication benchmark

- Freeze the research question, cohort selection, exclusions, metrics, baseline
  versions/configuration and tuning policy before examining held-out results.
- Use exactly the same source representation for all comparable receivers.
  Report observation-local and global deduplication, added frames, missed frames,
  valid empty observations and failures separately.
- Report the confirmed-signal subset separately from all attempted observations.
  Known development recordings are regression evidence, not fresh holdout data.
- Include false-positive controls, full coverage, independent integrity audit,
  statistical uncertainty and ablations. Do not infer novelty from a larger
  search budget or GPU acceleration.
- Measure repeated, order-balanced, isolated end-to-end latency and resources;
  distinguish it from kernel-only speed and summed parallel worker time.

## Commercial operation

- Close the deployment's dependency/security audit, incident-reporting channel,
  backup/recovery, soak/fault testing and measured resource/SLO requirements.
- If exposed as a service, supply authentication, authorization, tenant isolation
  and operational monitoring outside this offline CLI and test them explicitly.
- Document supported missions/profiles and remaining unsupported combinations.
  Do not advertise universal superiority, a certification or an SLA without
  the corresponding evidence and organizational commitment.
