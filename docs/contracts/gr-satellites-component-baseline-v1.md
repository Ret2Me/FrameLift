# Official gr-satellites component baseline v1

## Scientific role

`gr-satellites-component-baseline-v1` is a generic, source-compatible AX.25
baseline for same-byte receiver comparisons. It sends one headerless CI16-LE
capture through GNU Radio's `interleaved_short_to_complex`, the official
gr-satellites 5.9.0 `fsk_demodulator`, and the official 5.9.0
`ax25_deframer`. The frozen finite bank is 1200, 4800, 9600, and 19200 baud,
each with plain and G3RUH line coding, IQ input, 5000 Hz deviation, DC blocking,
and the official clock defaults.

This is an **official-component baseline**, not a claim to reproduce the
historical SatNOGS platform. It also is not a mission SatYAML flowgraph. A
mission-profile gr-satellites run remains a separate operational secondary
baseline. These distinctions are constants in every result's `baseline` and
`claim_guard` objects.

## Input and output contract

The caller supplies the exact input size and SHA-256. The supervisor accepts
only a nonempty, complete-pair, regular non-symlink CI16 file. It checks
device/inode/size/timestamps and hashes before and after the child flowgraph.
It also verifies that the runner and frozen component runtime did not change.
The result is atomically published with hard-link no-clobber semantics and a
directory fsync. Existing or concurrently created output is never overwritten.

The formal result schema is
`schemas/gr-satellites-component-baseline-v1.schema.json`. Every official
deframer PDU is emitted as exact FCS-free hexadecimal bytes, SHA-256, baudrate,
and G3RUH provenance. The official deframer verifies FCS and removes it. The
runner separately applies the project's strict AX.25 UI structure policy;
CRC-valid but structurally non-AX.25-UI PDUs remain recorded candidates and
are not counted as trusted AX.25 UI.

## Frozen runtime closure

`runtime.files` records path, size, and SHA-256 for the official demodulator,
deframer, HDLC, RMS-AGC and options sources; gr-satellites, GNU Radio and PMT
Python extension objects; `libgnuradio-satellites`; the gr-satellites,
GNU Radio core, PMT, NumPy and CPython conda package records; and the golden
Python executable. The conda records carry the distribution file inventory and
expected hashes, while the directly executed sources and extension objects are
also hashed in place. The parent re-creates this manifest after the child exits
and fails on drift.

## Resource and failure semantics

The child runs in a new process group with a hard CPU-time limit, an 8 GiB
address-space limit, an output-file-size limit, a wall-clock timeout, and a
2 GiB resident-memory watchdog sampled every 50 ms. Frame count, stored PDU
bytes, and serialized child output are independently bounded. The child writes
its JSON on a private result descriptor, so GNU Radio diagnostic text on stdout
cannot be mistaken for a result. Captured stdout and stderr sizes and hashes,
observed peak RSS, limits, return code, executable hash, runner hash and argv
are recorded.

If PDU storage limits are reached, the runner publishes
`status=output_limit_exceeded` and exits 2; that partial result is not a valid
completed comparator outcome. Timeout, memory, content, runtime, child, schema,
or publication errors exit nonzero without a new final result.

## Standalone invocation

```bash
.venv/bin/python -B work/gr_satellites_component_baseline.py \
  --input /data/observation.ci16 \
  --output /results/observation.grsat-component.json \
  --sample-rate-hz 57600 \
  --expected-size-bytes 121479168 \
  --expected-sha256 HEX_DIGEST \
  --golden-root work/golden/env \
  --timeout-seconds 3600 \
  --cpu-seconds 3600 \
  --maximum-rss-mib 2048 \
  --maximum-address-space-mib 8192
```

For a frozen holdout, a campaign supervisor must map each preregistered
observation to exactly one immutable input identity and one fresh output path.
The supervisor may skip an existing output only after validating the v1 schema,
attempt fingerprint, input identity, configuration hash, runtime closure and
recorded output-file SHA-256. It must not adapt the hypothesis bank after
observing outcomes.

