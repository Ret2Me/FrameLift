# Retained research overview

Snapshot of the former README, retained on 2026-09-14. The dates, results and
qualification statements below describe their original experiments, not the
current build. Start with [README.md](README.md) for the maintained receiver.

# telemetry-yield

Reproducible, fail-closed foundations for generic telemetry recovery from
recorded IQ. SatNOGS is a compatible baseline/backend, not the boundary of the
program. The acceptance path remains classical:

`IQ → high-recall selection → waveform plugins → FEC/deframe profile → protocol validation → union`

Machine learning may later rank configurations, but it never creates or
accepts payload bits.

## Native Rust receiver (September 2026)

A standalone Rust receiver and bounded parallel CLI now live in `rust/`, with
the root `Cargo.toml`/`Cargo.lock`. See [build and usage](docs/rust-receiver.md)
and the [scope/qualification inventory](reports/rust-migration-inventory-v1.md).
Measured migration results and immutable build identities are in the
[qualification report](reports/rust-migration-results-20260908.md).
Python research sources/results are retained as immutable scientific oracles,
not runtime dependencies of the Rust receiver. OGG retains external FFmpeg
to reproduce the original PCM exactly. Migration qualification is distinct
from the historical research results below.

An opt-in [residual / codec-conditioned Rust experiment](docs/innovation-receiver.md)
adds matched receiver lanes without changing the qualified defaults. Its scope
is mono post-FM FSK/GMSK audio and native AX.25 UI validation; it does not add
general PSK/FEC coverage or establish scientific novelty by itself.
See its [measured development results and negative codec ablation](reports/innovation-receiver-results-20260910.md).

The optional [v2 pooled-codec extension](docs/innovation-v2-usage.md) combines
calibration evidence from disjoint, CRC-valid source windows. Its completed
[original-OGG OLD20 regression](reports/innovation-pool-development-20260910.md)
preserved all 140 observation/PDU pairs (121 globally distinct PDUs), but added
none. This is a development result, not evidence of improved reception or a
fresh-cohort validation. V2 can also bind original codec metadata to the exact
shared PCM16 waveform used in external decoder comparisons.
The completed [five-arm shared-PCM benchmark](reports/innovation-v2-benchmark-20260910.md)
finds 138 observation/PDU pairs for v2, 138 for prior innovations v1, 139 for the
full native progressive receiver, 86 for Dire Wolf, and 44 for the specified
gr-satellites profile. Added and missed exact PDUs, global deduplication and
shared-host time costs are reported separately; this is not a fresh holdout.

## Retained research results

- **A0: failed.** The public `satellite-recordings` matrix covered 92 WAVs;
  87 were mapped and run twice with gr-satellites 5.9.0, but only 78 were
  deterministic. A controlled diagnosis confirmed that nine recordings can
  produce different frame sets, not merely a different ordering. GNU Radio
  3.10.12 provides TPB only; requesting STS silently falls back to TPB.
  CRC vectors, SigMF validation, and GPS PRN acquisition pass.
- **A0.5: calibrated replay parity passed; blind validation remains pending.**
  The exact historical `gr-satnogs` reconstruction still returns zero frames,
  but a deterministic phase-first soft-list receiver now recovers all four
  SatNOGS reference payloads from observation 12511021 byte-for-byte: 200, 250,
  101, and 101 bytes. Every accepted body passes strict HDLC unstuffing, legal
  size, and full CRC-16/X.25; two runs are identical, and two off-burst
  negative windows produce zero CRC-valid frames in two repeats. A separate
  decode-only process with no reference-file access also yields 4/4 before the
  post-decode byte audit. Timing, event localization, and lag selection were
  calibrated using the references, so this is P-POS benchmark parity, not a
  claim of blind or archive-wide superiority. The dump rate is **57.6 kS/s**.
  On a second observation, the same front-end with separately calibrated timing
  recovers 2/4 reference payloads exactly (200 and 101 bytes). One additional
  CRC-valid but byte-wrong frame is rejected by the post-decode audit, which
  demonstrates why a 16-bit CRC alone is insufficient after a large list
  search.
- **A1: data complete.** 5,010 SatNOGS transmitter records were compared with
  675 SatYAML definitions; 498 were conservatively matched.
- **Blind multi-recording union improves yield.** Across three
  protocol-comparable positive IQ observations, the source-compatible baseline
  recovered 22 trusted frames, phase-first recovered 16, and their validated
  union recovered 24: two frames (+9.09%) beyond the baseline without losing
  baseline-only frames. Observation 4048 is reported separately as byte-exact
  raw PDU agreement and is excluded from trusted counts because no payload
  grammar was confirmed. Standalone phase-first is therefore complementary,
  not yet a replacement.
- **Channel-conditioned phase transfer result.** Without changing the frozen
  v1 report, a new complex-prefilter-before-phase branch plus the legacy phase
  branch recovered 10 trusted AX.25+CCSDS payloads on CANVAS 14366383 versus 6
  for the source-compatible baseline, with no baseline-only payloads. This is
  explicitly `transfer_holdout_not_preregistered`, not a general superiority
  claim; see `reports/polyitan-iq-comparison-v2.md`.
