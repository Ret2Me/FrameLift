# RML24 acquisition v2: preregistered result

## Verdict

The frozen waveform-time-origin estimator is a **null/negative result**. It
cannot replace the truth-aided final shift search.

The estimator receives only the blind timing phase, samples per symbol and
bits per symbol. It rounds the phase to the nearest symbol origin, producing a
shift of 0 or 1 bit for BPSK/GMSK and 0 or 2 bits for QPSK/OQPSK. Binary
polarity and square-constellation/OQPSK ambiguities remain explicitly
truth-aided, so this is not a fully blind BER claim.

## Frozen split results

Each split contains 768 waveform records: four supported modulations, three
symbol rates and 64 records per group. Each split contains 668,544 unique truth
bits. The final report scores ten declared correct/control methods per record.

| Split | zero-shift BER | estimator BER | estimator change vs zero |
|---|---:|---:|---:|
| Development (+20 dB, indices 0-63) | 0.480037 | 0.479959 | -0.000078 |
| Transfer (+10 dB, indices 256-319) | 0.479710 | 0.479228 | -0.000482 |
| Holdout (+16 dB, indices 640-703) | 0.479565 | 0.480377 | **+0.000812** |

Negative changes are improvements. The small development and transfer effects
did not generalize: holdout became slightly worse. The preregistered minimum
holdout improvement was 0.01 absolute.

On holdout, truth-aided +/-8 BER is 0.459433, wrong-record estimator BER is
0.480576 and random-candidate estimator BER is 0.480615. The estimator recovers
only 0.94% of the +/-8 oracle gain measured from the wrong-record control,
versus the preregistered requirement of at least 50%.

## What the +/-1024 diagnostic shows

The much wider truth-aided search reaches holdout BER 0.240509. This is not
explained by search multiplicity alone: the same search gives 0.462707 against
the next record's truth and 0.462646 for random candidate bits. Particularly
strong correct-record results occur for QPSK at 500 kBd (0.032555), QPSK at
250 kBd (0.106720), and GMSK across the three rates (0.169189-0.215396).

This is evidence that useful decisions often exist at larger record-level
offsets, but it is not a usable receiver result because the winning offset is
selected using transmitted bits. The blind clock phase is only a within-symbol
quantity and therefore contains insufficient information to recover those
large integer-symbol offsets.

## Controls and accounting

- The estimator API has no truth, bit-length, payload or timestamp input.
- FFT-based +/-8 and +/-1024 oracle scoring was tested for exact equivalence
  with the production alignment functions, including tie breaks.
- Wrong-record and deterministic random-candidate controls are present for the
  estimator and both oracle windows.
- Missing truth bits count as errors; excess candidate bits are ignored in BER
  but counted separately.
- All aggregate checks pass: overlap plus missing equals truth, every scored
  record has one shift, error bounds hold, and every method covers all records.
- The first complete holdout result is preserved by hash in the execution log;
  a reporting-only rerun reproduced it exactly while adding the two +/-1024
  controls.

## Research implication

A next estimator should target the **integer-symbol record offset**, not reuse
the timing-recovery phase. The stable large-offset modes in several
modulation/rate strata justify a separately preregistered calibration- or
waveform-boundary estimator, but these holdout results must not be used as a
fresh holdout for that successor.
