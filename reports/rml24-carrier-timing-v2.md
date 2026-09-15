# RML24 carrier/timing recovery v2

Date: 2026-09-02

## Outcome

The frozen truth-independent v2 path passed every preregistered holdout rule.
On the primary 36-record +20 dB BPSK/QPSK/OQPSK holdout it reduced BER,
including missing aligned edges, from 0.235669 to 0.149167.  The absolute
v2-minus-legacy delta is -0.086502; its deterministic paired-bootstrap 95%
interval is [-0.136965, -0.042036].

The result is not a complete RML24 demodulator.  BPSK and QPSK at 100 ksym/s
remain close to the random/null floor, and OQPSK at 250 ksym/s remains weak.
The improvement is nevertheless real, survives a frozen record-level holdout,
and is far separated from the wrong-record control BER of 0.461200 on the
primary population.

The implementation is available as the explicit
`recovery_version="carrier_timing_v2"` route.  The constructor default remains
`legacy`; this preserves the frozen code hash and avoids silently changing the
meaning of existing full-run reports.  Promotion to an operational default can
be a separate versioned change after a full-dataset replay.

## What changed

1. BPSK uses a bounded second-power FFT carrier estimate instead of unwrapping
   a sample-by-sample squared phase and fitting its slope.  The search is
   limited to ±2 kHz: the article's CFO plus Doppler bound is ±1.5 kHz, with a
   fixed 500 Hz margin.  The recovered constant phase remains modulo π.
2. QPSK and OQPSK use the corresponding bounded fourth-power FFT estimate.
   QPSK decisions are otherwise unchanged.
3. OQPSK emits all six boundary-valid hypotheses before truth is available:
   either I or Q delayed by half a symbol, combined with branch-pairing offset
   -1/0/+1 symbol.  The already-declared final D4/timing/alignment scorer chooses
   among those fixed hypotheses.
4. GMSK follows the legacy implementation bit-for-bit as a control.

No RRC bank or phase-tracking loop was promoted.  Both were tested as bounded
development hypotheses; the RRC IQ-only selector was null on the earlier
holdout, and phase smoothing regressed development BER.  The v2 path therefore
has no runtime parameter sweep or truth-dependent parameter selector.

## Preregistration and truth boundary

The parent plan was written before the new holdout rows were read.  Development
used only previously inspected indices `0-7,37,109,503,733`.  Holdout indices
`211,389,641,887` were frozen in advance across four modulations, three SNRs and
three symbol rates, giving 144 records.

After development, the exact implementation, configuration and evaluator were
hashed into a second holdout plan.  Only then did the evaluator load the
holdout rows.  For every record, both legacy and v2 waveform decisions were
produced before its truth row was loaded.  Truth entered only the same final
polarity/D4/alignment scorer.  Missing aligned edges count as errors and the
maximum alignment is 1024 bits.

The full dataset aggregate and earlier diagnostic rows were already known.
Accordingly this is a preregistered record-level holdout, not a never-observed
dataset-level benchmark.

## Primary holdout

| Metric | Legacy | Carrier/timing v2 |
|---|---:|---:|
| Records | 36 | 36 |
| Truth bits | 34,820 | 34,820 |
| Errors including edges | 8,206 | 5,194 |
| BER including edges | 0.235669 | 0.149167 |
| Overlap BER | 0.183745 | 0.089523 |
| Edge erasures | 2,215 | 2,281 |

The v2 improvement is not caused by scoring fewer difficult bits: it has 66
more edge erasures than legacy, and those erasures are counted as errors.

Preregistered promotion checks:

- absolute improvement at least 0.01: pass (`0.086502`);
- paired-bootstrap 97.5th percentile below zero: pass (`-0.042036`);
- no target modulation regresses by more than 0.005: pass;
- GMSK no-regression guardrail: pass, exact delta `0.0`.

Per-modulation high-SNR deltas, v2 minus legacy:

| Modulation | BER delta |
|---|---:|
| BPSK | -0.235928 |
| OQPSK | -0.098291 |
| QPSK | 0.000000 |
| GMSK control | 0.000000 |

## High-SNR holdout by rate

