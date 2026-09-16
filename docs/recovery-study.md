# Recovery study receipts and promotion gates

`summarize-recovery-study` accounts for paired decoder results. It is not a
decoder, a source-file audit, or a certificate of publication readiness.

```sh
telemetry-yield-rs summarize-recovery-study \
  --input study.json --output summary.json
```

## Receipt contract

The typed Rust contract is in `rust/recovery_metrics.rs`. A study declares
`schema: "framelift-recovery-study-v1"`, one `baseline_arm`, distinct
`candidate_arms`, and `observations`. Candidate arms may be full receivers or
individual ablations; no special naming convention changes their meaning.

Every observation declares its identifier, mission, station, pass group,
numeric-input SHA-256, duration, exposure category, optional independently
established signal label/evidence, control status and arm receipts. Different
observations cannot contain the identical input hash. Overlapping windows must
be reconciled into a single observation before aggregation.

Each arm binds its name, completed/failed/unsupported/timed-out status, input,
runtime and profile hashes, wall time, optional CPU time, error and frames. The
input hash must match the observation's numeric input: IQ and demodulated audio
are different experiments, even if they originate in the same recording. Failed
arms have no admitted frame set. Missing arms remain visible as attrition.

Frame validation is explicit:

- `received_ax25_fcs` supplies `frame_hex` including received FCS. The aggregator
  rechecks CRC, UI structure and exact correspondence to the FCS-free payload.
- `received_frame_check` supplies an explicit supported `validator`. Its key is
  the complete checked frame, including received checksum bytes. AX.25 is
  rejected here: it must use the preceding variant so FCS-inclusive and
  FCS-free keys cannot silently produce false additions/losses.
- `external_decoder_attested` records a decoder name/version. It never becomes
  an independently checked received FCS. KISS normally strips those bytes.

Keys must use the same representation within a comparison. Do not compare
FCS-inclusive keys from one arm with FCS-free keys from another. Byte totals
include protocol headers; they are not automatically application payload bytes.

## Outputs

The report includes observation-local unique frames, additions **and losses**,
net yield, added/lost bytes, paired wall times, per-mission and exposure strata, independently
labelled `confirmed_signal`, and a separate `baseline_positive` stratum. The
last two must not be conflated. Zero baseline yield produces a null percentage,
not an infinite improvement. Duplicate frame records do not increase yield.

Known-negative controls are excluded from positive-yield totals and report
accepted frames and recording-hour exposure separately. Zero observed accepts
does not establish zero population false-acceptance probability. Synthetic
controls do not determine a real-world false-acceptance rate.

Descriptive intervals reuse the archive report's paired bootstrap. Observations
sharing a station or pass group form connected components, which are resampled
together. Fewer than two components produces no interval. The method does not
prove that remaining components are independent or that the cohort represents
the satellite population. Its seeded output is deterministic.

## Experiment order

1. Close regression, protocol, negative-control, resume and worker-parity tests.
2. Validate actual mission framing on a development set; fix the receiver,
   profiles, comparators, selection rule, budgets and exposure exclusions.
3. Freeze metadata selection and runtime identities before examining test
   waveforms or outcomes. Keep selected failures and unavailable recordings.
4. Run matched numeric inputs through all applicable arms. Match compute budgets
   for an efficiency comparison; run-to-completion is a separate comparison.
5. Report paired gains/losses, attrition, protocol corroboration, false-acceptance
   controls, timing and ablations. Keep IQ and OGG results distinct.

A newly downloaded archive file is not automatically a holdout: prior access to
the waveform, overlapping recordings or decoded payloads can expose it. A small
multi-mission coverage pilot is useful engineering evidence but does not by
itself close these publication gates. The summarizer always emits
`publication_ready: false`, `source_provenance_verified: false` and
`equal_compute_certified: false`; those claims require separate evidence.
