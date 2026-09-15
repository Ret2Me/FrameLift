# Frozen experiment protocol — Phase A pilot

Status: **frozen before empirical campaign results**. Amendments require a new
version and must not silently replace this file.

## Hypotheses and thresholds

- H1: among independently confirmed signals with no live frames, F10 is below
  50%. Confirmation levels remain separate strata.
- H2: the relative increase in unique CRC-valid frames from offline search over
  historical live configuration C0 is at least 10%.
- H3: transmitter frequency and symbol-rate residuals are temporally
  predictable; the preregistered target is temporal-test R² > 0.5 for at least
  five satellites.
- H4: human waterfall false negatives are measurable and increase with TLE age.

H2 is **not measurable** until historical C0 is recovered from the observation
job/configuration; the current transmitter database is not an accepted proxy.

## Primary metrics

Frames are unique by `(transmission_event_id, sha256(payload))`. Every yield
result is paired with `false_accepts_per_hour` on explicitly stratified negative
controls, grouped bootstrap confidence intervals, and CPU/wall-time cost.

## Splits and leakage

The atomic split unit is `transmission_event_id`. Required evaluations are
in-domain, leave-one-satellite-out, leave-one-station-out, temporal holdout, and
a frozen challenge set. Features derived from history, corrected parameters, or
drift models are fit only inside the training fold and use only pre-observation
information.

## Cohorts

CAMRAS IQ is a depth cohort. Archive.org audio is a scale cohort. They are never
presented as interchangeable evidence for amplitude/phase-dependent categories.
The four negative-control mechanisms are separate strata, not one ground-truth
absence class. Human review of ambiguous IQ requires blind double adjudication.

## Stop rules

Unmet resource, source-contract, historical-C0, or leakage gates stop technical
promotion. Under ADR 004, unresolved license paperwork does not stop internal
research, but it still stops publication export. Thresholds are not lowered
after viewing results.
