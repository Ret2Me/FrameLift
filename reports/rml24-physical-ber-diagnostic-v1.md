# RML24 physical BER diagnostic v1

Date: 2026-09-02

## Verdict

The published full-run BER near 0.464 is not a single IQ-conversion or bit-order
failure.  It combines two effects:

1. The scorer's `max_shift_bits=8` assumption is false for many real records.
   On a record-level preregistered transfer sample, 38/72 records selected a
   shift larger than eight bits (maximum 218).  Expanding only the final
   truth-aided scorer reduced BER from 0.464851 to 0.379539, while an expanded
   wrong-record control remained at 0.463862.
2. The current waveform recovery is genuinely weak for BPSK, OQPSK and
   100-ksym/s QPSK.  It is not random everywhere: at +20 dB, GMSK has 0.00576
   BER on the aligned overlap, and QPSK at 250/500 ksym/s has approximately
   0.002 overlap BER.  Therefore the near-random aggregate concealed both
   working and non-working cells.

There is no validated production DSP improvement in this diagnostic.  A
second preregistered high-SNR holdout tested the article-supported RRC 0.35
hypothesis.  The IQ-only selected RRC bank was fractionally worse than legacy
(0.236693 versus 0.236406 BER including edges), so the production demodulator
and the full BER report remain unchanged.

## Methodology and leakage boundary

The 72-record transfer plan covers BPSK/GMSK/OQPSK/QPSK, SNR -20/0/+20 dB,
100/250/500 ksym/s, and record indices 37 and 503.  The full aggregate and
development indices 0-7 had already been inspected; the exact transfer indices
were frozen before their IQ or truth rows were read.  This is a record-level
preregistered diagnostic transfer sample, not a pristine dataset-level test.

Waveform recovery emitted decisions without access to `bits` or
`bit_lengths`.  Truth was then used only by the declared final scorer to resolve
polarity/constellation symmetry and alignment.  The control paired each IQ row
with the other selected truth row from the same modulation/SNR/rate cell.
Missing truth at aligned edges counted as errors.

The separate 24-record frontend plan used only +20 dB records at indices 109
and 733, frozen before those rows were read.  Legacy, fixed RRC spans 4/6/8,
and the IQ-only eye-score selection were all produced before truth entered the
same final scorer.  Fixed-span results are diagnostic sensitivity results;
only the IQ-only selector is an honest deployable selection rule.

## Aggregate alignment result

| Scorer | Errors / truth bits | BER incl. edges | BER on overlap | Edge erasures | `|shift| > 8` |
|---|---:|---:|---:|---:|---:|
| Reported limit ±8 | 29,135 / 62,676 | 0.464851 | 0.462475 | 277 | 0 by construction |
| Expanded limit ±1024 | 23,788 / 62,676 | 0.379539 | 0.358728 | 2,034 | 38 / 72 |
| Wrong-record control, expanded | 29,073 / 62,676 | 0.463862 | 0.458715 | 596 | 27 / 72 |

The expanded scorer has median absolute shift 10 bits and maximum 218 bits.
The wrong-record control has median four and maximum 38 bits.  Because edge
erasures remain errors, the reduction is not obtained by discarding a large
unscored region.

## BER by SNR

| SNR | Expanded BER incl. edges | Expanded BER on overlap | Wrong-record BER incl. edges | Edge erasures | `|shift| > 8` |
|---:|---:|---:|---:|---:|---:|
| -20 dB | 0.463048 | 0.457622 | 0.464149 | 209 | 9 / 24 |
| 0 dB | 0.459458 | 0.453071 | 0.462952 | 244 | 11 / 24 |
| +20 dB | 0.216111 | 0.151934 | 0.464484 | 1,581 | 18 / 24 |

The low-SNR rows remain at the controlled random/null floor.  The strong
high-SNR separation from the wrong-record control proves that some recovered
sequences correspond to their own truth rows.

## High-SNR result by modulation and rate

