# RML24 blind-origin v3.2.4 — full-snapshot provenance replay

## Verdict

The formal v3.2.4 replay completed with every local fail-closed gate passing.
It reproduces the previously exposed v3 numerical result exactly. This is not
a new holdout and is not, by itself, an independent audit PASS; a separate
auditor must review these artifacts before any provenance PASS claim.

The canonical metric-core SHA-256 is
`262e1830c0a532312edac77a9e2367dc4e41c6db88cbd7c83d2524610be61f61`.
All seven preregistered historical-reproduction comparisons are exact.

| Split / method | Errors / truth bits | BER |
|---|---:|---:|
| Transfer zero shift | 319755 / 668544 | 0.478285647616313 |
| Transfer acquisition-v2 | 319591 / 668544 | 0.478040338407046 |
| Transfer blind-origin v3 | 264980 / 668544 | 0.396353867509094 |
| Transfer oracle +/-8 | 306818 / 668544 | 0.458934640053609 |
| Transfer oracle +/-1024 | 109203 / 668544 | 0.163344521826536 |
| Holdout zero shift | 319287 / 668544 | 0.477585618897186 |
| Holdout acquisition-v2 | 319389 / 668544 | 0.477738189259047 |
| Holdout blind-origin v3 | 256817 / 668544 | 0.384143751196630 |
| Holdout oracle +/-8 | 305508 / 668544 | 0.456975157955198 |
| Holdout oracle +/-1024 | 111515 / 668544 | 0.166802783362052 |

Holdout wrong-record v3 is `0.491874880336971`. Missing truth edges count as
errors. Truth is used only by the final scorer and labelled controls, not by
waveform recovery or the runtime origin estimator.

## What v3.2.4 closes

The older v3 and v3.1 artifacts remain unchanged and retain their historical
`FAIL_CLOSED_PROVENANCE` status. The intermediate v3.2, v3.2.1, v3.2.2 and
v3.2.3 attempts are also not promoted: they respectively stopped before a
complete formal replay or exposed missing full-data snapshot, attestation,
lazy-native-load, or import-closure gates.

V3.2.4 addresses the resulting provenance requirements as follows:

- The launcher creates a fresh empty `PYTHONPYCACHEPREFIX`, sets
  `PYTHONDONTWRITEBYTECODE=1`, and starts the child with `python -B -v`.
- The verbose trace observes the exact 16 expected local Python source files,
  with zero missing, zero unexpected and zero local `.pyc` loads.
- Code, supporting evidence/config and all selected IQ/truth data are copied
  byte-for-byte into a new snapshot. The snapshot contains 193 files and
  1,195,946,200 bytes. Every snapshot file has a different inode from its
  origin, so this is not a hardlink snapshot.
- All 120 selected IQ/truth shards (1,191,975,360 bytes) are read only from the
  snapshot during DSP. Both origin and snapshot are fully rehashed after the
  child finishes, before the result is accepted.
- The launcher and child independently inventory every regular executable
  mapping in `/proc/self/maps`, including CPython, NumPy, SciPy, BLAS/LAPACK,
  libc/libm/libmvec, libstdc++, libgfortran, crypto/ffi and the lazily loaded
  `mmap.cpython-312-x86_64-linux-gnu.so`. Native inventories and hashes match
  the frozen lock before and after evaluation.
- The child writes the complete final JSON with exclusive creation. The
  launcher verifies and preserves that exact child output; there is no
  post-hoc field injection.

The snapshot and output files use mode `0444`, directories use `0555`, and
artifacts have separate SHA-256 sidecars. This is accurately described as
**write-protected, tamper-evident and O_EXCL-created**, not as filesystem
immutable. The attestation explicitly records
`filesystem_immutable_claimed=false`.

## Formal artifacts and hashes

