# RML24 blind whole-record origin v3

## Result

The preregistered v3 estimator is **positive within its narrow RML24 scope**.
On the record-disjoint +18 dB holdout, the frozen deterministic profile delay
reduced micro-BER from `0.477585619` at zero shift and `0.477738189` for
acquisition-v2 to `0.384143751`. It recovered `30.07%` of the measurable gap
from the better blind baseline to the diagnostic truth-aided +/-1024 oracle.
The unchanged transfer split had the same direction: `0.478285648` at zero
shift, `0.478040338` for acquisition-v2, and `0.396353868` for v3.

This result does **not** validate a dynamic burst-boundary detector. Direct
power and cyclostationary boundaries were null on development, and bounded
dynamic corrections were worse than the fixed profile constants. The positive
result is a calibrated constant per modulation and symbol rate.

| Split / method | BER | Errors / truth bits |
|---|---:|---:|
| Development zero shift | 0.478379384 | 959453 / 2005632 |
| Development acquisition-v2 | 0.478576828 | 959849 / 2005632 |
| Development v3 deterministic delay | 0.393690867 | 789599 / 2005632 |
| Development oracle +/-8 | 0.458257048 | 919095 / 2005632 |
| Development oracle +/-1024 | 0.169980834 | 340919 / 2005632 |
| Transfer zero shift | 0.478285648 | 319755 / 668544 |
| Transfer acquisition-v2 | 0.478040338 | 319591 / 668544 |
| Transfer v3 | 0.396353868 | 264980 / 668544 |
| Transfer oracle +/-8 | 0.458934640 | 306818 / 668544 |
| Transfer oracle +/-1024 | 0.163344522 | 109203 / 668544 |
| Holdout zero shift | 0.477585619 | 319287 / 668544 |
| Holdout acquisition-v2 | 0.477738189 | 319389 / 668544 |
| Holdout v3 | 0.384143751 | 256817 / 668544 |
| Holdout oracle +/-8 | 0.456975158 | 305508 / 668544 |
| Holdout oracle +/-1024 | 0.166802783 | 111515 / 668544 |

All seven frozen success checks passed: both holdout improvements exceeded
`0.01`, at least `10%` of the oracle-1024 gap was recovered, transfer improved
in the same direction, correct-record v3 beat its wrong-record control by more
than `0.01`, edge accounting passed, and the runtime signature contains none of
the forbidden truth, SNR, index, filename, or group inputs.

## Frozen experiment

Development pooled three previously exposed acquisition-v2 cohorts: +20 dB
indices 0--63, +10 dB indices 256--319, and +16 dB indices 640--703. It had
2304 waveform records and 2005632 truth bits. Truth was allowed only here to
fit profile constants and select one estimator.

Before opening new record-level data, the implementation and config were
frozen. Transfer used +14 dB indices 512--575; holdout used +18 dB indices
768--831. Each contained 768 waveform records and 668544 truth bits. The code
opened transfer first and then holdout once, without tuning between them.
These are record-disjoint splits, but the dataset and group-level full-run
aggregates had already been seen. This is therefore not a pristine external
validation claim.

Provenance correction: the preregistration's `frozen_at_utc` midnight value is
a non-authoritative date placeholder, not the exact event time. The original
file is retained byte-for-byte to preserve its pre-holdout hash. Filesystem
ordering is preregistration `19:25:43.854Z`, development artifact `19:41:07.376Z`,
freeze lock `19:41:31.668Z`, and initial evaluation artifact `19:50:17.179Z` on
2026-09-02 UTC. The evaluation JSON records this correction explicitly.

The frozen profile shifts are:

| Profile | Shift (bits) | Holdout v3 BER |
|---|---:|---:|
| BPSK 100 kBd | 0 | 0.467073171 |
| BPSK 250 kBd | -45 | 0.329742432 |
| BPSK 500 kBd | -26 | 0.294158936 |
| GMSK 100 kBd | 0 | 0.472942073 |
| GMSK 250 kBd | 2 | 0.483703613 |
| GMSK 500 kBd | 2 | 0.487854004 |
| OQPSK 100 kBd | 0 | 0.456669207 |
| OQPSK 250 kBd | 0 | 0.469894409 |
| OQPSK 500 kBd | -58 | 0.451385498 |
| QPSK 100 kBd | 0 | 0.461089939 |
| QPSK 250 kBd | -90 | 0.326675415 |
| QPSK 500 kBd | -52 | 0.237495422 |

The improvement is concentrated in BPSK 250/500 kBd, OQPSK 500 kBd, and QPSK
250/500 kBd. GMSK does not improve and remains a useful negative control.

## Boundary-family ablation

The frozen bounded development grid tested all required families. Best results
within each family were:

