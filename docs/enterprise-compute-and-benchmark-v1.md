# CPU/CUDA receiver qualification candidate — 2026-09-14

This is an implementation and qualification candidate, **not an enterprise
certification or a publication-ready claim**. CUDA hardware execution and speed
must be measured on an actual GPU. A successful CPU test, CUDA-enabled Rust
build, or NVRTC compilation is not a substitute.

## Implemented scope

| Operation | CPU | Optional CUDA feature |
|---|---|---|
| Causal real FIR + decimation (audio / post-discriminator IQ) | Serial or sample-parallel Rayon | Output-parallel f64 kernel |
| Causal complex FIR + decimation (IQ) | Serial or sample-parallel Rayon | Output-parallel f64 kernel |
| Centered complex matched filtering (BPSK/QPSK/OQPSK) | Serial or sample-parallel Rayon | Output-parallel f64 kernel |
| Carrier estimation, FFT, timing loops, MLSE, FEC, framing | Existing CPU implementations | Still CPU |
| Progressive checkpointing and generic IQ run contracts | Backend identity bound into resume contract | Device UUID, driver, NVRTC and kernel/PTX hashes additionally bound |

CUDA is **not a new demodulation algorithm**. It evaluates the same FIR sums on
another processor. Independent output samples/components run in parallel, but
each sum retains tap order. Device code uses `__dmul_rn` and `__dadd_rn`; no FMA
contraction, reduced precision, fast math, tap pruning or parallel reductions.
The technique bank, anchor dependencies, decoded bits and received FCS rules
are unchanged. Do not claim a new frame gain from this acceleration itself.

The main `telemetry-yield-rs` CLI and Rust `compute::Engine` expose the feature.
The separate `telemetry-sstv` and `telemetry-meteor` executables do not expose
these CLI flags. They must not be advertised as GPU-accelerated receivers.

## Building and choosing a backend

Use the pinned Rust toolchain in `rust-toolchain.toml` and retain `Cargo.lock`.
The CUDA dependency is optional, pinned to cudarc 0.19.9, and dynamically loaded
using CUDA 12 driver/NVRTC bindings. CPU-only builds need no NVIDIA libraries.

```sh
cargo build --locked --release --no-default-features --bin telemetry-yield-rs
cargo build --locked --release --features cuda --bin telemetry-yield-rs

# Probe/initialize the selected backend. No decoding occurs.
telemetry-yield-rs --compute cpu --compute-threads 8 compute-info
telemetry-yield-rs --compute cuda --cuda-device 0 compute-info

# Window parallelism and FIR sample parallelism are separate resources.
telemetry-yield-rs --compute cpu --compute-threads 4 decode-progressive \
  --input /data/recording.wav --output /results/cpu-session \
  --mode full --threads 4
telemetry-yield-rs --compute cuda --cuda-device 0 --cuda-streams 2 \
  --cuda-buffer-mib 256 decode-progressive \
  --input /data/recording.wav --output /results/gpu-session \
  --mode full --threads 4
```

Default is CPU and one FIR worker, preserving existing window-parallel behavior.
`--compute-threads` accepts 1–256. More threads are not automatically faster;
sample-parallel work uses one shared pool, not a fresh pool per window. Small
FIRs stay serial in CPU mode. Select resources using development data, not the
held-out evaluation results. CUDA supports 1–16 retained stream lanes. Input
tiles include their exact FIR overlap; outputs are never omitted to fit a
buffer budget. Per-lane buffers are reused. CUDA allocations are limited by
`--cuda-buffer-mib` in aggregate (16–16384 MiB), **excluding** context, driver,
compiler, allocator overhead and host-side buffers. This is not a total GPU
memory or RSS cap. Production jobs still need OS/container resource controls.

CUDA requests fail if the feature, driver, device or NVRTC is unavailable, if
startup arithmetic parity fails, or if a launch/copy/synchronization fails.
There is no silent CPU fallback. `compute-info` initializes the actual selected
device; CUDA startup includes a small bit-exact arithmetic test. Full hardware
qualification remains mandatory. CUDA context initialization takes place after
the progressive worker's exec, never in its pre-fork supervisor.

## Durability and cancellation

Existing immutable task commits, no-clobber publication, fsync, atomic result
snapshots and exclusive session locks remain active. New progressive sessions
use `progressive-audio-session-v3`; do not mutate or resume the historic v2
scientific sessions with the new executable. CPU/CUDA or driver/kernel changes
require a new session; changing resource budgets does not authorize changing
the search bank. Generic IQ checkpoints also bind the compute identity.

SIGINT/SIGTERM requests stop the progressive supervisor normally: it cleans up
its owned worker process group and scratch area, writes a cancellation receipt,
and preserves committed work. A receipt distinguishes `signal`, `deadline`,
`completed` and `worker_error`. Resume explicitly with `--resume`, the same
binary, backend identity, source and search policy. Uncommitted tasks may rerun.
Host power loss/SIGKILL cannot guarantee a final receipt; immutable completed
tasks remain the recovery source. Resource limits are never signal of zero
telemetry or complete processing. Inspect `complete`, not just process exit 0.

