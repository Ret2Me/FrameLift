# Results: probabilistic satellite reception and scheduling (observation-planning-publication-v4)

## Cohort and integrity

The immutable snapshot yielded 55870 raw observations and 47039 normalized observations. The primary signal task contains 19274 labeled observations from 49 satellites and 440 stations. The conditional decode proxy contains 13588 observations. The predeclared count gate was **pass**.

The common outcome-blind sampling panel contains 5 UTC intervals totaling 87.0 days per satellite. Normalization retained 138 distinct transmitters: 19223 rows matched the transmitter used to construct the satellite sampling frame and 27816 came from other transmitters on the same selected satellites.

Counted exclusions were `{"contradictory_signal_and_demoddata": 1214, "failed_job": 6811, "missing_station": 300, "unconfirmed_transmitter": 502}`. The independent arithmetic and hash audit passed 2233 checks with 0 failures.

## Reception-probability result

The sole confirmatory comparison is temporal-holdout `full_logit` versus `global_rate` for `signal_present`. Conditional decode, composed end-to-end reception and its scheduler replay, transfer, station-cluster, alternative-model and ablation intervals are secondary sensitivity analyses without a family-wise multiplicity claim. The composed endpoint was added during documented input-level hardening before V4 model fitting or performance evaluation and is not retroactively promoted to confirmatory status.

The full frozen model met the predeclared useful-effect criterion: at least 5% relative Brier improvement over the global rate and a paired 95% cluster-bootstrap interval wholly below zero.

`signal_present` temporal holdout: global-rate Brier 0.258142, full-model Brier 0.184285, relative reduction 0.2861, paired difference 95% CI [-0.139295, -0.029734].

`signal_present` station-cluster sensitivity: 166 held-out station clusters, paired 95% CI [-0.096161, -0.052986], useful-effect gate=True.

`signal_present` external-satellite: 1010 test observations (4040 model predictions) from 9/10 predeclared unseen groups; global-rate Brier 0.231316, full-model Brier 0.250719, paired difference 95% CI [-0.025080, 0.046466].

`signal_present` external-station: 952 test observations (3808 model predictions) from 35/106 predeclared unseen groups; global-rate Brier 0.293446, full-model Brier 0.238957, paired difference 95% CI [-0.086652, -0.026417].

`decode_success_given_signal` temporal holdout: global-rate Brier 0.210143, full-model Brier 0.142555, relative reduction 0.3216, paired difference 95% CI [-0.110587, -0.058171].

`decode_success_given_signal` station-cluster sensitivity: 265 held-out station clusters, paired 95% CI [-0.083085, -0.051212], useful-effect gate=True.

`decode_success_given_signal` external-satellite: 598 test observations (2392 model predictions) from 6/10 predeclared unseen groups; global-rate Brier 0.222699, full-model Brier 0.265079, paired difference 95% CI [-0.124562, 0.078305].

`decode_success_given_signal` external-station: 1305 test observations (5220 model predictions) from 50/106 predeclared unseen groups; global-rate Brier 0.210218, full-model Brier 0.194494, paired difference 95% CI [-0.033499, -0.000074].

The operational end-to-end quantity is evaluated separately as `P(signal) * P(decode artifact | signal)`. Its fixed 0.5-threshold classification figures are descriptive and do not replace probability calibration metrics.

End-to-end packet reception temporal: n=3465, full-model Brier 0.121749, AUROC 0.8022, 0.5-threshold accuracy 0.8306, sensitivity 0.5071, specificity 0.8843, positive predictive value 0.4209.

End-to-end packet reception satellite: n=757, full-model Brier 0.066291, AUROC 0.6929, 0.5-threshold accuracy 0.9300, sensitivity 0.0000, specificity 1.0000, positive predictive value NA.

End-to-end packet reception station: n=647, full-model Brier 0.208986, AUROC 0.7212, 0.5-threshold accuracy 0.6600, sensitivity 0.0138, specificity 0.9883, positive predictive value 0.3750.

`signal_present` full-versus-geometry ablation: relative Brier reduction 0.2794, paired 95% CI [-0.134636, -0.028721], useful-effect gate=True.

`decode_success_given_signal` full-versus-geometry ablation: relative Brier reduction 0.2935, paired 95% CI [-0.119142, -0.044596], useful-effect gate=True.

Per-satellite sample composition is in `table-cohort.csv`; complete temporal and transfer metrics are in `table-probability.csv`; end-to-end reception calibration and fixed-threshold confusion matrices are in `table-reception.csv`; direct full-model ablations are in `table-ablation.csv`. Every bootstrap draw remains in the machine-readable evaluation artifacts.

## Frozen successor-campaign model

The content-bound successor-campaign artifact froze `signal_present`=operational_logit, `decode_success_given_signal`=operational_logit. Optional weather/hardware terms enter only where both predeclared cluster gates passed; this refit is not evaluated on the reused historical holdout.

## Scheduling result

The held-out end-to-end SatNOGS replay is non-informative for scheduler gain because the already-admitted Network jobs contain no natural same-station conflicts. It is retained as a degeneracy check, not evidence of equality or improvement.

The MILP matched independent exact solvers on every single-receiver interval instance and every crossed station/satellite contention instance. This verifies implementation exactness only; it is not empirical yield evidence.

The month-like engineering instance contained 1200 candidates and 467 selected opportunities; the proven-optimal solve took 0.056 s in the captured environment.

The declared mild and stress correlated-risk simulations produced mean yields 32129.29 and 23825.09 synthetic sample units, respectively. These values are scenario sensitivity, not forecasts.

## TLE refresh result

The audit compared 11108 successive distinct embedded-TLE transitions; 49 had no same-pass match for either element set and 1 encountered an SGP4 propagation error, with both states reported as indeterminate. Shifts are relative to the newer Network planning geometry evaluated at capture-time client coordinates when available and explicitly flagged current-coordinate fallbacks otherwise; TLE epoch is only a proxy for availability time and neither quantity is physical orbit truth.

## Scope and limitations

`without-signal` is evidence of no visible satellite signal in a vetted waterfall, not proof that the spacecraft did not transmit. The decode endpoint is the presence of an uploaded demodulation artifact conditional on a confirmed signal, not strict frame validity. Observation-cached schedule geometry is used by the probability model. Capture-time client coordinates support historical weather and diagnostic range/Doppler; current API station-coordinate fallbacks and all coordinate-derived range/Doppler remain excluded from probability models. Historical terrestrial weather was time-aligned for 46471 of 46471 rows carrying a capture-time client location; 568 rows with only current API station coordinates were ineligible for that join. Kp was aligned for 47039 rows from independently archived environmental sources. These retrospectively retrieved values support an association/ablation analysis; they are not misrepresented as forecasts that were available when the original SatNOGS jobs were scheduled. The public Network API exposes the current station antenna profile but not a versioned antenna history, so current hardware was not backdated into historical observations. Capture-time client metadata supplied receiver configuration for 46471 rows, including an RF receiver-gain setting for 39350; this gain and the reported receiver port are not physical antenna gain/type and remain audit-only for the same observation. Prospective plans instead freeze the public antenna type and frequency ranges before each decision. The study does not establish transfer to proprietary stations.

SatNOGS source data are identified as CC BY-SA 4.0 by the project. External redistribution remains subject to a human attribution/share-alike review. See the frozen protocol, methods, data card, manifests and independent audit shipped with this package.