| Modulation | Rate | Legacy BER | v2 BER | Legacy overlap BER | v2 overlap BER |
|---|---:|---:|---:|---:|---:|
| BPSK | 100k | 0.439024 | 0.437805 | 0.423559 | 0.432964 |
| BPSK | 250k | 0.339355 | 0.151855 | 0.257002 | 0.001724 |
| BPSK | 500k | 0.334473 | 0.027344 | 0.316106 | 0.000502 |
| OQPSK | 100k | 0.442683 | 0.433537 | 0.432298 | 0.420823 |
| OQPSK | 250k | 0.355957 | 0.288574 | 0.264361 | 0.190106 |
| OQPSK | 500k | 0.272583 | 0.140991 | 0.204406 | 0.059727 |
| QPSK | 100k | 0.446341 | 0.446341 | 0.440197 | 0.440197 |
| QPSK | 250k | 0.094727 | 0.094727 | 0.002690 | 0.002690 |
| QPSK | 500k | 0.029785 | 0.029785 | 0.003261 | 0.003261 |
| GMSK | 100k | 0.352439 | 0.352439 | 0.241429 | 0.241429 |
| GMSK | 250k | 0.102539 | 0.102539 | 0.004334 | 0.004334 |
| GMSK | 500k | 0.235596 | 0.235596 | 0.131966 | 0.131966 |

The very low overlap BER for BPSK 250/500k shows that the bounded square-law
carrier estimate fixes a genuine waveform-recovery failure.  OQPSK improves
because the previous single branch pairing often aligned one bit lane and
mispaired the other, which naturally produces BER near 0.25.

## All holdout SNRs

| SNR | Legacy BER | v2 BER | Legacy overlap BER | v2 overlap BER |
|---:|---:|---:|---:|---:|
| -20 dB | 0.461660 | 0.461947 | 0.455483 | 0.457258 |
| 0 dB | 0.458956 | 0.376580 | 0.444040 | 0.345470 |
| +20 dB | 0.231428 | 0.159343 | 0.171423 | 0.092164 |

Across all 144 holdout records, legacy BER is 0.384015 and v2 BER is 0.332623.
The v2 wrong-record control is 0.461253.  The tiny -20 dB regression is expected
null-floor variation and does not affect the preregistered high-SNR promotion
population.

## Development evidence

On 108 previously inspected high-SNR target records, the frozen plugin path
reduced BER from 0.252479 to 0.165824.  GMSK was exactly unchanged at 0.163747.
The independent holdout delta (-0.086502) is almost identical to the development
delta (-0.086655), which argues against a record-specific fit.

## Reproducibility

- Parent plan SHA-256:
  `779cf607d11cb73ccd731d1f0c2026df6c39d15007a4c30475e79c29fd27393e`
- Frozen configuration SHA-256:
  `8ab1e87411ecb11e21911257e36ecef750d02f1e3ef56be67459796085333acc`
- Holdout plan SHA-256:
  `7db8b8860ad5671444c0ed5bc45b2322dd48c4c5dab0ffa91409ad4de9f4750d`
- Evaluated physical plugin SHA-256:
  `c2ffb75a8ff03098f08e604c1170434dffa931f0a262c9e7d473c2c051f3327d`
- Holdout evaluator SHA-256:
  `a76861a285c0252c3b98e472daac1c7e9352815cc02e787c5eb06d638840016a`
- Holdout result SHA-256:
  `7974fddd6ee4d14afe95aa864f2ff27270d3fb5efd2f883a18d1bdca1f4b047a`
- Source manifest SHA-256:
  `dba5fd0b840c9835967963179ba2065baefad9a32985ac33a2f6b2b63e05a2ef`
- Source archive SHA-256:
  `daac87c1ce1b10c986c0a2bd6eaf5666316e629f45d53592fc76444bf3614c1d`

Commands:

```bash
rtk .venv/bin/python work/rml24/develop_carrier_timing_v2.py \
  --plan reports/rml24-carrier-timing-v2-plan.json \
  --output reports/rml24-carrier-timing-v2-development.json \
  --snr 20

rtk .venv/bin/python work/rml24/evaluate_carrier_timing_v2_holdout.py \
  --plan reports/rml24-carrier-timing-v2-holdout-plan.json \
  --output reports/rml24-carrier-timing-v2-holdout-result.json

rtk .venv/bin/pytest -q \
  tests/test_rml24_physical_plugin.py \
  tests/test_rml24_physical_diagnostic.py
```

Focused tests: `20 passed in 0.56s`.  The multi-candidate high-SNR development
replay took approximately 50.7 seconds; the frozen 144-record legacy/v2/wrong-
control holdout took 18.6 seconds on this VM.
