# Native research migration — 15 September 2026

This is an engineering regression report, not a new field-yield benchmark.
Repository-wide migration remains **incomplete**; the
[status inventory](../docs/rust-migration-status.md) lists the remaining scope.

## Delivered code

The `framelift-research` binary exposes eleven native commands for metrics,
event grouping, split/provenance checks, configuration fingerprints, blind
cohort selection, beacon metadata binding, packet audits and SQLite attempts.
No command invokes Python. Reference receiver algorithms and acceptance rules
are unchanged. CI now includes the new binary and integration tests.

The original CI failure was an exact floating-point timing-score assertion in
the Morse oracle test. Commit `1de0fe7` permits at most four ULPs in that score,
while retaining exact decoded text, count, valid fraction and partition order.
A separate test rejects larger/invalid score differences. The
[repair run passed both jobs](https://github.com/Ret2Me/FrameLift/actions/runs/35020996678).

## Local verification

- `cargo xtask check`: passed. The integration checkpoint had 471 passing
  library tests, six explicitly ignored tests, 20 existing receiver CLI tests,
  nine research CLI tests, two research binary tests, 34 independent readiness
  auditor tests and six build-tool tests. Binary tests for the receiver,
  Meteor and SSTV also passed. Strict lint, formatting and Rustdoc passed.
- CUDA host checks: ten tests passed; two hardware/NVRTC tests remained ignored.
  This does not qualify a GPU or establish acceleration parity.
- After adding the complete frozen PCAP report and extracting the SQLite row
  parser, `cargo test --locked --lib research::`: **27 passed**, no failures
  or ignored cases. These are a subset of the library suite, not 27 independent
  satellite observations.
- The new CLI tests clear `PATH` and the process environment. They exercise
  malformed input, metrics, gates, salt/hash vectors, cohort projection, beacon
  trust flags and leases across separate native processes.
- `git diff --check`: passed.

New dependency auditing is delegated to the required GitHub `dependencies` job;
`cargo-audit` is not installed in the local development environment. Its earlier
green run does not attest the new lockfile. The migration commit must pass a
fresh run before its new dependencies are described as checked.

## Differential checks

Thirteen complete JSON reports matched the original Python implementation:
classic PCAP in both byte orders, both timestamp resolutions and three link
types (RAW, Linux cooked-v2 and an unsupported-link rejection control), plus
one length-prefixed PDU file. Each contained a valid packet, its duplicate and
a damaged-checksum packet. Comparison included byte hashes, packet identities,
timestamps, rejection reasons and all counts, not just recovered-frame totals.
One full independent reference report is now a literal Rust regression fixture.

SQLite interoperability passed in both directions:

1. Python reserved a row; Rust completed it; Python read the terminal result.
2. Rust reserved a row; Python completed it; Rust read the terminal result.

Native tests additionally check the exact expiry boundary, stale-token rejection,
terminal-state idempotency, malformed stored data and one reservation winner
among 16 simultaneous workers. They do not replace production crash/durability
qualification on every filesystem.

Frozen reference outputs cover configuration hashes (including Unicode,
exponents and signed zero), eight integer-ranking boundary cases, a 30-row
cohort order, complete beacon metadata and a complete PCAP report. Some native
validation is deliberately stricter; the [tool guide](../docs/native-research-tools.md)
documents those boundaries. The one-off cross-language checks used synthetic
temporary inputs, not a new scientific dataset or a deployed production database.

## Evidence unchanged

The paper PDF retains SHA-256
`a441168fa1e88734c7e4e1f3125a2821b6775309803247dddd8434057fda25fe`.
Its `evidence/checksums.sha256` retains SHA-256
`576108cca982a4f08c5ccefcc10e8a6958ea60789579a07c50192ce9aeccfcae`.
No recovery totals, comparison images, payload sets or publication conclusions
were changed to accommodate the language migration.
