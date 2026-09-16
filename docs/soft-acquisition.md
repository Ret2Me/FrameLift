# Additive soft marker acquisition

The advanced IQ receiver normally finds a fixed syncword by hard slicing with
at most two bit disagreements. `recovery.soft_acquisition` adds an opt-in
signal-only search for markers that miss that gate. It is disabled by default;
existing profiles and reports omit the field when it is `null` or absent.

```json
{
  "workers": 4,
  "coherent_cpm": false,
  "tracking": null,
  "soft_acquisition": {
    "minimum_correlation": 0.70,
    "maximum_hard_errors": 12,
    "maximum_candidates": 32,
    "maximum_work": 10000000
  }
}
```

This is an example `recovery` object for an engineering profile, not a verified
mission setting. Freeze thresholds before evaluating a held-out cohort.

## Signal-only gate

For received marker samples `y` and bipolar marker `s`, the gate computes
centered correlation `dot(y-mean(y), s-mean(s))` divided by the product of their
energies' square roots. The real-valued frontend requires positive correlation
because it already enumerates the polarity/quadrature hypotheses. The legacy
rectangular BPSK frontend uses complex correlation magnitude because a constant
phase ambiguity remains; its existing pilot fit resolves that phase.

A bounded Hamming-distance prefilter avoids evaluating obviously unrelated
windows. Constant/silent markers have no usable centered energy and are
rejected. The existing pilot/channel, carrier, complete-frame and repeat-header
checks still apply. The marker is used only where physically received; no
payload bytes, received CRC, FEC success or expected frame selects a marker.

The search covers the same carrier/timing/waveform bank as the existing path:
legacy BPSK, BPSK/QPSK/OQPSK, FSK/GFSK/GMSK and IQ-demodulated AFSK. It does not
turn OGG audio into coherent IQ or infer an unknown on-air protocol.

## Additive guarantees and limits

Hard candidates are selected first with their unchanged ranking and duplicate
rules. Additional candidates have a separate limit and cannot displace them.
Soft-only candidates are decoded using the configured downstream mechanisms,
but do not seed cancellation or repetition combining: they cannot alter later
baseline residuals or the baseline's training/grouping decisions. They may
recover the same physical burst using an alternative timing hypothesis; those
alternatives are never counted as independent repeat evidence.

`baseline_frames` retains hard-acquired first-pass frames. Supplementary frame
provenance is prefixed `soft_acquisition/`; the receiver still deduplicates the
complete validated frame. Soft-marker receipts record scan work, gate passes,
selected marker scores and physical sample/carrier coordinates.

Limits are correlation in `[0.5,1]`, no more than one third of marker bits
disagreeing in the broad prefilter, 1–128 supplementary selected candidates,
and 1–200 million search work units per acquisition round. The deterministic
proxy charges one unit per attempted window and 64 per evaluated marker symbol.
There is also a raw-candidate cap of 256 times the selected-candidate limit.
Search work is added to the receiver's aggregate budget; subsequent decoding
is charged normally. Each round's search limit is capped to the remaining
aggregate budget before scanning. Exhaustion returns an explicit error, never a silently
truncated successful result. Comparing such an attempt as zero-yield success
would be incorrect; benchmark accounting must retain it as attrition.

Normalized correlation is not a calibrated false-alarm probability. Wider
acquisition increases both decoder work and the number of integrity checks.
Noise, wrong-checksum and out-of-profile controls remain necessary. Unit tests
and constructed damaged-marker recovery do not establish a gain on orbital
recordings or novelty over established soft synchronization techniques.

## Reproducible checks

The unit suite uses independently generated waveforms with six fixed syncword
bit inversions. It checks BPSK/QPSK/OQPSK with rectangular and RRC pulses,
FSK/GFSK/GMSK, IQ-demodulated AFSK, and the legacy BPSK frontend. The hard gate
rejects these markers; the soft gate recovers the original validated payload.
Matched wrong-CRC and noise controls, serial/parallel report identity, baseline
SIC/repetition preservation, and explicit resource-limit failures are separate
checks. These are engineering fixtures, not measured on-air improvements.

`examples/acquisition_benchmark.rs` replays an existing, unmodified output from
`recovery_pipeline_benchmark --stress`. It does not regenerate signals or
introduce damaged markers. The fixed grid contains 108 positive recordings and
216 matched wrong-CRC/noise controls. It records the frozen manifest, profiles,
input hashes and executable identity, then compares the current hard receiver
with serial and four-worker soft acquisition using the settings above. It also
checks hard-receiver reports against the saved pre-change reports and retains
every failure, error, added frame and lost frame.

```sh
cargo build --release --example acquisition_benchmark
target/release/examples/acquisition_benchmark \
  --dataset /path/to/frozen-stress-output \
  --output /path/to/new-empty-result-directory
```

Use a frozen copy of the executable when other builds may run concurrently;
the benchmark rejects executable or input mutation. The original stress cohort
and the new damaged-marker unit fixtures must be reported separately.
