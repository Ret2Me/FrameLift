# PolyITAN zero-frame G3RUH: official gr-satellites vs native

This is an artifact-only comparison on the exact same IQ bytes. No decoder was rerun.

## Cohort integrity

- Compared observations: 28 (SHA-256 and byte size matched for every input).
- Official full campaign: 67 observations; excluded from this comparison: 39.
- Selection: catalogue `frames_recovered=false`, exact mode `FSK AX.25 G3RUH`.

## Results

| Metric | official gr-satellites | native |
|---|---:|---:|
| Raw candidates | 11 | 9 |
| Strict rejected | 11 | 9 |
| Trusted AX.25 (unique per event) | 0 | 0 |
| Exact inner CCSDS Space Packets | 0 | 0 |

- Trusted overlap: 0
- Trusted union: 0
- Incremental native over official: 0
- Official-only trusted: 0

## Interpretation

**parity_zero_equals_zero** — Both branches recovered zero strictly trusted AX.25 frames on the same 28 IQ captures; this is 0=0 parity, not a decoder victory.

Raw candidates that fail strict AX.25 remain listed as rejected evidence and are not counted as telemetry. The official KISS path does not retain the consumed link FCS; the native path independently verifies its retained FCS before normalization.
