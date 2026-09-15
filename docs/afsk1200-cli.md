# Built-in AFSK1200/AX.25 file receiver

`telemetry-yield decode-afsk1200` is the deployment entry point for the generic
Bell-202 plugin. It is separate from every RML benchmark and contains no
satellite, NORAD, or SatNOGS-specific routing rule.

The Python and CLI input contract is pinned as
`telemetry-yield-afsk1200-file-api-v1`; the result document is pinned as
`afsk1200-file-result-v1`. Pass `--api-version` when an orchestrator needs an
explicit compatibility assertion. Unknown versions fail before input hashing or
DSP begins.

## Basic run

```bash
telemetry-yield decode-afsk1200 recording.iq \
  --output reports/recording-afsk1200.json \
  --expected-size-bytes 83980024 \
  --expected-sha256 dcea8f25db885eb7b63dd16baee7679a9dfa6982ebba235d11c540e05a6c26fa
```

The input contract is little-endian interleaved signed 16-bit I,Q. Defaults are
57,600 complex samples/s, 1,200 baud, 1,200/2,200 Hz Bell-202 tones, and a
9,600-sample/s discriminator-audio stream. Override those values only when the
capture contract says otherwise; invalid rates, tones, segment bounds, hashes,
and window geometry fail before demodulation.

The command writes JSON atomically. Its `route` identifies `bell202_afsk` and
the plain-NRZI `ax25_plain` adapter. `effective_config`, `config_sha256`, source
size/SHA-256, bounded-memory counters, and the complete candidate ledger make
the attempt reproducible.

## Bounded memory and segments

Only one overlapping complex64 window is materialized at a time. The default
maximum is 280 × 4,096 complex samples (about 9.2 MiB of complex64 IQ), not the
size of the recording. To process one exact upstream-selected interval:

```bash
telemetry-yield decode-afsk1200 recording.iq \
  --output reports/event-7.json \
  --segment-start-sample 12000000 \
  --segment-sample-count 2304000 \
  --event-key event-7
```

The segment must contain at least 16,384 complex samples and lie wholly inside
the file. The stable resource counters report total/completed windows,
cumulative samples materialized, and the maximum single-window allocation.

## Interruption and resume

A checkpoint is written after every completed window. By default it is the
output name plus `.checkpoint.json`; use `--checkpoint PATH` to choose another
location. Re-running the same command resumes only when source, configuration,
plugin version, and optional baseline fingerprints match exactly. A malformed
or mismatched checkpoint is rejected rather than silently reused.

`--max-windows N` intentionally stops after at most N pending windows and emits
a `partial` result. Re-run without that limit to finish. `--no-resume` starts a
new checkpoint. A process interruption between checkpoints loses at most the
current window.

## Candidate classes and acceptance

The JSON keeps three namespaces:

- `candidates.raw`: unique CRC-valid HDLC frame material before AX.25 structure
  acceptance;
- `candidates.rejected`: CRC-valid frames rejected by strict AX.25 UI parsing;
- `candidates.trusted`: complete HDLC, CRC-16/X.25-valid, structurally valid
  AX.25 UI frames.

Only `trusted` entries enter `candidate_ledger`. Exact-length CCSDS parsing of
an AX.25 information field is metadata and never substitutes for the outer
AX.25 integrity path. Raw and rejected candidates are diagnostics, not
telemetry.

## Union with an external baseline

Use `--external-baseline baseline.json` to union independently validated frames
with the native branch. The file must be at most 64 MiB and use:

```json
{
  "schema_version": "candidate-ledger-origins-v1",
  "detections": [
    {
      "capture_sha256": "<same IQ SHA-256>",
      "segment_start_sample": 0,
      "segment_sample_count": 1000000,
      "branch_id": "external-baseline",
      "source_role": "baseline",
      "plugin_id": "decoder-name",
      "plugin_version": "version",
      "protocol_id": "ax25",
      "original_frame_hex": "<hex>",
      "normalized_payload_hex": "<AX.25 PDU without FCS hex>",
      "validation_layers": ["independent_validation_name"],
      "validated": true,
      "hypothesis_fingerprint": "<stable identifier>",
      "config_fingerprint": "<stable identifier>",
      "event_key": "capture-segment",
      "event_time_seconds": 0.0,
      "provenance": {}
    }
  ]
}
```

Every baseline detection must match the IQ SHA-256, `event_key`, and `ax25`
protocol and must explicitly name a validation layer. Invalid or unvalidated
records reject the complete run. The ledger deduplicates normalized content
within the event while retaining every independent native/baseline origin.