| Modulation | Rate | BER incl. edges | BER on overlap | Edge erasures | `|shift| > 8` |
|---|---:|---:|---:|---:|---:|
| BPSK | 100k | 0.448780 | 0.444717 | 3 | 0 / 2 |
| BPSK | 250k | 0.274414 | 0.228453 | 61 | 2 / 2 |
| BPSK | 500k | 0.328125 | 0.252986 | 206 | 2 / 2 |
| GMSK | 100k | 0.234146 | 0.006329 | 94 | 2 / 2 |
| GMSK | 250k | 0.126953 | 0.004454 | 126 | 2 / 2 |
| GMSK | 500k | 0.073730 | 0.006286 | 139 | 2 / 2 |
| OQPSK | 100k | 0.436585 | 0.428218 | 12 | 0 / 2 |
| OQPSK | 250k | 0.381348 | 0.242225 | 376 | 2 / 2 |
| OQPSK | 500k | 0.289551 | 0.241002 | 262 | 2 / 2 |
| QPSK | 100k | 0.448780 | 0.444717 | 6 | 0 / 2 |
| QPSK | 250k | 0.092773 | 0.002148 | 186 | 2 / 2 |
| QPSK | 500k | 0.028809 | 0.002007 | 110 | 2 / 2 |

This rate dependence also explains why the full aggregate looked almost
random: longer 500k records dominate the bit count, and the old ±8 scorer
missed the large acquisition offsets even in the working QPSK/GMSK cells.

## IQ, truth and mapping checks

- Every selected IQ shard has shape `(1000, 2, 2048)` and dtype little-endian
  float32.  Every selected truth value is binary, with little-endian int32
  storage.
- Truth lengths are 205/512/1024 bits for BPSK and GMSK at 100/250/500 ksym/s,
  and 410/1024/2048 for QPSK and OQPSK.  The 100k rows round 204.8 nominal
  symbols upward; the other rates match the nominal record duration exactly.
- At +20 dB, the published direct bit sequence gives aggregate BER 0.216111.
  Reversal, differential XOR, cumulative XOR and per-byte bit reversal give
  0.457448, 0.463814, 0.464675 and 0.434473 respectively.  There is no evidence
  for those alternative upstream bit semantics.
- Published `I + jQ`, conjugated IQ and swapped I/Q tie exactly after the
  declared polarity/D4 ambiguity scorer.  Time reversal gives 0.457448.  This
  supports the published sample-time direction and rules out a simple IQ-axis
  conversion error; the symmetry-equivalent orientation cannot be identified
  more narrowly by this scorer.

## RRC 0.35 frontend holdout

| Frontend | Errors / truth bits | BER incl. edges | BER on overlap | Edge erasures |
|---|---:|---:|---:|---:|
| Legacy | 4,939 / 20,892 | 0.236406 | 0.187357 | 1,261 |
| RRC, IQ-only eye-selected span | 4,945 / 20,892 | 0.236693 | 0.188489 | 1,241 |
| Fixed RRC span 4 (diagnostic) | 4,946 / 20,892 | 0.236741 | 0.188210 | 1,249 |
| Fixed RRC span 6 (diagnostic) | 4,916 / 20,892 | 0.235305 | 0.187262 | 1,235 |
| Fixed RRC span 8 (diagnostic) | 4,923 / 20,892 | 0.235640 | 0.187618 | 1,235 |
| Wrong-record legacy | 9,695 / 20,892 | 0.464053 | 0.458533 | 213 |
| Wrong-record RRC selector | 9,711 / 20,892 | 0.464819 | 0.459385 | 210 |

The best fixed span improves absolute BER by only 0.00110 and was selected
with truth, so it is not a deployable result.  The preregistered IQ-only
selector regresses by 0.00029.  RRC 0.35 is therefore a null result on this
holdout, not a production fix.

## What the official sources do and do not specify