| Development family | Best BER | Selected parameters |
|---|---:|---|
| Deterministic profile delay | 0.393690867 | one whole-symbol shift per modulation/rate |
| Bounded combination | 0.402648641 | power, 4 symbols, threshold 0.25, residual +/-2 symbols |
| Cyclostationary boundary | 0.478475114 | 4 symbols, threshold 0.25 |
| Power boundary | 0.479190599 | 4 symbols, threshold 0.25 |

The dynamic residual does not generalize better even on development, so it was
not selected. This also means v3 has not solved arbitrary burst-start
acquisition; it has calibrated stable per-profile record offsets in RML24.
Without the official waveform generator, the offset cannot be attributed
uniquely to receiver DSP delay rather than dataset record construction.

## Controls and accounting

On holdout, the same v3 shift scored against the next record's truth has BER
`0.491874880`, versus `0.384143751` on the correct record. The wrong-record
truth-aided +/-1024 control is `0.461588168`, and the random-candidate +/-1024
control is `0.461021264`. These controls distinguish the fixed-delay gain from
wide-oracle hypothesis multiplicity.

Missing truth edges count as errors. Holdout v3 has 18096 missing truth bits
and 17252 excess candidate bits; the reported BER already includes every
missing edge as an error. For comparison, the holdout +/-1024 oracle has 58810
missing truth bits. All partition, error-bound, per-record, and score-row checks
passed for both transfer and holdout.

Waveform recovery uses the opt-in `carrier_timing_v2` path. Truth remains in the
final scorer for binary polarity and QPSK/OQPSK D4/variant ambiguity resolution.
Consequently this is a truth-independent **origin estimator**, not yet a fully
blind end-to-end BER receiver.

## Opt-in interface and reproduction

The production interface is `Rml24BlindOriginV3` in
`src/telemetry_yield/rml24_blind_origin.py`. It is not imported or invoked by
the default physical plugin. It fails closed outside the evaluated 2048-sample,
1 MHz RML24 records and the twelve tested modulation/rate profiles. It does not
claim other modulations, arbitrary capture lengths, packet yield, or SatNOGS
comparability.

Frozen hashes before transfer/holdout:

- preregistration: `4a3d1d8a14e6c35ba7f300fc40e18b667f694c91a358f7fd1b4a0911f60a399b`
- implementation: `94db23f1c7028c656939dfba5f500eb42e7ae6a61c67627c170b9f31b7c58496`
- config: `74e49f1f236e8636ff56d1df5d83c293a87280008211f72c49b6c7e825fb0b61`
- manifest: `dba5fd0b840c9835967963179ba2065baefad9a32985ac33a2f6b2b63e05a2ef`
- carrier/timing dependency: `c2ffb75a8ff03098f08e604c1170434dffa931f0a262c9e7d473c2c051f3327d`
- opt-in production interface: `7d39792935c09efe366a380b27974c873d2f1afe98d3cee188e4c207bb2bd5b4`

Observed execution cost was approximately 4.5 minutes for development and 6.1
minutes for transfer plus holdout on one CPU core. Resident memory observed
during the runs stayed below approximately 150 MB. These are observational,
not instrumented benchmark timings.

Exact staged commands:

```text
rtk env PYTHONPATH=src python3 work/rml24/run_blind_origin_v3.py develop --manifest work/nature-dataset/shards/manifest.json --prereg reports/rml24-blind-origin-v3-prereg.json --output reports/rml24-blind-origin-v3-development.json --config-output reports/rml24-blind-origin-v3-frozen-config.json
rtk env PYTHONPATH=src python3 work/rml24/run_blind_origin_v3.py freeze --manifest work/nature-dataset/shards/manifest.json --prereg reports/rml24-blind-origin-v3-prereg.json --config reports/rml24-blind-origin-v3-frozen-config.json --lock reports/rml24-blind-origin-v3-freeze-lock.json
rtk env PYTHONPATH=src python3 work/rml24/run_blind_origin_v3.py evaluate --manifest work/nature-dataset/shards/manifest.json --prereg reports/rml24-blind-origin-v3-prereg.json --config reports/rml24-blind-origin-v3-frozen-config.json --lock reports/rml24-blind-origin-v3-freeze-lock.json --output reports/rml24-blind-origin-v3-evaluation.json
```

Focused tests:

```text
rtk .venv/bin/pytest -q tests/test_rml24_blind_origin_v3.py tests/test_rml24_acquisition_v2.py tests/test_rml24_physical_plugin.py
35 passed in 1.81s
```

Machine-readable artifacts are
`reports/rml24-blind-origin-v3-development.json`,
`reports/rml24-blind-origin-v3-frozen-config.json`,
`reports/rml24-blind-origin-v3-freeze-lock.json`, and
`reports/rml24-blind-origin-v3-evaluation.json`.
