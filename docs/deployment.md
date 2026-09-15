# Deployment guide

## Supported production boundary

The releaseable built-in paths are batch receivers for CI16-LE files carrying
either Bell-202 AFSK1200 or binary FSK/GFSK/GMSK followed by AX.25 UI framing.
Both perform bounded-window DSP, HDLC/NRZI deframing, CRC-16/X.25 and strict
AX.25 structure validation. The FSK-family route optionally applies the G3RUH
`x^17+x^12+1` descrambler. The generic receiver can route other
waveform/protocol plugins, but an installed plugin is not production-qualified
merely because the interface accepts it.

Benchmark truth, expected payloads, satellite IDs and reference frame files are
never inputs to the production path. External decoders remain untrusted
subprocess backends; their bytes require the same downstream validation.

## Installation

Use Python 3.12 and the locked dependency set:

```bash
python3.12 -m venv .venv
.venv/bin/pip install uv
.venv/bin/uv sync --frozen --extra analysis
```

Run the release test artifact before promotion. The exact source/test inventory
hash and all subprocess outputs are recorded in `reports/release-tests-v1.json`
or its later version.

## One content-bound attempt

Resolve and record the input size and SHA-256 outside the worker, then run:

```bash
.venv/bin/telemetry-yield decode-afsk1200 /data/capture.iq \
  --api-version telemetry-yield-afsk1200-file-api-v1 \
  --output /results/capture.result.json \
  --checkpoint /state/capture.checkpoint.json \
  --expected-size-bytes 4294967296 \
  --expected-sha256 HEX_DIGEST \
  --event-key capture-id
```

The IQ contract is interleaved little-endian signed int16 I,Q. Odd byte counts,
incomplete complex samples, invalid segment bounds, unknown API/profile IDs,
input hash drift, stale checkpoints and malformed external ledgers fail closed.
Output and checkpoint replacement is atomic.

The result identifies the API/result/checkpoint versions, exact source and
configuration hashes, route/plugin versions, bounded-memory counters, raw CRC
candidates, structurally rejected candidates, trusted frames and the
deduplicated union ledger. Its formal top-level contract is
`schemas/afsk1200-file-result-v1.schema.json`.

For the FSK-family route, the sample rate, baud rate, integer decimation and
G3RUH state are mandatory rather than guessed from a satellite name:

```bash
.venv/bin/telemetry-yield decode-fsk-ax25 /data/capture.iq \
  --api-version telemetry-yield-fsk-ax25-file-api-v1 \
  --output /results/capture.fsk.result.json \
  --checkpoint /state/capture.fsk.checkpoint.json \
  --sample-rate 48000 --baudrate 2400 --decimation 8 --no-g3ruh \
  --expected-size-bytes 137256356 \
  --expected-sha256 HEX_DIGEST \
  --event-key capture-id
```

Its formal output contract is
`schemas/fsk-ax25-file-result-v1.schema.json`. Both file APIs accept the same
validated external-baseline ledger contract and return an origin-preserving
union.

### Blind clipping-robust candidate route

The phase-first research receiver now also has a content-bound batch interface:

```bash
.venv/bin/telemetry-yield decode-blind-phase-fsk /data/capture.iq \
  --api-version telemetry-yield-blind-phase-fsk-file-api-v2 \
  --output /results/capture.blind-fsk.result.json \
  --sample-rate 57600 --baudrate 9600 \
  --expected-size-bytes 121479168 \
  --expected-sha256 HEX_DIGEST \
  --scratch-directory /scratch/telemetry-yield \
  --maximum-scratch-mib 512
```

Unlike the older in-memory research selector, this interface streams one
analysis window at a time and keeps exact selection statistics in bounded
temporary SQLite storage. It rejects mutable identity drift, symlinks,
non-regular or malformed CI16 input, unknown API versions, and configurations
outside hard production resource bounds. The final result is atomic and embeds
the effective configuration plus source-module hashes. Publication is
no-clobber. On retry, an existing completed result is integrity-checked and the
entire deterministic selection/decode is rerun; the existing file is reused
only when the newly reconstructed canonical result is exactly equal. A
self-hashed but semantically forged, mismatched, or corrupt result blocks the
retry. Its contract and full
trust semantics are in
`docs/contracts/blind-phase-fsk-file-api-v2.md`; its output schema is
`schemas/blind-phase-fsk-file-result-v2.schema.json`.

This makes the interface deployable, not scientifically qualified. It has not
yet passed the required 4 GiB/512 MiB measured run or a preregistered blind
holdout. Until those gates pass, deploy it only as a shadow/candidate path and
never promote list-repaired frames to trusted telemetry. A repaired CRC-valid
frame remains unauthenticated even when correlated frontend variants agree.

The release resource gate must use the exact content-frozen confirmatory
candidate, not a smaller convenience configuration:

```bash
.venv/bin/python work/qualify_blind_phase_fsk_4gib.py \
  --work-dir /scratch/blind-fsk-4gib-fresh \
  --report /results/blind-fsk-4gib-qualification-fresh.json \
  --candidate-config /frozen/candidate-config.json \
  --expected-candidate-config-size-bytes EXACT_SIZE \
  --expected-candidate-config-sha256 EXACT_SHA256
```

Both destinations must be fresh. The qualifier rejects configuration drift,
runs all four frozen rates over the same deterministic nonzero 4 GiB CI16
control, and binds the candidate, input, result schema, qualifier and receiver
implementation closures before and after execution. It is resource evidence
only; a PASS does not establish telemetry yield or scientific superiority.

### Generic official-component comparison baseline

