# Documentation

[Back to the repository](../README.md)

## Understand the receiver

| Start here | What it answers |
|---|---|
| [How it works](how-it-works.md) | What problem are we solving, and why try multiple receivers? |
| [Architecture](architecture.md) | Where do samples, tasks, packets and evidence flow? |
| [Support matrix](support-matrix.md) | Which modulation, framing and coding paths actually exist? |
| [Comparison with other projects](comparisons.md) | What is different, and what is not established? |

## Use and operate it

| Guide | Purpose |
|---|---|
| [Native receiver guide](rust-receiver.md) | CLI examples, explicit IQ plans, metadata and library integration |
| [Native research tools](native-research-tools.md) | Offline cohort selection, packet audits, yield metrics and SQLite leases |
| [Native archival experiments](native-archive-workflow.md) | Exposure audit, acquisition, frozen paired runs and packet-level reports |
| [Repository migration status](rust-migration-status.md) | Verified Rust replacements and remaining Python responsibilities |
| [Progressive execution](how-it-works.md#progressive-execution) | Quick/deep/full budgets, checkpoints and resumption |
| [CPU/CUDA configuration](enterprise-compute-and-benchmark-v1.md) | Backend scope, identity and parity checks |
| [HDLC channel memory](hdlc-channel-memory.md) | Causal cross-window IQ models and signal-only bootstrap; experimental |
| [HDLC coherent CPM](hdlc-cpm.md) | Flag-trained complex-IQ recovery of variable-length AX.25 frames |
| [Soft marker acquisition](soft-acquisition.md) | Additive fixed-frame IQ acquisition with bounded search and unchanged integrity checks |
| [FEC-assisted acquisition](fec-assisted-sync.md) | Damaged-marker RS/LDPC recovery with separate received-integrity validation |
| [Operations](operations.md) | Worker isolation, input safety, failures and restart behavior |
| [Contribution guide](../CONTRIBUTING.md) | Tests, coding rules and evidence-preserving changes |

## Evaluate the evidence

| Guide | Purpose |
|---|---|
| [Benchmark results](benchmarks.md) | Complete 266-observation result and honest cost accounting |
| [Acquisition follow-up](../reports/acquisition-recovery-20260919.md) | Synthetic gain, retained false-accept failure and null incremental real-IQ result |
| [Soft-list, combining and scheduler](soft-list-combining-scheduler.md) | Reliability-ordered FEC, cross-observation LLR fusion, adaptive gating and GPU estimate contract |
| [Soft-recovery validation](../reports/soft-recovery-validation-20260919.md) | Controlled gains, scheduler task reduction, regression receipt and evidence boundary |
| [GPU estimate](../reports/gpu-speedup-estimate-20260919.md) | Amdahl scenarios for current FIR-only and future batched FEC acceleration |
| [Reproduction](reproducibility.md) | Verify the compact evidence; distinguish that from replaying DSP |
| [Current paper](../publication/decoder-paper-v2/README.md) | IEEE-style manuscript, PDF, bibliography and source |
| [Roadmap](roadmap.md) | Remaining scientific and operational acceptance gates |
| [Research archive](../RESEARCH.md) | Historical experiments; not current product promises |

The pages above are the current entry points. Other dated guides preserve specific
experiments and configurations. When a historical result and current capability
description differ, use the executable hash and study version to identify which
claim is being made; do not silently update old experiment evidence.
