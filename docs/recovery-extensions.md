# Optional IQ recovery extensions

These extensions are development features, not qualification of a satellite
mission or a replacement for the historical paper's frozen receiver. All
accepted packets require their received CRC/FECF. No reference payloads enter
acquisition, channel fitting, or decoding.

## What is integrated

| Area | Implementation | Boundary |
|---|---|---|
| Coding | LDPC, uncoded fixed frames, K7 log-MAP, RS, RS → optional randomizer → K7 | Explicit framing, polynomial order/inversion, termination, shortening and interleaving |
| PSK synchronization | Decoder-assisted timing, clock, carrier, drift and complex-gain fitting | BPSK/QPSK/OQPSK only; conditional waveform guard, not independent statistical evidence |
| Coherent CPM | Complex-IQ phase/history BCJR with cached channel metrics | Explicit finite Gaussian/rectangular pulse, integer SPS, rational h; no coherent RF phase from OGG |
| Repeat identity | Existing checksum-protected mission-header field layout | Mission must guarantee an immutable coded payload within the repeat window |
| Execution | Ordered parallel candidates, shared preprocessing, exact successful-pass reuse, resumable windows | No heuristic pruning; window completion is atomic, not a hard time deadline |

[Coding details](recovery-code.md), [PSK tracking](decoder-assisted-tracking.md),
[CPM model](coherent-cpm.md), [full receiver contract](advanced-iq-receiver.md).

Variable-length AX.25 UI has a separate [HDLC IQ path](recovery-hdlc.md):
`decode-recovery-hdlc`. It retains ordinary demodulation results and adds
complete-observation BCJR trained on separate, already FCS-validated packets.
It does not yet attach fixed-codeword CPM, turbo/FEC or SIC to arbitrary HDLC
streams. A recording without usable training packets retains the baseline only.

The original LDPC branch retains its arithmetic. New non-LDPC soft detection
includes the first and last received observations and marginalizes unknown
exterior symbols. Omitting those observations was found to lose valid uncoded
frames and occasionally accept an incorrect CRC-valid decision in a deliberately
CRC-invalid control. The development failures are retained in the test report;
the negative cases were not removed or weakened.

## Profiles

The `code` field still accepts/serializes the original raw LDPC configuration.
Other codes use the explicit tagged forms in the coding documentation. Optional
receiver settings are:

```json
"recovery": {
  "workers": 4,
  "coherent_cpm": false,
  "tracking": {
    "timing_bound_symbols": 0.5,
    "clock_bound_ppm": 2000,
    "carrier_bound_hz": 20,
    "drift_bound_hz_per_s": 50,
    "iterations": 3,
    "minimum_holdout_improvement": 0.01,
    "minimum_holdout_explained": 0.1,
    "maximum_work": 64000000
  }
}
```

Use `tracking: null` for non-PSK waveforms. `coherent_cpm: true` applies only to
FSK/GFSK/GMSK, with integer samples/symbol, zero clock-bank offsets, and at most
4096 total preamble/header/payload bits. The modulation index must be p/q with
p,q in 1..16 and h≤2. The residual carrier bound is at most 0.2 radians/sample.
All original branches remain in the accepted union; rejected optional fits are
diagnosed and never erase their results. Resource exhaustion remains a failed
attempt, not a successful partial result.

For PSK, genuine decoder extrinsics may guide a fit before successful decoding.
After independent received integrity passes, a reconstructed codeword can guide
a data-aided fit. Neither kind of feedback is counted as a second received copy.
Refinement-only frames are not used with the original geometry for SIC.

`repetition.mission_header` describes the existing bytes immediately after sync:

```json
{
  "bytes": 12,
  "protected_start": 0,
  "protected_end": 8,
  "checksum_offset": 8,
  "checksum": "crc32c_be",
  "identity_fields": [{"start": 0, "end": 4}, {"start": 6, "end": 8}],
  "immutable_codeword_contract": "Mission-defined epoch and counter name one immutable codeword within the repeat window"
}
```

This is an adapter example, **not a certified real satellite profile**. A header
length, spacecraft ID, or unprotected timestamp alone is not a repeat identity.
Changing telemetry measurements must never be merged. Missions without a
separately verifiable header identity leave this feature disabled.

## Resuming a fixed window plan

`decode-recovery-session` accepts a plan with `format`, `sample_rate_hz`,
`receiver` and a `windows` list of `{start_sample, sample_count}`. Its receiver
is the same configuration as `decode-advanced-iq`.

```sh
telemetry-yield-rs decode-recovery-session --input capture.cf32 \
  --profile windows.json --output recovery-run --max-windows 1
telemetry-yield-rs decode-recovery-session --input capture.cf32 \
  --profile windows.json --output recovery-run --resume
```

Completed windows have atomic checkpoints. Resume binds the source, executable,
compute identity, entire plan, consumed-IQ hash and result envelope. It rechecks
frame integrity and provenance before reuse; changing the receiver requires a
fresh run. Duplicate PDUs across windows count once in a session summary. This
does not yet resume an interrupted window or change a quick plan into a different
deep plan while reusing arbitrary internal decoder state.

Immutable file/session guarantees currently require Linux: a held descriptor
plus change notifications detects edits, path replacement and edit/restore even
when filesystem timestamps collide. An observed change invalidates the session.
This is consistency checking on a trusted host, not protection from a privileged
attacker rewriting the program or kernel.

## CPU and CUDA scope

`recovery.workers` parallelizes independent candidates, with ordered result
reduction and identical arithmetic inside each attempt. Successful fixed-channel
first passes are reused rather than recomputed. Original IQ statistics and
channel-only likelihoods are cached only where their inputs are identical.

The existing optional `--compute cpu|cuda` backend can accelerate RRC FIR work;
the build still defaults to CPU. It does **not** move BCJR, LDPC, K7, synchronization
or CRC to the GPU. No GPU hardware benchmark for this extension is claimed.
GPU failures do not silently fall back to CPU. More optional inference can cost
more work despite these exact-reuse optimizations.

## Qualification

Run the Rust unit/integration tests, `recovery_pipeline_benchmark`, and legacy
`multimode_iq_benchmark`/`advanced_iq_benchmark` regression before promotion.
An independently held-out, mission-matched real-IQ comparison with external
receivers remains required. Synthetic correctness, additional synthetic yield,
runtime parity and publication evidence are separate gates.

The [paired-study receipt contract](recovery-study.md) provides one accounting
format for baseline, candidate and ablation runs, preserving failed attempts and
separating independently labelled signal-positive observations. It does not turn
development data or decoder-attested external packets into ground truth.