For same-byte AX.25 comparisons that do not have a mission SatYAML profile,
use `work/gr_satellites_component_baseline.py`. It runs the frozen official
gr-satellites 5.9.0 FSK demodulator and AX.25 deframer components at
1200/4800/9600/19200 baud in plain and G3RUH modes. The input size/SHA,
component closure, golden runtime, runner, limits and exact FCS-free PDU bytes
are bound into a strict JSON result. See
`docs/contracts/gr-satellites-component-baseline-v1.md` and
`schemas/gr-satellites-component-baseline-v1.schema.json`.

This is an official-component baseline, not an identical reconstruction of a
historical SatNOGS deployment and not a mission-profile gr-satellites
flowgraph. Keep mission SatYAML results as a separate secondary baseline.

## Resume, retries and idempotence

The AFSK/FSK checkpoint commits each terminal window under an attempt fingerprint. A
retry with the same input, configuration and baseline skips completed windows;
a retry with any different content is rejected. A completed rerun produces the
same trusted normalized-payload set and does not duplicate ledger entries.

The blind API resumes at immutable-observation granularity: it verifies the
completed result binding, reruns deterministic DSP, and requires exact equality
with the reconstructed result. It does not checkpoint within an observation.
The campaign supervisor may additionally bind raw output to a separately
protected execution receipt. The generic component baseline uses fresh no-clobber result paths;
its campaign supervisor owns verified completed-result skipping.

Workers should retry only process interruption or explicitly classified
transient external-backend errors. They should not retry malformed data,
contract mismatches, candidate-limit exhaustion or validation rejection.

## Resource envelope

Each built-in file path materializes one bounded overlapping window at a time.
The AFSK 4 GiB sparse CI16-LE qualification completed all 1,872 windows with a
146,493,440-byte peak RSS, no swap and no candidates. The separately frozen FSK
qualification completed all 1,789 windows in 474.824 seconds with a
123,985,920-byte peak RSS, no swap, no candidates and no execution failures;
its result validates against the versioned output schema. The FSK run used a
1,200,000-complex-sample window (19,200,000 bytes after conversion to the
internal complex128 representation), which is larger than the default
240,000-sample window. Evidence and the earlier fail-closed contract correction
are preserved in `reports/fsk-4gib-streaming-v2.json` and its frozen plan.

Configure a worker limit of at least 512 MiB RSS plus interpreter/runtime
overhead and a wall-time limit appropriate to CPU speed. These are measured
batch-file envelopes, not a live-SDR latency guarantee. Do not infer a safe
limit for an external decoder from the built-in result; each backend must have
its own process-tree timeout, RSS/CPU limit and output-size bound.

The current packaged FSK implementation also has a frozen real-IQ equivalence
check. On one disclosed known-positive CELESTA window it reproduced the exact
raw CRC-valid and strict AX.25 frame sets of the research implementation in two
byte-identical runs. This is an implementation-parity qualification, not a new
scientific performance trial; see
`reports/fsk-packaged-real-iq-parity-v2.json`.

The blind clipping-robust API has a static memory envelope: one analysis array,
one decoder window, a 4 MiB SQLite cache, and bounded candidate/result lists.
Its preflight rejects a conservative per-window working-set model above 512
MiB and records that model in the result. The model includes a fixed runtime
reserve and the multiplicative syndrome-search state; it is an admission
ceiling, not a measured RSS qualification or a replacement for cgroup limits.
Scratch storage grows with the number of analysis windows and is guarded by a
preflight estimate and runtime checks. Run a measured 4 GiB qualification before
setting its production RSS limit; the 512 MiB recommendation above is evidence
for the older AFSK/FSK paths and must not be transferred to this route without
measurement.

## Operations and observability

Persist the result JSON, checkpoint, worker exit code, wall time, peak RSS,
application version, source hash and configuration hash. Alert on:

- nonzero exit or timeout;
- source/checkpoint/config mismatch;
- candidate-limit exhaustion;
- any external-backend process-tree cleanup failure;
- a sudden rise in CRC-valid but structurally rejected frames;
- output-schema or API-version mismatch.
- blind-selector scratch-budget rejection or input identity drift.

Trusted-frame count alone is not a health signal: a valid no-signal recording
may correctly produce zero.

For the blind phase route, a valid, nonempty, exactly all-zero CI16 file is an
explicit terminal degenerate result: it completes with `degenerate_input=true`
and zero selected/decoded windows or detections, without entering DSP. Keep that
exposure separate from nonzero waveform-destroying controls in monitoring and
scientific tables.

## Rollout and rollback

Canary a new plugin/config version on a fixed replay set containing positives,
zeros, seeded waveform-destroying controls and malformed inputs. Promote only
when output schemas validate, prior trusted frames are preserved for a union
release, nulls remain clean, and deterministic replays match. Roll back by
restoring the prior package/config hashes and starting a new attempt; never
resume a checkpoint under a different implementation or configuration.

## Known limits

This release is a generic plugin host, not a claim to decode every modulation
or protocol automatically. The built-in qualified routes are AFSK1200/AX.25
and binary FSK/GFSK/GMSK with plain or G3RUH AX.25. CCSDS and external QPSK
evidence have separate claim boundaries; the generic protocol host can
validate them, but they are not silently inferred from an AX.25 route. Live
SDR/stdin ingestion and the proposed AI candidate selector are not part of
this batch deployment artifact. The blind phase route currently validates only
AX.25 UI after CRC-16/X.25; its generic architecture does not mean that raw
CCSDS is automatically recognized by this particular command.
