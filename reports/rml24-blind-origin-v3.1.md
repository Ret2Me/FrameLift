# RML24 blind-origin v3.1 — provenance-complete replay

## Verdict

v3.1 is an **exact, locally provenance-locked replay** of the already exposed
v3 numerical result. It is not a new holdout and is not an independent audit
PASS. The old v3 series remains unchanged and retains the independent verdict
`FAIL_CLOSED_PROVENANCE`.

The replay reproduces every persisted transfer/holdout aggregate and success
value exactly. The canonical metric core SHA-256 is
`4206f4c4b2e32f5a8c2af2808b0feca0ea37a892a00e1d565f3ffa8e20b397e0`,
identical to the same core extracted from historical v3.

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

Holdout wrong-record v3 is `0.491874880336971`. Random-candidate oracle
+/-1024 is `0.461021264120237`. Missing truth edges continue to count as
errors, and all edge-accounting checks remain part of the hashed metric core.

## What v3.1 fixes

The v3 freeze omitted `work/rml24/diagnose_acquisition_v2.py`, even though the
evaluator imported its scorers, aggregation, RNG constants and controls. v3.1
locks and verifies every local Python file loaded by the replay before importing
the evaluator, NumPy or SciPy. The post-run loaded-module audit found no extra
local file and no locked-but-unloaded file.

The exact locked local set is:

- `work/rml24/run_blind_origin_v31.py`
- `work/rml24/run_blind_origin_v3.py`
- `work/rml24/diagnose_acquisition_v2.py`
- `src/telemetry_yield/__init__.py`
- `src/telemetry_yield/canonical.py`
- `src/telemetry_yield/crc.py`
- `src/telemetry_yield/license_gate.py`
- `src/telemetry_yield/metrics.py`
- `src/telemetry_yield/models.py`
- `src/telemetry_yield/rml24_benchmark.py`
- `src/telemetry_yield/rml24_physical_plugin.py`

The evaluator itself emitted the complete final JSON, including provenance,
production-interface metadata, limitations, claims and the metric-core hash.
It used exclusive creation and mode `0444`, then emitted a separate exact SHA
sidecar. There was no post-hoc JSON edit.

## Locked inputs and environment

The read-only freeze lock has SHA-256
`158992efdcee02e9df0440ebbb1dc47fb99b2b9c95da53a848d2653ddf053ebc`.
Its inventory hashes are:

- local executed code: `c361477c7c78c7517f961764407bbfa79cf59c00199813cae888ada99e46ca15`
- supporting config/evidence: `13e68992cd476d5fca47db1ed99f9f1795e8195f02a12c1c761d324cf1701a96`
- data: `618f52f54931bcc24f736831e7022864b954706f553c610a6005f7aadf46d8dd`
- environment: `b6453313f98712a675de24cd3dcce5e435d46dcd545413104027eb9ed4de61d3`

The data inventory contains all 120 IQ/truth shards involved in the provenance
chain from development calibration through transfer and holdout: exactly
`1,191,975,360` bytes. Every file was hashed during freeze and again before the
replay.

The environment lock contains the CPython 3.12.3 executable hash, determinism
environment variables and exact filesystem trees for:

- Python standard library: 2396 files / 81,190,344 bytes,
  `d02ec7f9a601293392b0a911ab41f56a07219456826070a3b90a349df8876fe9`
- NumPy 1.26.4: 782 files / 23,309,576 bytes,
  `9009bafd0e007de3e8b38717075963f2995a0e0e70f8fe82d724d27a9e3fc051`
- SciPy 1.11.4: 1258 files / 60,915,022 bytes,
  `368b80156ba71be3272bfd6a1291bb2371f51aefa947ad11d7b5ca0de60251f6`

Bytecode caches are excluded because they are generated mutable derivatives;
the source and native package files that generate them are included.

## Immutable artifacts

- evaluation JSON SHA-256:
  `5893350dc09fc8681c7fa98038eedaf280bcf49bb26eae1df4577f3cb91a1234`
- metric core SHA-256:
  `4206f4c4b2e32f5a8c2af2808b0feca0ea37a892a00e1d565f3ffa8e20b397e0`
- runner SHA-256:
  `f8ffcb83db5f736ca993fb03e094073598e50fb830722d6f9c2ab3401e61fe19`
- prereg/replay protocol SHA-256:
  `27878a5e0cd83a6a63c7b57761776f44e955b6485bc40bafbe47161bc0c5842b`
- config SHA-256:
  `112292914b70876f71a3e0201a776302c7f579942a033da59630961d8db10e4a`

The JSON and both SHA sidecars are mode `0444`. Any subsequent content change
would invalidate the separately stored SHA.

## Commands and cost

```text
rtk env PYTHONPATH=src /usr/bin/time -v python3 work/rml24/run_blind_origin_v31.py freeze --prereg reports/rml24-blind-origin-v3.1-prereg.json --config reports/rml24-blind-origin-v3.1-config.json --manifest work/nature-dataset/shards/manifest.json --lock reports/rml24-blind-origin-v3.1-freeze-lock.json --lock-sha reports/rml24-blind-origin-v3.1-freeze-lock.json.sha256
rtk env PYTHONPATH=src /usr/bin/time -v python3 work/rml24/run_blind_origin_v31.py evaluate --prereg reports/rml24-blind-origin-v3.1-prereg.json --config reports/rml24-blind-origin-v3.1-config.json --manifest work/nature-dataset/shards/manifest.json --lock reports/rml24-blind-origin-v3.1-freeze-lock.json --lock-sha reports/rml24-blind-origin-v3.1-freeze-lock.json.sha256 --output reports/rml24-blind-origin-v3.1-evaluation.json --output-sha reports/rml24-blind-origin-v3.1-evaluation.json.sha256
rtk .venv/bin/pytest -q tests/test_rml24_blind_origin_v31.py tests/test_rml24_blind_origin_v3.py tests/test_rml24_acquisition_v2.py tests/test_rml24_physical_plugin.py
```

Freeze used 10.29 seconds wall time and 61,400 KiB peak RSS. Evaluation used
605.51 seconds user CPU, 4.94 seconds system CPU, 30:41.83 wall time under heavy
host contention, 112,616 KiB peak RSS and no swap. Focused tests: `42 passed in
28.33s`.

## Claim boundary

This replay does not upgrade v3 into an independent validation. Transfer and
holdout were already fully exposed. The lock is local and self-authored, with no
trusted external timestamp or signed commit. Truth still exists in the scorer
process, although it is not passed into waveform recovery or the origin
estimator. The selected method remains a per-modulation/rate constant rather
than a universal dynamic burst synchronizer. Packet yield, full blindness and
SatNOGS comparability are not claimed. A new independent audit is still needed.