The article specifies RRC pulse shaping and gives the formula
([article.html:1001](../work/rml24-audit/article.html#L1001)); Table 2 specifies
roll-off 0.35, sample rate 1 MHz, CFO up to 500 Hz, Doppler up to 1 kHz and
multipath delays 0/4.5/8.5 ns
([table2.html:592](../work/rml24-audit/table2.html#L592)).  It gives only a
high-level receiver order—filtering, carrier synchronization/equalization,
matched filtering, then timing recovery
([article.html:1087](../work/rml24-audit/article.html#L1087)).

It does not specify the RRC span/window/padding, carrier-loop detector or loop
coefficients, equalizer algorithm/taps/training, symbol synchronizer, Gray or
natural mapping, bit-to-I/Q order, differential coding, OQPSK delay convention,
or GMSK initial state.  Standard modulations are explicitly not described
individually ([article.html:977](../work/rml24-audit/article.html#L977));
`Bitdata` is described only as raw ground-truth binary sequence
([article.html:1034](../work/rml24-audit/article.html#L1034),
[article.html:1053](../work/rml24-audit/article.html#L1053)).

The checked-out official repository at commit
`5efe95e6a8baf05ea626c8175a69de68cf8ccacf` contains README, AMR models and
figures, but no GNU Radio generation flowgraphs, mapping code or reference
demodulator.  Its README says the generation code was still being organized
([README.md:32](../work/rml24/upstream/README.md#L32)).  Consequently, an
"official RML24 PLL/equalizer" cannot be reconstructed from the released local
sources without guessing.  At 1 MHz the largest listed 8.5 ns path delay is
only 0.0085 output sample, so the table also does not define a meaningful
sample-spaced equalizer at the released IQ rate.

## Reproducibility

- Source archive SHA-256:
  `daac87c1ce1b10c986c0a2bd6eaf5666316e629f45d53592fc76444bf3614c1d`
- Converted manifest SHA-256:
  `dba5fd0b840c9835967963179ba2065baefad9a32985ac33a2f6b2b63e05a2ef`
- Physical plugin SHA-256:
  `a9c493239fb05708c725619c30dea9344a0ca8d08ae0d0560d8f8842d350a764`
- Alignment plan SHA-256:
  `10843507c0f9959a6abaa6c5fe40318734126bcb47cd662ed3876d42946a33d7`
- Alignment diagnostic implementation SHA-256:
  `51176a21730f42d99b3db8f98f5cce68adf30bd81d31c6157be69a58fe68a67c`
- Alignment diagnostic JSON SHA-256:
  `5dc8517722717a9cb24235545f0d88c73ab6765dbf1dabf1ceac34010d63a42b`
- Frontend holdout plan SHA-256:
  `3aef18bd12e486d13f7db08d6f288d971ec72390ba69479c16bbb9b6695bb2e1`
- Frontend diagnostic implementation SHA-256:
  `ec69e0409f1a0919b3a5c0e4669008cb4b091ce0972febfb738e5475d4ead36c`
- Frontend diagnostic JSON SHA-256:
  `19b6c7e35352eb784b0f82420345fdadd552eec5dfacc63d6a2c6ef0b015b5c7`

Commands:

```bash
rtk .venv/bin/python work/rml24/diagnose_physical_ber_v1.py \
  --plan reports/rml24-physical-ber-diagnostic-plan-v1.json \
  --output reports/rml24-physical-ber-diagnostic-v1.json

rtk .venv/bin/python work/rml24/diagnose_physical_frontend_v1.py \
  --plan reports/rml24-physical-frontend-diagnostic-plan-v1.json \
  --output reports/rml24-physical-frontend-diagnostic-v1.json

rtk .venv/bin/pytest -q \
  tests/test_rml24_physical_diagnostic.py \
  tests/test_rml24_physical_plugin.py
```

Focused test result: `15 passed in 0.50s`.

Machine-readable details, including all 36 modulation/SNR/rate cells and every
selected record, are in `rml24-physical-ber-diagnostic-v1.json`.  The separate
frontend holdout is in `rml24-physical-frontend-diagnostic-v1.json`.  The
existing full report `rml24-physical-ber-v1.json` was not overwritten.
