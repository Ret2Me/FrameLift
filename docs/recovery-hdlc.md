# Variable-length HDLC recovery from IQ

`decode-recovery-hdlc` is an opt-in development receiver for AX.25 UI frames of
18–1023 bytes, including their received FCS. It does not require a fixed payload
length, an artificial syncword, or an added repeat identifier. Existing generic,
audio and fixed-length advanced-IQ receivers are unchanged.

The line-code profile explicitly selects `nrzi` or `direct`, followed by either
`none` or `g3ruh` descrambling. G3RUH uses received input bits at delays 12 and 17.
The first 17 decoded bits of a G3RUH stream are not treated as flag evidence:
the recording may begin with unknown scrambler history. The ordering agrees with the
[gr-satellites AX.25 deframer](https://github.com/daniestevez/gr-satellites/blob/main/python/components/deframers/ax25_deframer.py).
Unlike that deframer's PDU interface, this receiver retains the received FCS.
It requires both complete flags, valid stuffing, octet alignment, two independent
CRC checks and an AX.25 UI address/control structure. It does not pad truncated
frames, regenerate an FCS, or accept a frame just because it has a plausible header.
The two CRC implementations check the same 16-bit constraint; they do not
provide 32 independent check bits or square the false-acceptance probability.

## Signal path

The existing IQ frontend generates the baseline soft-symbol streams. FSK/GFSK/GMSK
use `phase_fsk` or `channel_conditioned_phase_fsk`; Bell-202 within FM uses
`bell202_afsk`; coherent PSK uses `iq_bpsk`, `iq_qpsk` and `iq_oqpsk`. The method
is additive:

1. Decode and retain all ordinary hard-sliced, CRC-valid frames.
2. Select the earliest validated physical occurrences, up to a declared limit.
3. Fit a centered three-tap channel to their guarded interiors. Twenty physical
   symbols are excluded at each training boundary. Quadrature branches are
   fitted and detected separately, not treated as one scalar ISI stream.
4. Run the same complete-observation log-MAP/BCJR detector used by the advanced
   IQ recovery code. Exterior bits are marginalized; endpoint observations are
   retained. There are no payload-bit priors and no CRC-directed bit flips.
5. Admit only independently CRC-valid frames outside every training span and
   its guard, including the target's flags and line-code history. Union these
   with the untouched baseline.

This is **cross-frame channel transfer**, not FEC for AX.25, not coherent CPM,
and not a proof of universal gain. It needs at least one correctly received
anchor with sufficient symbol diversity. It does not rescue an otherwise empty
window by using a packet from an archive. Time-varying channels may invalidate
the transferred model; the baseline stays available. CRC-16 is an integrity
check, not authentication or an absolute guarantee against false acceptance.

A separate optional [coherent CPM lane](hdlc-cpm.md) now processes the original
IQ using repeated HDLC flags for acquisition. It does not replace the baseline
or this anchor-trained lane; omission of its option preserves existing behavior.

## Explicit run and ablation

```sh
telemetry-yield-rs decode-recovery-hdlc \
  --input recording.ci16 --profile hdlc-plan.json --output hdlc-results
```

An illustrative FSK-family profile follows. It is not a certified satellite
definition; the recording format, rate, modulation and line coding must be
independently established for each mission.

```json
{
  "format": "ci16_le",
  "sample_rate_hz": 57600,
  "start_sample": 0,
  "sample_count": 262144,
  "receiver": {
    "waveform": {
      "hypothesis_id": "fsk9600-nrzi-g3ruh",
      "demodulator_id": "phase_fsk",
      "dsp": {
        "baud": 9600,
        "mode": "fsk",
        "rate_errors_ppm": [-100, 0, 100],
        "phase_bins": 8,
        "top_timing": 8,
        "bank": "full"
      },
      "decimation": 1,
      "cutoff_hz": 7200,
      "carrier_hz": null,
      "mark_hz": 1200,
      "space_hz": 2200,
      "psk": null
    },
    "line_coding": "nrzi",
    "scrambler": "g3ruh",
    "sequence": {"maximum_training_frames": 8},
    "workers": 4,
    "work_budget": 2000000000
  }
}
```

Set `sequence` to `null` for the hard-slice ablation. Change only `workers` from
1 to 4 for a deterministic parallelism comparison. The entire report, including
frame bytes, ordering, work accounting and channel receipts, must match between
worker counts. Keep source windows and frontend profiles identical between
arms. Worker parallelism covers per-stream framing/fitting/BCJR; it does not
parallelize every frontend operation.

## Evidence and operational bounds

Reports identify the source hash, exact decoded f64 IQ hash, runtime and compute
backend. The held source/runtime descriptors are checked before output commits.
Frame provenance includes the frontend stream, physical serialized-symbol
interval and conservative source-window bounds. It does **not** claim an exact
sample timestamp for adaptive PSK clocks.

Input is coherent `ci16_le`, `cf32_le` or `cf64_le`, not OGG or reconstructed IQ.
The unchanged FSK frontend consumes approximately 512 symbols for DC-blocker
warm-up and 32 additional timing guards. Windows must retain this leading
context; boundary-truncated transmissions are not silently padded.
Windows contain 8192–1048576 samples; Bell-202 requires at least 16384. The bank
is limited to 4096 streams, 16777216 total soft-symbol visits, 1–64 workers and
an explicit deterministic work budget. Sequence fitting failures are recorded
without deleting baseline frames. Resource/admission failures abort the run;
they are not silent empty decodes.

The checked-in tests exercise independent transmit FCS generation, variable
lengths and stuffing, line-code combinations, FSK/PSK IQ round trips,
bad-FCS/truncation/noise controls, a controlled disjoint-anchor recovery and
serial/parallel identity. These are development checks, not a multi-mission
held-out field benchmark or a publication-readiness certificate.