`runs/*/compute-start.json` and `compute-finish.json` record configuration,
hardware identity and actual FIR call/output counts. Finish metadata can be
absent on cancellation; that is not a completed GPU/CPU run.

## Required qualification gates

1. CPU default and CUDA-feature builds, unit/CLI/golden tests, malformed-input
   and cancellation/resume tests. Record exit codes, ignored tests and hashes.
2. Numerical FIR parity against the independent serial loop on edge cases and
   the **entire selected source** in explicit chunks. Timing includes device
   copies and synchronization, but not context/JIT/source I/O. The report says
   this is a kernel qualification, not an end-to-end receiver benchmark.
3. On an actual GPU, run the explicitly ignored hardware test, including
   multi-stream concurrent callers and inputs spanning multiple device tiles.
   Missing hardware must fail a hardware qualification job, not count as pass.
4. Run complete CPU and CUDA sessions using **the same CUDA-enabled executable**
   and the same input/policy, with separate new session directories. Audit all
   task/model/rank/frame values. An extra frame as well as a lost frame fails
   exact backend parity; investigate numerical divergence before publication.
5. Measure paired, repeated, order-balanced, isolated end-to-end runs. Include
   source I/O, JIT cold start, transfers, worker setup, checkpoint I/O, teardown,
   CPU time, RSS, GPU model/driver/VRAM and energy if claimed. Report cold and
   warm latency separately. Do not turn summed concurrent worker wall times
   into campaign elapsed time. Report kernels with no speedup too.
6. Deploy as a constrained, unprivileged offline worker and perform soak/fault
   testing before offering an SLA. No unauthenticated network API was added.
   Authentication/RBAC, tenancy, billing, fleet management, vulnerability
   response and certifications remain separate product work, not inferred
   from the decoder's local access permissions.

```sh
telemetry-yield-rs --compute cpu --compute-threads 4 qualify-compute \
  --input /data/recording.wav --repeats 5 --output /evidence/cpu-fir.json
telemetry-yield-rs --compute cuda qualify-compute \
  --input /data/recording.wav --repeats 5 --output /evidence/gpu-fir.json
cargo test --locked --features cuda --lib \
  compute::cuda::tests::gpu_hardware_full_parity_and_concurrent_streams -- --exact --ignored
telemetry-yield-rs audit-compute-pair \
  --cpu-session /results/cpu-session --candidate-session /results/gpu-session \
  --output /evidence/frame-and-task-parity.json
```

The session audit uses shared read locks, verifies source/WAV bytes, task
commitments and geometry, independently rechecks received FCS/UI, reconstructs
the full snapshot, and compares scientific task data. It removes only the
per-task session identifier and `elapsed_seconds`, never decisions or models.
It produces a failed receipt for invalid/partial sessions. It establishes local
artifact consistency, not independent reproduction or transmitter authenticity.

## Publication experiment must remain separate

The all-Rust orchestration command below registers the input/executable and
resources **before** processing, checks device availability in a separate
process, alternates run order across repeats, measures outer process latency,
and invokes the task/frame audit. It retains a failed summary on an error.
Use a new output directory. The per-run deadline is 3600 s and per-file output
limit is 2 GiB; incomplete runs cannot pass. For a CPU acceleration development
comparison, replace `--candidate cuda` with `--candidate cpu` and select
`--candidate-threads`. This does not select or certify an independent cohort.

```sh
telemetry-yield-rs benchmark-compute-pair --input /data/recording.wav \
  --output /evidence/backend-pair --candidate cuda --threads 4 --repeats 5
```

The completed 266-observation CANVAS cohort is exposed development/historical
evidence, not a newly independent holdout. Preserve the earlier artifacts and
their final 6220 versus 4066 endpoint; do not mix new CUDA results into them.
The existing frozen five-arm runner pins old executable hashes and must keep
rejecting replacement binaries. Create a **new registered campaign** for the
new release instead of editing that runner's identities in place.

Before any new outcome inspection, freeze an archival sampling interval,
satellite/station/pass grouping, representation-specific profiles, input hashes,
software/external-decoder hashes, exclusions, acquisition failures, arm resources,
and primary endpoints. Use the same numerical input per eligible comparison;
mono post-FM OGG is not coherent PSK IQ. No universal superiority claim follows
from a GMSK/AX.25 cohort. Include all eligible observations and all losses.

Predeclare two distinct estimands: standalone receiver yield and additive
union yield. Track per-observation unique PDUs separately from globally novel
payloads. Report a separately predeclared **confirmed-signal stratum**, with its
evidence source and denominator; do not select that stratum using our output.
Perform ablations, paired uncertainty by pass/station/satellite grouping, exact
miss analysis, independent reproduction and noise/control exposure. Keep all
missingness visible. Acceleration alone does not establish scientific novelty.

Neither qualification command can set `publication_ready` or `deployment_ready`
to true. Those are independent evidence gates, not flags to relax.

## Numeric references

- NVIDIA, [Floating Point and IEEE 754](https://docs.nvidia.com/cuda/floating-point/index.html).
- NVIDIA, [NVRTC compilation options](https://docs.nvidia.com/cuda/nvrtc/).
- [cudarc 0.19.9](https://docs.rs/cudarc/0.19.9/cudarc/).
