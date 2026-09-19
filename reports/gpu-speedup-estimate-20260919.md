# GPU acceleration estimate — 2026-09-19

## Scope

This is an Amdahl-law scenario analysis, **not a GPU measurement**. The current
optional CUDA backend offloads ordered f64 FIR filtering only. Acquisition,
timing, sequence detection, BCJR/log-MAP, LDPC, Reed–Solomon, list search, CRC and
framing remain on CPU.

The retained CPU comparison for observation 14967393 processed 1,372 tasks and
28,149,372 FIR output samples. Its one-thread-FIR and four-thread-FIR candidates
took 127.44 and 128.72 wall seconds on a shared VM and produced identical task
and frame outputs. That result does not identify the FIR wall-time fraction and
does not measure CUDA; it only shows that adding host FIR workers was not a
visible end-to-end win in that run.

## Model

All scenarios normalize the measured CPU runtime to 100 units. Predicted time is

`unaccelerated + accelerated / assumed stage speedup + transfer/startup overhead`.

The 3-unit overhead is deliberately visible. It represents transfers, allocation,
kernel launch and synchronization. Real overhead can be lower for persistent
batched buffers or higher for short per-frame launches.

| Scenario | CPU share moved to GPU | Assumed device speedup for that share | Predicted whole-pipeline speedup |
|---|---:|---:|---:|
| Current FIR-only, low eligible share | 10% | 10× | 1.06× |
| Current FIR-only, middle scenario | 25% | 10× | 1.24× |
| Current FIR-only, high eligible share | 40% | 10× | 1.49× |
| Future batched frontend + FEC, conservative | 60% | 10× | 2.04× |
| Future batched frontend + FEC, middle | 75% | 15× | 3.03× |
| Future batched frontend + FEC, aggressive | 90% | 25× | 6.02× |

The defensible engineering expectation is therefore:

- **about 1.1–1.5× end-to-end with the CUDA scope currently in the repository**;
- **about 2–6× only after batching and offloading acquisition/sequence/FEC/list
  work as well as FIR**.

These ranges are hypotheses. They must not appear as measured product claims.
Small codewords may become slower on a GPU unless many candidates are batched.
The unresolved-only scheduler can reduce work independently of GPU acceleration,
but its gain depends on the quick-success rate and the relative cost of skipped
stages.

## Required measurement

Run `benchmark-compute-pair` on the target NVIDIA device, preserve cold and warm
runs, and report CPU/GPU identity, transfer/JIT time, p50/p95 wall time, energy if
claimed, and exact task/frame parity. The new `estimate-gpu` command records the
same formula for measured stage profiles without relabelling an estimate as a
hardware benchmark.

