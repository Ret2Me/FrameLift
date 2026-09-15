# RML24 blind-origin benchmark: Methods, Results, and Limitations

## Claim boundary

This package supports one narrow result: on a future-public-randomness-selected,
stratified holdout of 1,008 RML24 records, the frozen `blind_origin_v3`
physical-layer origin correction reduced micro-averaged bit-error rate (BER)
relative to the `acquisition_v2` baseline. It does **not** establish a fully
blind receiver, packet or telemetry yield, recovery from a real satellite
pass, superiority to SatNOGS, or deployment readiness of the wider system.

RML24 is described by Zhang et al. as a hardware-in-the-loop (HIL) satellite
TT&C dataset with simulated satellite-channel effects and RF-chain artifacts.
The authors explicitly distinguish it from a true physical space-to-ground
channel. The source publication is [Scientific Data 13, article 860
(2026)](https://doi.org/10.1038/s41597-026-07182-7); the full article is also
[available from Nature](https://www.nature.com/articles/s41597-026-07182-7).

## Methods

### Data and confirmatory cohort

The local RML24 shard manifest contains 1,323,000 records from the current
download. Each record contains 2,048 complex I/Q samples at 1 MHz and associated
raw truth bits. This experiment used only BPSK, GMSK, OQPSK, and QPSK, at every
available SNR from -20 through 20 dB in 2 dB steps and symbol rates of 100, 250,
and 500 kBd. These factors form 252 complete
modulation/SNR/symbol-rate groups.

The selection unit was a group-local record index. Predeclared ranges and
individual indices used by earlier development were excluded. Within every
group, two eligible records were assigned to transfer and four to holdout,
giving 504 transfer and 1,008 holdout records. The selector used group-manifest
metadata and the output of the NIST Randomness Beacon 2.0 pulse at
`2026-09-03T12:45:00.000Z`; it did not open selected I/Q or truth. The frozen
selection commitment was RFC 3161 timestamped at `2026-09-03T12:32:18Z`, 762
seconds before that pulse. The independent audit reimplemented selection and
verified the pulse certificate, signature, output relation, and timestamp.

This is a new record-level outcome-blind selection, not a pristine-dataset
experiment. Earlier project-wide processing had already exposed aggregate
results over all 1,323,000 local records. Individual outcomes for the newly
selected records were not consulted during selection.

### Compared methods and information boundary

Both methods received identical selected waveform records. `acquisition_v2`
was the baseline waveform-origin rule. `blind_origin_v3` was the frozen
candidate: an opt-in deterministic whole-symbol delay for each supported
modulation/rate profile. It was frozen before public-random cohort selection;
no retraining or parameter change was permitted.

The candidate used the I/Q waveform plus the known modulation and symbol-rate
profile. It did not receive truth bits, SNR, or record identity while choosing
its origin correction. Truth was available to the final BER scorer and named
truth-aided or negative controls. Because modulation and rate labels were
provided and truth remained in the scoring process, this is not a fully blind
BER benchmark.

### Endpoint and statistics

The preregistered primary endpoint was micro-BER over every holdout truth bit.
Missing candidate edge bits counted as errors; candidate bits beyond the truth
extent were excluded from the BER denominator and reported. The primary
contrast was `blind_origin_v3 - acquisition_v2`, so negative values favor the
candidate.

Uncertainty was evaluated with 10,000 percentile-bootstrap replicates,
resampling complete modulation/SNR/symbol-rate groups. A paired group-level
sign-flip test used 100,000 deterministic draws. The sole primary success rule
required both at least 0.01 absolute BER improvement and a two-sided 95%
bootstrap upper bound below zero. Per-modulation results and record-level
win/tie/loss (W/T/L) are descriptive secondary endpoints.

### Recorded deviation and audit

The first transfer execution failed closed because the evaluator omitted an
already-frozen configuration-shape adapter. It opened I/Q and truth for one
BPSK transfer record and constructed a truth view, but the candidate did not
finish, truth scoring did not run, no metric was written, and no holdout record
was opened. The amended evaluator restored only that pre-existing adapter; the
selected IDs, candidate code and parameters, scoring, endpoints, and statistics
were unchanged. This deviation prevents a pristine-transfer claim but, subject
to the access record being accurate, did not expose the primary holdout.

Two completed evaluations produced byte-identical JSON. A deterministic audit
and a separately implemented, read-only score audit recomputed the selection,
micro-BER, confidence interval, sign-flip result, and provenance checks. That
second audit was performed by another agent role on the same host, filesystem,
session, and local artifacts. It is role separation, not external or
organizational independence.

## Results

The primary holdout contained 877,464 truth bits across 1,008 records and 252
groups.

| Quantity | `acquisition_v2` | `blind_origin_v3` | Candidate minus baseline |
|---|---:|---:|---:|
| Bit errors | 420,529 | 384,563 | -35,966 |
| Micro-BER | 0.4792549894 | 0.4382664132 | -0.0409885762 |

The absolute BER improvement was 0.0409885762 (about 8.55% relative to the
baseline BER). The 95% group-cluster percentile-bootstrap interval for the
candidate-minus-baseline difference was [-0.0564436786, -0.0266439396]. The
paired group sign-flip two-sided p-value was `0.00000999990000099999`. Both
parts of the preregistered success rule passed.

Record-level W/T/L for the candidate versus baseline was **329/221/458**. The
candidate therefore lost on more individual records than it won, even though
it reduced total bit errors. This is not a contradiction: the primary estimand
weights every truth bit equally, whereas W/T/L gives every record one vote and
ignores improvement magnitude. The result should be reported with both views.

| Modulation | Records | Truth bits | Baseline BER | Candidate BER | Absolute improvement |
|---|---:|---:|---:|---:|---:|
| BPSK | 252 | 146,244 | 0.4839788299 | 0.3884603813 | 0.0955184486 |
| GMSK | 252 | 146,244 | 0.4846215913 | 0.4798350702 | 0.0047865212 |
| OQPSK | 252 | 292,488 | 0.4724911791 | 0.4689252209 | 0.0035659583 |
| QPSK | 252 | 292,488 | 0.4809735784 | 0.4117262930 | 0.0692472854 |

The transfer split showed the same aggregate direction: BER changed from
0.4787204945 to 0.4344839218 over 438,732 truth bits. Transfer was not the
primary confirmatory endpoint.

## Limitations

- The channel is HIL plus synthetic space-channel impairment, not a real
  satellite downlink or an operationally representative pass distribution.
- Only four modulation families and three known symbol rates were evaluated.
  The candidate is profile-calibrated and receives the modulation/rate labels.
- Micro-BER is a physical-layer bit metric. RML24 supplies no record-level
  AX.25/CCSDS frame truth, synchronization/FEC configuration, or integrity
  labels for measuring valid packet or telemetry yield.
- Aggregate results from a prior complete-dataset campaign were already known.
  The defensible novelty of this cohort is future-random, record-level
  outcome-blind selection, not pristine data or pristine factor groups.
- The transfer-only pre-score adapter failure is a protocol deviation. Its
  non-contamination conclusion depends on the recorded access log, not on a
  tamper-proof filesystem-access monitor.
- The sign-flip statistic is an unweighted mean of group BER differences,
  whereas the primary micro-BER difference weights truth bits. The p-value and
  confidence interval answer related but not identical weighting questions.
- Byte-identical repeats demonstrate deterministic replay on this execution
  environment. The amended lock does not actively enforce runtime identity, so
  this is not yet evidence of cross-runtime reproducibility.
- The independent audit was methodologically separate but ran on shared
  infrastructure. External replication remains outstanding.
- The positive result is uneven across strata and is concentrated chiefly in
  BPSK and QPSK. It must not be generalized to all RML24 modulations or all SNR
  conditions.

The exact permitted claim and all quantitative values are machine-checked by
`work/rml24/build_beacon_holdout_publication_manifest_v1.py`. The package
manifest deliberately sets both system-level publication readiness and
deployment readiness to `false`.