- **Archive campaign is complete for the exact G3RUH cohorts.** The supplied
  catalogue has 704 observations and 257 IQ captures. A protocol-neutral inventory routes 205
  captures to the current FSK-family frontend and 52 to missing plugins. The
  zero-reference campaign contains 208 IQ objects (17.89 GB); downloads,
  conversion, decoding, and strict post-decode audits use durable manifests and
  continue after individual failures. On the same-IQ exact cohorts, trusted
  yield is 0:0 for 28 zero-reference captures and 0:0 for 19 positive captures;
  every raw candidate failed strict AX.25 validation. All 49 positive IQ files
  were downloaded and verified (100.72 GB). A live S3 inventory found 33 newer
  IQ objects not present in the catalogue; all 33 were triaged and the newest
  three produced no trusted telemetry in the bounded probes.
- **RML24 was evaluated end to end.** Both 20 GB archives were downloaded and
  verified, the complete 1,323,000-record physical dataset was converted
  without executing Pickle, and every record was scored. The current receiver
  supports 252,000 BPSK/GMSK/OQPSK/QPSK rows and obtains an oracle-aided BER of
  0.463707, so it is not competitive on this broad benchmark. A preregistered
  diagnostic showed that the old +/-8-bit alignment bound hid some real signal
  (0.464851 to 0.379539 with the wider scorer), but an RRC 0.35 frontend did
  not improve an independent holdout. The remaining limitation is receiver
  synchronization/equalization, not report arithmetic or IQ conversion.
- **Carrier/timing v2 improves a frozen physical-layer holdout.** A bounded
  nth-power carrier estimator and fixed OQPSK boundary hypotheses reduce the
  +20 dB BPSK/QPSK/OQPSK holdout BER from 0.235669 to 0.149167. An independent
  audit reproduced every result row, the bootstrap interval, source hashes and
  truth-isolation checks. The versioned full replay reduces the historical
  +/-8 BER only from 0.463707 to 0.462666; that small aggregate is partly biased
  by the larger OQPSK hypothesis bank and is not claimed as independent proof.
  The default remains `legacy`; v2 is explicit opt-in.
- **A 21-class IQ-only routing baseline is now measured.** A frozen classical
  feature/ridge bank reaches 35.4% top-1 and 55.4% top-3 on different records
  from the same RML24 cells. A stricter group-disjoint transfer audit reaches
  28.2% top-1 and 49.6% top-3, versus balanced random baselines of 4.8% and
  14.3%. Both are explicitly post-development AMR diagnostics, not real-IQ,
  bit-recovery or packet-yield claims.
- **AFSK1200 is an independent native path.** The Bell-202 receiver uses FM
  discrimination, non-coherent tone energy, a fixed blind clock bank,
  NRZI/HDLC, CRC-16/X.25 and strict AX.25 validation. On 23 frozen same-IQ
  captures it tied the generic gr-satellites control at 0:0 trusted frames;
  all 69 null controls also remained zero. The result adds bounded streaming
  coverage but is parity, not superiority.

See the [phase-first parity note](docs/phase-first-parity.md),
[execution gates](docs/gates.md), the
[CAMRAS signal-state decision](docs/decisions/001-camras-doppler.md), and the
[latest research campaign](reports/research-campaign-v3.md), plus the
machine-readable reports in `reports/`.

## Retained research tooling

- canonical configuration hashes, reference CRC-16/X-25, metrics, and a
  fail-closed publication gate;
- resumable SQLite replay harness with worker/process-tree isolation and
  CPU/RSS/wall-time accounting;
- CAMRAS index parsing, guarded downloads, per-recording IQ analysis, and
  immutable raw-to-SigMF conversion;
- GET-only SatNOGS client with bounded concurrency, retries, response-size
  limits, and conditional caching;
- real golden-corpus adapters for gr-satellites and Dire Wolf;
- SatNOGS DB × SatYAML comparison with JSON, Parquet, and SVG outputs;
- SigMF checksum/schema validation and GPS L1 C/A acquisition;
- PostgreSQL scale schema plus frozen campaign and protocol configurations.
- bounded, immutable A2 ingest planning and a budgeted lazy A4 sweep planner;
- deterministic offline FSK clock recovery with synthetic rate/phase controls,
  protocol-minimum AX.25 filtering, and fixed-budget replay artifacts;
- a clipping-robust phase-first discriminator, soft timing bank, bounded
  low-reliability/diversity list decoder, and reference-separated CRC audit;
- a content-bound blind phase-FSK batch API with constant-memory candidate
  scanning, bounded SQLite scratch, atomic JSON output, source/config hashes,
  and a fail-closed distinction between uncorrected trusted frames and
  unauthenticated list-repaired candidates; scientific holdout and 4 GiB RSS
  qualification remain open gates;
