# Blind phase FSK confirmatory v1 — review before execution

Status: metadata-only review. No selected IQ object or decoder outcome was read.

## Cohort facts fixed before execution

- The selection contains 30 observations, 17 satellite UUIDs and 2,538,196,084
  expected CI16 bytes (3.0601322386 signal-hours at 57.6 ksample/s).
- Conservative exact-UUID, ±5 kHz, unique-NORAD and unique-profile routing finds
  an executable pinned SatYAML route for 14 observations.
- Of those 14, 12 observations from 7 satellites have an exact frequency-matched
  `FSK` + `AX.25 G3RUH` profile compatible with the candidate's current protocol
  adapter. The two Astrocast routes use FX.25/CCSDS framing and therefore are not
  protocol-matched candidate comparisons.
- The remaining 16 observations have no unambiguous executable route through the
  pinned profile registry. This is a coverage stratum, not a SatNOGS/gr-satellites
  demodulator failure.

## Claim hierarchy

1. The confirmatory paired comparison uses only the 12 exact FSK + AX.25 G3RUH
   observations. Its unit is an observation and its estimand is the difference in
   unique trusted complete-frame counts between candidate and executable baseline
   on identical bytes. This is a receiver-chain comparison; it is not an isolated
   DSP comparison unless both demodulators later feed the same deframer and
   validator.
2. The 14 profile-routable observations support a secondary whole-receiver union
   analysis. Results must be stratified by candidate protocol compatibility.
3. All 30 observations support descriptive deployable-pipeline coverage. Candidate
   results from the 16 profile-missing observations require independent strong
   protocol validation and cannot be described as superiority over an unavailable
   baseline.

## Is the frozen cohort sufficient for a publication claim?

No, not by the preregistered minimum of 30 comparable observations. The headline
`n=30` would overstate the effective comparison sample: the honest primary sample
is `n=12` (or `n=14` only for the more confounded whole-receiver question). This
cohort remains useful as preregistered pilot and coverage evidence. A separate
outcome-blind cohort must bring the exact protocol-matched comparison to at least
30 observations before a publication-level baseline-superiority claim. Candidate
code and tuning must stay frozen while that additional cohort is enrolled.

The earlier selection audit established reproducible, code-level outcome-blind
selection, but cannot cryptographically prove that the manually chosen selection
salt predated knowledge of catalogue outcomes. The claim should retain that
caveat; future cohorts should commit a public/randomness-derived salt before the
eligible population is available.

## Null and statistical gates

- Every observation gets 10 deterministic null controls: all-zero, full complex
  time reversal, and eight literal-uint64 PCG64 quarter-turn phase scrambles.
- Total null exposure is 30.6013223862 hours. With zero false accepts, the exact
  one-sided 95% Poisson upper rate is 0.0978955169/hour, below the 0.1/hour gate.
  At this fixed exposure any false accept fails the gate.
- All native emissions and all repaired/corrected-unmatched candidates on nulls
  count as false accepts. CRC validity or correlated frontend agreement does not
  authenticate a repair.
- Paired inference resamples observations, never frames: 10,000 PCG64 bootstrap
  draws, seed 20260903, two-sided percentile 95% interval.

## Freeze boundary

`work/blind-phase-confirmatory-v1/freeze_execution_plan.py` is ready to bind the
selection, both transmitter snapshots, parsed SatYAML registry, exact routed
SatYAML files, baseline executable/package records, final per-rate candidate
configuration, and the candidate's transitive local Python source closure. It
must not create the immutable execution-plan output until the 19.2 kbaud path and
the final candidate development rerun have passed their gates.
