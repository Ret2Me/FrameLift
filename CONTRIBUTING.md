# Contributing

The maintained product is the Rust receiver. Linux, the toolchain in
`rust-toolchain.toml`, and `Cargo.lock` define the supported development build.
Keep patches focused: do not rewrite archived Python or historical benchmark
scripts as a side effect of a receiver change.

## Before changing behavior

Identify which contract changes: samples, decoded frames, provenance, persisted
state, CLI, or resource use. Write a regression test at that boundary. A test
that only compares frame counts is insufficient when payloads or origins can
change. Keep failures and valid empty observations distinguishable.

Use named configuration types for related parameters and keep ownership clear.
Prefer small, explicit functions to speculative plugin frameworks. Comments
should explain constraints, units, numerical conventions and safety, not narrate
each line. Keep library errors actionable; do not turn a failed window into an
empty successful result or catch a panic as normal control flow.

Numerical refactors must preserve evaluation order, thresholds, tie-breaking,
signed zero and decoded bytes. Do not enable fast math, contract multiply/add,
prune hypotheses, or change a checksum rule as a cleanup. Any intentional
scientific change needs a separately identified experiment and evaluation.

## Local checks

```sh
cargo xtask fmt
cargo xtask check
cargo xtask research-check
```

`check` covers maintained Rust formatting, warning-free CPU and CUDA-feature
library/binaries, tool tests, receiver unit/CLI tests, the independent auditor,
and warning-free public API documentation. Research examples are compile-checked
separately; their existing warnings stay visible. Formatting follows the module
trees rooted in `rust/`, not the historical examples or scientific artifacts.

Quality runs execute serially under a workspace file lock. **Do not run another
Cargo build against the same target tree while tests execute.** Some tests hash
their running executable; rebuilding it invalidates their evidence. Cargo's
ordinary build lock does not protect a test process after compilation. The
quality lock coordinates `xtask`, not arbitrary Cargo commands.

The CUDA host checks do not execute a GPU. Use the explicit hardware procedure
in [the compute guide](docs/enterprise-compute-and-benchmark-v1.md). Ignored tests
must remain visible in the report, with their reason; they are not passes.

The separate dependency CI job runs a current RustSec scan. To reproduce it:

```sh
cargo install cargo-audit --version 0.22.2 --locked
cargo audit --deny warnings
```

This needs network access and does not audit external FFmpeg/NVIDIA packages.
Do not add advisory exceptions simply to make a release gate pass.

## Evidence and review

Preserve input hashes, frozen binaries, manifests and reports. Create new run
directories. A new executable must not resume an old executable's session by
removing its identity checks. Large recordings stay outside source distribution;
test fixtures remain available with their provenance.

Document the exact commands and results, including failures that led to a fix.
Attach a same-input regression to numerical/receiver refactors. Review unsafe
code, subprocess ownership, path handling and persisted schemas explicitly.
Dependencies and toolchain updates are separate reviewed changes; do not run an
unrelated lockfile refresh in a formatting patch.
