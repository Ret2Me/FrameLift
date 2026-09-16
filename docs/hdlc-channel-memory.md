# Causal HDLC channel memory

`decode-recovery-hdlc-memory --input capture.cf32 --profile pass.json --output new-run`
adds cross-window BCJR and optional signal-only bootstrap to the existing
variable-length AX.25 UI IQ receiver. It is experimental and disabled unless this
command/API is selected. It is not a replacement for the original receiver.

## Data flow and trust boundary

1. Decode the current window with the unchanged local receiver (including its
   configured sequence/CPM lanes).
2. Run the frontend again and try compatible channel models learned from earlier
   **baseline-valid** windows. This duplicated frontend cost is included in the
   memory work counter; this first implementation is not a speed optimization.
3. Optionally fit a three-tap channel from inferred symbols, with disjoint fitting
   and waveform-validation blocks. Neither payload references nor CRC outcomes
   enter this estimator. Exclude both blocks, with guards, from frame acceptance.
4. Union frames passing received X.25 FCS, independent residue and AX.25 UI
   structure validation. Supplemental successes never populate channel memory.

The session owns a fixed source SHA-256, sample rate and receiver profile. Models
cannot be imported or deserialized into its private state. A model's entire source
window must precede the target window; overlapping windows do not transfer models.
Age is measured in source samples since the end of the training window. Capacity
uses deterministic oldest-first eviction and reports its count. A timing-bank
index is not assumed stable between windows: all retained models with a matching
frontend lane and branch count are attempted within the explicit work budget.

QPSK/OQPSK branches retain separate channel models. Cross-window channel stability
is a hypothesis, not a guarantee. Blind fits use inferred symbols and can fit
noise; a fit alone is never evidence of received telemetry.

## Plan

Use the same `receiver` object as `decode-recovery-hdlc`, with `sequence` enabled.
The top-level fields are:

```json
{
  "format": "cf32_le",
  "sample_rate_hz": 57600,
  "receiver": "replace with the HDLC receiver object",
  "memory": {
    "maximum_age_samples": 3456000,
    "maximum_models": 8,
    "blind_bootstrap": true,
    "work_budget": 2000000000
  },
  "windows": [
    {"start_sample": 0, "sample_count": 576000},
    {"start_sample": 576000, "sample_count": 576000}
  ]
}
```

This is a shape illustration, not an executable profile: replace the `receiver`
placeholder with a verified waveform/line-coding/scrambler configuration. Windows
must have strictly increasing starts; each must meet the local IQ receiver bounds.
The maximum is 4096 windows, 32 retained models and a separately bounded amount of
memory work per window. Budget exhaustion fails the window without updating state
or pretending the search completed. A failed file run has no complete summary;
intermediate reports are diagnostic only. Resume is deliberately not offered.

## Evidence

`window-NNNN.json` keeps the full local report in window-relative coordinates and
records the union in absolute source coordinates. Every memory attempt identifies
the source window/stream or the blind fitting/validation exclusions. The final
summary compares unique full received frames, including their received FCS, with
the union of local results over the **same windows**. It must not be compared to a
different decoder's payload-only representation without canonicalization.

The tests exercise source isolation, causality, expiry, overlap rejection, failed
window transactions, deterministic worker parity, baseline preservation, a
controlled ISI decoding case and blind-fit exclusion. A supplied-channel unit test
is not evidence of end-to-end learned transfer gain. Real-recording improvement
requires a separate held-out comparison; no percentage gain is promised here.
