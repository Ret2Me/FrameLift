# Reproducing the evidence

[Documentation](README.md) · [Results](benchmarks.md) · [Paper build](../publication/decoder-paper-v2/README.md)

Three different operations must not be confused.

## 1. Check the portable reporting bundle

From the repository root:

```sh
cargo run --locked --example paper_evidence -- \
  verify publication/decoder-paper-v2/evidence
cargo test --locked --example paper_evidence
```

This checks file hashes, required artifacts, duplicate IDs, set-count identities,
aggregate counts, timings and the signal-labelled subgroup. The inputs are the
compact frozen report files. It does not download recordings, run a decoder,
validate received FCS from raw packets or establish research independence.

The [evidence inventory](../publication/decoder-paper-v2/evidence/checksums.sha256)
is a corruption/revision check, not an authenticated signature against a party
replacing the whole bundle.

## 2. Re-export from the complete local experiment

This requires the retained laboratory tree, including committed per-observation
results and the final yield/cost analyzer reports:

```sh
cargo run --locked --example paper_evidence -- \
  freeze . publication/new-paper-evidence
```

Use a **new** destination. The exporter refuses existing directories. It processes
the exact 266 rows in the completed analyzer, recomputes additions/losses from
recorded PDU bytes, and rejects disagreements with the final analysis or costs.
The output is a compact count-level bundle, not a copy of every packet/recording.

Source reports live under
`work/publication-speed-restart-20260912-v1/analysis-v2/`. Their hashes, original
decoder identities, profile and historical exposure record are retained in the
bundle. The laboratory tree is not required for operation 1 or building the PDF.

## 3. Replay and independently validate the experiment

This requires original OGGs with matching hashes, the frozen receiver binaries
and source/runtime environments, exact conversion, replay profiles, and the full
runner/control/artifact audit chain. Public URLs are locators, not a guarantee
that remote files remain available or byte-identical.

Do not run the current binary under the old campaign manifest. The paper's P
binary SHA-256 is
`cd2c8adcf9db34e5f177753357e7915ac29189816ce73e11e90814c0f3d96acf`.
The September 14 refactor has a different identity. Its successful one-recording
regression does not transfer an entire historical study to a new build.

Full reproduction should verify source and prepared-sample geometry, all native
task completion, received FCS and exact per-arm packet sets. External exported
PDUs omit FCS; retain the stated reliance on their internal checks or introduce
a separately registered richer capture policy. Log conversion, rejected frames,
attempts, timeouts and costs. Never count an incomplete observation as zero yield.

## Designing the next benchmark

Freeze the cohort and code before examining receiver outcomes. Existing historical
recordings can form the independent set if they are genuinely unexposed and
grouped to control leakage. Preserve zero-yield observations. Run same-input
comparators with documented parameters and measured resources; report additions
and losses at both per-observation and exact-PDU levels. Keep signal labels and
mission attribution separate from successful decoding.

For efficiency claims, add isolated repeated paired timings, CPU usage, GPU model,
transfer/initialization overhead and tail latency. For scientific attribution,
use matched ablations with fixed input, acceptance rules and search/compute
budgets. See the [roadmap](roadmap.md); no untouched holdout is claimed by the
current paper.
