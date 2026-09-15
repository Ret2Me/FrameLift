# Data card: RML24 future-randomness holdout v1.1

## Summary

This card documents the exact RML24 subset used for the confirmatory
`blind_origin_v3` physical-layer BER comparison. It is not a card for the
complete RML24 release and does not certify the telemetry-yield system for
deployment.

RML24 is published as “Cognitive Radio for Satellite TT & C System: A General
Dataset Using Software-defined Radio,” [Scientific Data 13, article 860
(2026)](https://doi.org/10.1038/s41597-026-07182-7). The source paper describes
more than 1.3 million short I/Q examples spanning 22 modulation schemes. The
authors characterize the data as HIL RF acquisition combined with simulated
space-channel impairments, not true satellite-to-ground captures.

## Local source snapshot

| Property | Value |
|---|---|
| Local manifest | `work/nature-dataset/shards/manifest.json` |
| Manifest SHA-256 | `dba5fd0b840c9835967963179ba2065baefad9a32985ac33a2f6b2b63e05a2ef` |
| Local records | 1,323,000 |
| Samples per record | 2,048 complex I/Q samples |
| Sample rate | 1 MHz |
| Truth | Raw bit sequence and bit length paired with I/Q |
| Physical origin | USRP X410 HIL/RF-chain effects plus modeled channel impairments |
| Explicit exclusion | Not a corpus of real satellite passes |

The current local upload has 1,323,000 records, fewer than the 1,386,000 / 22
classes described in the paper. The benchmark trusts the hashed local shard
manifest for executable facts and uses the paper for dataset design context.

## Evaluated slice

- Modulations: BPSK, GMSK, OQPSK, QPSK.
- SNR: -20 to 20 dB inclusive, step 2 dB.
- Symbol rates: 100, 250, and 500 kBd.
- Factor groups: 4 modulations x 21 SNR levels x 3 rates = 252.
- Records available per group: 1,000 before preregistered exclusions.
- Transfer: 2 records per group, 504 total.
- Holdout: 4 records per group, 1,008 total.
- Holdout truth bits: 877,464.

The selection unit is `(modulation, SNR, symbol rate, group-local record
index)`. Development ranges and individual record indices were excluded before
selection. The remaining records were ranked deterministically from a seed
bound to the pre-pulse freeze lock and a future NIST Randomness Beacon value.
Transfer and holdout selections are disjoint.

## Record fields and access boundary

| Information | Candidate origin estimator | Final scorer / controls | Selector |
|---|---|---|---|
| I/Q waveform | Yes | Yes | No |
| Modulation label | Yes | Yes | Group metadata only |
| Symbol-rate label | Yes | Yes | Group metadata only |
| SNR label | No | Used for stratified reporting | Group metadata only |
| Group-local record identity | No estimator input | Used for pairing/audit | Used for deterministic selection |
| Truth bits and bit length | No | Yes | No |

The candidate is consequently waveform-only with respect to per-record origin
selection, but profile-informed rather than fully blind. Truth isolation refers
to the estimator decision, not to the final metric process.

## Outcome definition

The primary measure is micro-averaged BER over all holdout truth bits. Missing
candidate bits at record edges count as errors. Excess candidate bits do not
enter the denominator and are reported. Record W/T/L is a secondary endpoint:
the candidate recorded 329 wins, 221 ties, and 458 losses against the baseline.
Because records contain different truth-bit counts and W/T/L ignores effect
magnitude, it is not interchangeable with micro-BER.

## Selection and contamination history

- Aggregate outcomes from an earlier run over the entire 1,323,000-record local
  corpus were known before this experiment. No dataset-pristine or
  factor-group-pristine claim is made.
- The new selected record identities were fixed from a future public beacon
  without opening their I/Q or truth during selection.
- A failed first execution opened I/Q and truth for one transfer record only,
  then stopped before origin estimation completed or any score was written.
  No holdout record was opened before the evaluator amendment.
- The amendment restored an already-existing frozen configuration adapter and
  did not change the cohort, algorithm, parameters, scoring, or statistics.

## Appropriate uses

- Reproducing the reported four-modulation physical-layer origin/BER result.
- Studying sensitivity by modulation, SNR, and symbol rate while treating those
  analyses as secondary.
- Testing audit, public-random selection, and fail-closed provenance machinery.

## Inappropriate uses

- Claiming recovered AX.25 or CCSDS frames, packet yield, or useful telemetry.
- Comparing operational yield with SatNOGS.
- Estimating performance on real satellite passes or all RML24 modulations.
- Advertising a universal blind synchronizer or deployment-ready receiver.
- Calling the dataset, groups, or transfer execution pristine.

## Distribution and reproducibility

The publication package inventories code, reports, locks, selection material,
and audits, but does not duplicate the multi-gigabyte RML24 arrays. Reproduction
therefore requires a local shard tree whose manifest hashes to the value above
and whose members satisfy that manifest. Consult
`docs/rml24-beacon-holdout-reproducibility-v1.md` for the ordered checks.