| Artifact | SHA-256 |
|---|---|
| `work/rml24/launch_blind_origin_v324.py` | `2bd02b0ea00bf627a9629c1b6eeb787e39962099e2e03d46f3a6799d97b47991` |
| `work/rml24/run_blind_origin_v324.py` | `c55edcdf2d579a5967969d220dac5a83ba8057669946102b22b9e95b262789cb` |
| `tests/test_rml24_blind_origin_v324.py` | `13c9eac4ccce9e6c91c9e7b705cbfcb81eca4d8bbe36e72f36c48c960ae39321` |
| preregistration | `5b155afacd8cf4baf6b63c5502c2a56efacc26850c6d440abd5a15ffa5d95e7a` |
| configuration | `1c748e42f19fcabb61539000d2a745a5e215b72e57862cff72b587b8ee8eaad2` |
| freeze lock | `e27782c764d392b6291448931e944fa5af7ff3f6f0cb44e88bd17d7d5b8963e4` |
| freeze launch attestation | `e1ff96b529138f19b89d1a0066fc01d5d70e7add94b755b736131a73152042bc` |
| freeze import trace | `59f4b502203af282f8e379ce5305d8fc1e5de73bc34ee0725898a179d102e596` |
| evaluation launch attestation | `dc8039f42db3cc6eea71541f2e86e86dbf1002fcf863c4851e687208b621e888` |
| evaluation import trace | `313108b5f6c0a5dd6e3e8fbe24f2660f054afea962d301cf65fd43f189b1ea3d` |
| evaluation JSON | `abd0134cb58eb939de6eb7ebad39eee922e70e46a9fc103ca9b1660bf35eb06e` |
| metric core | `262e1830c0a532312edac77a9e2367dc4e41c6db88cbd7c83d2524610be61f61` |
| physical snapshot inventory | `870a8455810980f5c587c8186cc3463f5edc950f606c3e05e29c2768f2777c6f` |

All machine artifacts listed above, except the human report and test file, have
matching separately stored SHA sidecars where the v3.2.4 protocol specifies
one.

## Commands and cost

The formal freeze used the v3.2.4 launcher in `freeze` mode with the
preregistration, configuration, shard manifest and lock paths shown above. The
formal evaluation command was:

```text
rtk env PYTHONPATH=src /usr/bin/time -v /usr/bin/python3 -B work/rml24/launch_blind_origin_v324.py --trace /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-evaluate-import-trace.txt --trace-sha /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-evaluate-import-trace.txt.sha256 --attestation /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-evaluate-launch-attestation.json --attestation-sha /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-evaluate-launch-attestation.json.sha256 -- evaluate --prereg /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-prereg.json --config /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-config.json --manifest /home/ubuntu/telemetry-yield/work/nature-dataset/shards/manifest.json --lock /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-freeze-lock.json --lock-sha /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-freeze-lock.json.sha256 --output /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-evaluation.json --output-sha /home/ubuntu/telemetry-yield/reports/rml24-blind-origin-v3.2.4-evaluation.json.sha256
```

Formal evaluation used 658.36 seconds user CPU, 9.35 seconds system CPU,
34:09.18 wall time under host contention, 120,068 KiB peak RSS and no swap.
The earlier unpublished full preflight used the same numerical core and passed
all gates before formal freeze/evaluation; its output is not the formal result.

## Claim boundary and remaining blocker

The numerical result is promising but bounded: blind-origin v3 reduces holdout
BER from `0.47759` (zero shift) and `0.47774` (acquisition-v2) to `0.38414`,
while a truth-aided +/-1024 oracle reaches `0.16680`. This demonstrates that a
truth-independent fixed per-modulation/rate origin correction recovers part,
not all, of the alignment gap on the already exposed dataset.

It does not establish a universal dynamic synchronizer, packet yield, support
for arbitrary waveforms, SatNOGS comparability or independent holdout
generalization. It also does not establish an independent provenance PASS
until a separate auditor reproduces and reviews the full artifact chain.
