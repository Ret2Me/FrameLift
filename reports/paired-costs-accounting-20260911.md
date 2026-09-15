# Paired receiver cost accounting — 2026-09-11

Implemented [paired_costs_analyze.rs](/home/ubuntu/telemetry-yield/examples/paired_costs_analyze.rs). This is a read-only reporting component, not a demodulator or scientific qualification gate.

The assembler reports each selected observation/receiver separately and inventories all retained canonical `attempt-*` directories. It keeps **final committed arm costs** separate from **all retained attempt costs including retries**. Missing, malformed, uncommitted and timed-out evidence is explicit; missing CPU/RSS or wall time is never replaced by zero. GNU time user CPU, system CPU and maximum RSS are rederived from hashed retained usage files. Maximum RSS is a maximum, not a sum.

The actual `run.process.json`, logs, committed arm artifacts, source/input path declaration, exact arm command/options, comparison manifest and observation metadata are bound and rechecked. Inventory changes during collection fail. Historical process receipts contain no OS PID; the report explicitly identifies them by canonical receipt path plus SHA256 and byte count and does not invent a PID. Receipt consistency is not cryptographically authenticated execution.

## Validation

12/12 no-DSP tests passed, including real-format temporary fixtures for a timed-out first attempt plus a successful retry, an unattempted receiver, tampered logs, escaped commits, orphan attempt directories and unavailable resource values. Tests independently distinguish final2s from total902s in the synthetic retry fixture. No RF samples or decoder processes are involved.

A read-only replay of the previously exposed historical old20 campaign completed: **20 observations, 100 final complete arms, 100 retained complete attempts**, with no unverified attempt. **722** file identities and **240** directory snapshots were recorded and rechecked. No actual retry or timeout occurred in this historical sample; their handling is tested with explicit fixtures, not falsely inferred from this sample.

| Historical arm | Complete | Sum child wall s | Sum user CPU s | Sum system CPU s | Maximum RSS KiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| direwolf | 20 | 73.347 | 70.390 | 0.940 | 5888 |
| gr_satellites | 20 | 83.035 | 127.480 | 20.270 | 89448 |
| innovation_v1_no_codec | 20 | 2562.456 | 4846.750 | 32.230 | 545452 |
| innovation_v2 | 20 | 2744.522 | 4954.650 | 52.620 | 617264 |
| progressive_v3 | 20 | 6565.296 | 10115.190 | 231.800 | 39480 |

These are old development receiver versions and concurrent child-process costs including startup. Summed child wall is not elapsed campaign time or isolated latency. Acquisition/conversion and temporary PCM creation are outside this receiver-cost report; their receipts must accompany the complete study report. Arm completion here follows internally consistent retained status/process records; full frame/task/configuration admission remains the receiver audit's responsibility.

## Artifacts and usage

- Source SHA256: `987b465fceedec4c6771438f377403c1a5e12f36d489cfaa02f6a9f1ae3623de`.
- Binary `paired_costs_analyze_v1`: `89378ad3c5f91169926894ce24e240825311f2c54fbffe9ebab7dd5ab8158149`.
- Test binary `paired_costs_analyze_tests_v2`: `22d5b6a7a4810505f08eb78c5e67c4daad955480836703f107f3554ede1ded77`.
- [Old20 cost report](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/old20-paired-costs-analysis-v1.json): `767308f347707f8d978c47c1773dd2d07e0ca325be0cb0fb975760a27ed81d38`.
- [Build/test/replay receipt](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/paired-costs-build-and-check-v1.json) records exact commands and identities.

```text
paired_costs_analyze_v1 --cohort /absolute/cohort.json --comparison /absolute/comparison --output /new/costs.json
```

Output is create-new: existing reports are never silently overwritten. It is an observed local inventory, not proof that no discarded historical attempts ever existed. No new waveform was acquired or decoded. No evaluation cohort is released and no publication-readiness claim is made.

