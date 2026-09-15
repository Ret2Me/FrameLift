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
| [Progressive execution](how-it-works.md#progressive-execution) | Quick/deep/full budgets, checkpoints and resumption |
| [CPU/CUDA configuration](enterprise-compute-and-benchmark-v1.md) | Backend scope, identity and parity checks |
| [Operations](operations.md) | Worker isolation, input safety, failures and restart behavior |
| [Contribution guide](../CONTRIBUTING.md) | Tests, coding rules and evidence-preserving changes |

## Evaluate the evidence

| Guide | Purpose |
|---|---|
| [Benchmark results](benchmarks.md) | Complete 266-observation result and honest cost accounting |
| [Reproduction](reproducibility.md) | Verify the compact evidence; distinguish that from replaying DSP |
| [Current paper](../publication/decoder-paper-v2/README.md) | IEEE-style manuscript, PDF, bibliography and source |
| [Roadmap](roadmap.md) | Remaining scientific and operational acceptance gates |
| [Research archive](../RESEARCH.md) | Historical experiments; not current product promises |

The pages above are the current entry points. Other dated guides preserve specific
experiments and configurations. When a historical result and current capability
description differ, use the executable hash and study version to identify which
claim is being made; do not silently update old experiment evidence.