- a network- and mission-neutral receiver API with pluggable waveform and
  protocol adapters; the built-in phase path covers binary FSK/GFSK/GMSK,
  strict AX.25 UI, AX.25 carrying CCSDS Space Packets, raw CCSDS only with an
  external integrity policy, CCSDS TM Transfer Frames with FECF or external
  integrity, and caller-validated fixed-sync frames;
- an independent, bounded-memory Bell-202 AFSK1200/AX.25 receiver with
  deterministic null controls;
- a versioned, resumable CI16-LE file API for binary FSK/GFSK/GMSK with
  explicit sample/symbol rates, optional G3RUH, strict AX.25 validation,
  origin-preserving baseline union and atomic checkpoints; its packaged DSP
  exactly reproduced a frozen research frame on real CELESTA IQ, and its full
  4 GiB qualification completed at 123,985,920-byte peak RSS with zero swap;
- an opt-in RML24 carrier/timing v2 path and an IQ-only 21-class AMR research
  baseline with record-level and group-disjoint audits;
- a high-recall candidate-selector contract with padding, merging, top-k
  rescue, and deterministic audits of rejected windows;
- bounded external-decoder adapters plus a concrete gr-satellites/KISS backend
  whose byte output remains untrusted until downstream protocol validation;
- an event-aware candidate ledger that forms a deterministic validated union,
  preserves every recovery path, and reports baseline/non-baseline yield;
- resumable full-bucket inventory, download, CI16-to-CF32 conversion, blind
  G3RUH processing, and protocol-audit tooling;
- a project knowledge graph linking implementation, evidence, gates, and
  governance decisions.
- a TLE-versioned monthly observation planner with SGP4 visibility windows,
  optional link/weather/history features, global MILP conflict resolution,
  seeded Monte Carlo yield forecasts, rolling replan and audited dry-run
  SatNOGS scheduling export.

## Retained Python research environment and tests

This section is for the retained research tools, not the native receiver.
For current receiver installation and tests, use the [Rust guide](docs/rust-receiver.md).
The historical tools require Python 3.12 or newer. Install their analysis and test stack:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[analysis,test]'
.venv/bin/python -m pytest -q
```

For the month-scale planner without the wider analysis stack, install
`.[planning,test]`. Its architecture and API example are in
[`docs/observation-planning.md`](docs/observation-planning.md).
The expanded 50-satellite cohort, real weather/space-weather enrichment,
external-satellite/station validation, and active 31-day shadow campaign are
documented in
[`docs/observation-planning-v4.md`](docs/observation-planning-v4.md).

The validated VM also has GNU Radio, gr-satellites, Dire Wolf/`atest`, SoX,
SatDump, and the PostgreSQL client. Exact versions and capacity are recorded in
`reports/environment.json`.

The current exact test count is reported by the latest milestone artifact;
four environment-dependent tests are expected to be skipped on this VM.

## Legacy Python research CLI examples

These reproduce historical experiments. For native decoding without Python,
use the [Rust CLI examples](docs/rust-receiver.md).

```bash
.venv/bin/python -m telemetry_yield.cli env
.venv/bin/python -m telemetry_yield.cli capabilities \
  --output reports/receiver-capabilities.json
.venv/bin/python -m telemetry_yield.cli inventory-archive catalogue.json \
  --output reports/archive-inventory.json
.venv/bin/python -m telemetry_yield.cli scrape-camras index.html \
  --output reports/camras-index.json
.venv/bin/python -m telemetry_yield.cli convert-camras input.raw work/output \
  --sample-rate 48000 --sample-rate-basis "validated for this observation" \
  --doppler-state pre_correction \
  --frequency 436650000 --start-utc 2022-01-14T10:20:46Z \
  --observation-id 5293127 --confirm-ci16-le
.venv/bin/telemetry-yield decode-fsk-ax25 capture.raw \
  --output capture.result.json --checkpoint capture.checkpoint.json \
  --sample-rate 48000 --baudrate 2400 --decimation 8 --no-g3ruh \
  --expected-size-bytes SIZE --expected-sha256 SHA256
.venv/bin/telemetry-yield decode-blind-phase-fsk capture.raw \
  --output capture.blind-fsk.result.json \
  --sample-rate 57600 --baudrate 9600 \
  --expected-size-bytes SIZE --expected-sha256 SHA256 \
  --scratch-directory /scratch/telemetry-yield
```

The converter has no default CAMRAS sample rate or Doppler state. Both must be
supported per recording; unresolved values remain `unknown` and block claims
that depend on them.
## Optional CPU/CUDA compute qualification

The Rust receiver now has selectable CPU and optional CUDA FIR backends,
backend-bound checkpoints, graceful progressive cancellation and strict
numerical/task/frame parity audits. CUDA is not the only execution mode.
See [scope, commands and remaining release gates](docs/enterprise-compute-and-benchmark-v1.md).
This is a qualification candidate, not a claim of full GPU or enterprise readiness.
