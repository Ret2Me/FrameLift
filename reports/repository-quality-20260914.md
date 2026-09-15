# Repository quality and receiver regression — 2026-09-14

The maintained receiver now has a warning-free CPU/CUDA product build, a single
local quality gate, CI configuration, tested CLI boundaries and clearer module
ownership. Same-recording regression found **no changed scientific tasks and no
lost or added frames**. This is engineering qualification, not an enterprise
certification, a fresh-cohort result or a deployment approval.

## Changes

- Split the 1,233-line primary CLI into argument schema, orchestration, bounded
  JSON I/O and diagnostics. The process entry point is now 20 lines. Command
  help, capabilities, flags and exit conventions retain their previous behavior.
- Replaced positional parameter lists with named PSK tracking, replay, Meteor
  decode and SSTV clock types. The soft-stream callback has a named type with
  the original lifetime contract; a legacy-signature plugin test protects
  downstream source compatibility.
- Cleaned up production lint findings and formatting without changing floating
  operation order. Three local, explained indexing expectations remain in
  matrix factorization/symmetric-update loops; there is no blanket lint waiver.
  Fixed broken API-documentation links.
- Added 18 tests for CLI I/O/errors/options, legacy plugin compatibility and the
  standard-library-only Rust `xtask` runner. It uses explicit process arguments,
  fail-on-error sequencing and a workspace lock. A concurrent run was rejected
  before invoking a build. Direct Cargo commands must still respect the
  documented no-rebuild-while-testing rule.
- Added repository-pinned formatting/lint tools, read-only SHA-pinned CI,
  dependency scans, binary fixture attributes and build/cache exclusions.
  Independently locked image tooling and frozen `work/` snapshots remain outside
  the new workspace; both metadata paths were checked.
- Replaced the accumulated README with current operating guidance; preserved the
  former content in `RESEARCH.md`. Added contribution, architecture, operations,
  security and release guidance. Historical research, recordings, reports and
  frozen executables were not overwritten.

See [architecture](../docs/architecture.md), [contribution rules](../CONTRIBUTING.md)
and [release requirements](../docs/release-checklist.md).

## Verification

| Final suite | Passed | Failed | Ignored in that invocation |
|---|---:|---:|---:|
| CPU receiver library | 444 | 0 | 6 |
| Main CLI unit tests | 10 | 0 | 0 |
| Black-box CLI integration | 20 | 0 | 0 |
| Meteor / SSTV binaries | 2 / 9 | 0 | 0 |
| Independent readiness auditor | 34 | 0 | 2 |
| Rust developer tooling | 6 | 0 | 0 |
| CUDA-feature host checks | 10 | 0 | 2 |
| Independent image audit tool | 1 | 0 | 0 |
| Explicit NVRTC kernel compilation | 1 | 0 | 0 |

These are execution counts; CUDA host checks repeat some tests. The main gate
ignored ten entries, including the NVRTC test that was then executed explicitly.
Dataset-dependent tests and actual GPU execution were not silently counted as
passes. The final [quality log](../work/repository-quality-20260914-v1/check-final.log)
records the exact commands and results. The first complete pass is also retained
as `check.log`; final results include the additional legacy-plugin test.

`cargo xtask check` passed, including strict CPU and CUDA Clippy, formatting and
warning-free rustdoc. [All research targets compile](../work/repository-quality-20260914-v1/research-check.log);
their existing unused-code/import warnings remain visible, outside the strict
product lint gate. The independently locked image tool also passes its test.
CI YAML and its read-only permissions were checked locally; **no remote CI run,
Git commit or deployment was performed**. This workspace has no root Git history.

A final rebuild after adding workspace exclusions retained the exact frozen
release hash below. The [231-file source/support inventory](../work/repository-quality-20260914-v1/source.sha256)
was verified with `sha256sum --check`.

## Dependency audit

Cargo-audit 0.22.2 with `--deny warnings` found zero known vulnerabilities and
zero warnings in both lockfiles: [receiver, 89 entries](../work/repository-quality-20260914-v1/dependency-audit.json)
and [image tool, 41 entries](../work/repository-quality-20260914-v1/image-tool-dependency-audit.json).
The shared RustSec database contains 1,246 advisories at commit
`e2e640471715167f73e22eaf761f2e547adafeec`, updated 2026-09-14.
This scan does not cover external FFmpeg/NVIDIA packages or prove absence of
unknown vulnerabilities. The image scan used that same database without refetching.

## Real-recording regression

Input is the previously exposed development WAV for observation **14967393**,
SHA-256 `ff24c8dbe3f4c4efb981b3074fe3f3cfa85c8e203fa29acb6402ea2e970ddb21`.

| Compared run | Windows | Completed tasks | Unique full frames |
|---|---:|---:|---:|
| Prior frozen release, CPU FIR 1 | 196 | 1,372 | 20 |
| Refactored release, CPU FIR 1 | 196 | 1,372 | 20 |
| Refactored release, CPU FIR 4 | 196 | 1,372 | 20 |

Every scientific task matches: channel models, timing ranks, decisions reported
in tasks and received-frame bytes. Only the session binding and exact
`elapsed_seconds` keys are removed for cross-release comparison; committed
artifacts and CRC/structure are checked by separate audits. The existing
same-build production audit was **not weakened** to permit different binaries.
Full snapshots match after removing only their session binding.

The [cross-release receipt](../work/repository-quality-20260914-v1/cross-release-parity.json)
and [same-build CPU1/CPU4 audit](../work/repository-quality-20260914-v1/real-14967393/r0-parity.json)
record zero lost frames, zero added frames and zero changed tasks. The 20 full
frames occupy 5,320 bytes including framing/FCS; this is not 5,320 newly recovered
application-payload bytes.

The [independent FIR qualification](../work/repository-quality-20260914-v1/fir-qualification.json)
also passes all **195 bit-exact cases**, including chunks covering the full WAV.

Recorded replay wall times were 88.69 s and 111.75 s. These runs overlap other
development work on a shared VM; **no speedup conclusion is justified**. This is
not a new comparison with SatNOGS, Dire Wolf or gr-satellites, and not a holdout.

## Build identity and remaining gates

Frozen executable:
[telemetry-yield-rs](../work/repository-quality-20260914-v1/release/telemetry-yield-rs)

- SHA-256: `5177d10ff75774f65a627978e96e8f2d01920466a60d8700d42a66c623841670`
- Size: 48,896,864 bytes; Rust 1.98.1, LLVM 22.1.8, x86_64 Linux.
- Build: `cargo build --locked --release --features cuda --bin telemetry-yield-rs`.
- CPU remains default; CUDA remains optional. Main CLI GPU selection fails
  explicitly with exit 1 on this host because the NVIDIA driver is unavailable.
- Source/support manifest SHA-256: `0eaeb6556487187050572fdc7fdec5226424fd831ec572002cfb6210cb1cfe49`.

The kernel compiles successfully with NVRTC, but **there is no actual GPU
execution result on this VM**. Hardware parity/performance, external dependency
audits, deployment-specific soak/fault/SLO tests, incident response and any
service authentication/tenancy requirements remain open. Publication still
needs a frozen independent cohort and its scientific controls. Neither
`deployment_ready` nor `publication_ready` is set to true.

The [machine-readable report](repository-quality-20260914.json) binds logs and
regression artifacts by checksum.
