# Results: probabilistic satellite reception and scheduling (observation-planning-publication-v3)

## Cohort and integrity

The immutable snapshot yielded 5216 raw observations and 4789 normalized observations. The primary signal task contains 1610 labeled observations from 9 satellites and 130 stations. The conditional decode proxy contains 382 observations. The predeclared count gate was **pass**.

Counted exclusions were `{"contradictory_signal_and_demoddata": 3, "failed_job": 389, "missing_station": 27, "unconfirmed_transmitter": 8}`. The independent arithmetic and hash audit passed 1135 checks with 0 failures.

## Reception-probability result

The sole confirmatory comparison is temporal-holdout `full_logit` versus `global_rate` for `signal_present`. Decode, transfer, station-cluster, alternative-model and ablation intervals are secondary sensitivity analyses without a family-wise multiplicity claim.

The full frozen model met the predeclared useful-effect criterion: at least 5% relative Brier improvement over the global rate and a paired 95% cluster-bootstrap interval wholly below zero.

`signal_present` temporal holdout: global-rate Brier 0.188460, full-model Brier 0.054945, relative reduction 0.7085, paired difference 95% CI [-0.168670, -0.078163].

`signal_present` station-cluster sensitivity: 48 held-out station clusters, paired 95% CI [-0.157874, -0.094225], useful-effect gate=True.

`decode_success_given_signal` temporal holdout: global-rate Brier 0.460593, full-model Brier 0.101869, relative reduction 0.7788, paired difference 95% CI [-0.402545, 0.004919].

`decode_success_given_signal` station-cluster sensitivity: 39 held-out station clusters, paired 95% CI [-0.451284, -0.246854], useful-effect gate=True.

`signal_present` full-versus-geometry ablation: relative Brier reduction 0.6534, paired 95% CI [-0.147001, -0.082929], useful-effect gate=True.

`decode_success_given_signal` full-versus-geometry ablation: relative Brier reduction 0.7186, paired 95% CI [-0.346096, 0.010700], useful-effect gate=False.

Complete temporal and transfer metrics are in `table-probability.csv`; direct full-model ablations are in `table-ablation.csv`. Every bootstrap draw remains in the machine-readable evaluation artifacts.

## Scheduling result

The held-out SatNOGS replay is non-informative for scheduler gain because the already-admitted Network jobs contain no natural same-station conflicts. It is retained as a degeneracy check, not evidence of equality or improvement.

The MILP matched independent exact solvers on every single-receiver interval instance and every crossed station/satellite contention instance. This verifies implementation exactness only; it is not empirical yield evidence.

The month-like engineering instance contained 1200 candidates and 467 selected opportunities; the proven-optimal solve took 0.084 s in the captured environment.

The declared mild and stress correlated-risk simulations produced mean yields 32129.29 and 23825.09 synthetic sample units, respectively. These values are scenario sensitivity, not forecasts.

## TLE refresh result

The audit compared 1673 successive distinct embedded-TLE transitions; 6 had no same-pass match for either element set and 1 encountered an SGP4 propagation error, with both states reported as indeterminate. Shifts are relative to the newer Network planning geometry evaluated at retrieval-time station coordinates; TLE epoch is only a proxy for availability time and neither quantity is physical orbit truth.

## Scope and limitations

`without-signal` is evidence of no visible satellite signal in a vetted waterfall, not proof that the spacecraft did not transmit. The decode endpoint is the presence of an uploaded demodulation artifact conditional on a confirmed signal, not strict frame validity. Cached historical station coordinates, antenna configuration and terrestrial/space-weather snapshots are unavailable in this API cohort and were not imputed; retrieval-time coordinates and their derived range/Doppler are excluded from probability models. The study does not establish transfer to proprietary stations.

SatNOGS source data are identified as CC BY-SA 4.0 by the project. External redistribution remains subject to a human attribution/share-alike review. See the frozen protocol, methods, data card, manifests and independent audit shipped with this package.
