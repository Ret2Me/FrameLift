# Frozen AFSK1200/AX.25 same-IQ comparison

## Scope and methods

The cohort was frozen before decoding: 23 local CI16-LE recordings at 57,600 sample/s, selected because the catalog and exact active transmitter agree on AFSK at 1,200 baud while the installed SatYAML routing has no exact mission profile. The official control is therefore explicitly a generic waveform profile, not a mission decoder.

The native plugin uses an RF FM discriminator, independent noncoherent Bell-202 1,200/2,200 Hz tone energy, a truth-free timing bank, NRZI/HDLC, CRC-16/X.25, and strict AX.25 UI parsing. Raw candidates never count.

## Result

- Frozen IQ observations: **23**
- Official strict unique AX.25 PDUs: **0**
- Native strict unique AX.25 PDUs: **0**
- Native-only strict PDUs: **0**
- Official-only strict PDUs: **0**
- Null controls: **{'all_zero_iq': 0, 'deterministic_sample_permutation': 0, 'wrong_baud_2400': 0}**
- Official campaign/audit complete: **True/True**
- Verdict: **zero_zero_parity_not_victory**

This is parity only in the narrow fail-closed sense: neither implementation decoded telemetry from this cohort, and all null controls stayed at zero. It is **not evidence that the native method is better**, and it does not establish positive-signal recall. A same-IQ cohort containing independently confirmed AFSK frames is still required for that claim.
