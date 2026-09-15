# CELESTA seven-observation confirmation v2: independent audit

Exact preregistered verdict: **FAIL_PREREGISTERED_CRITERION**. The scientific canonical-frame endpoint is **PASS**.

On the same seven untouched recordings, the executable baseline recovered 18 unique strict AX.25 UI frames and the native branch recovered 123. The native branch added 105 baseline-missed frames across 7/7 observations; the validated union contained 123. Zero and full-time-reversal controls produced 0 strict accepts.

The v2 preregistration nevertheless fails because baseline KISS files are not byte-identical between runs. Canonical strict-frame sets are identical. This post-exposure normalisation is reported as a sensitivity analysis, not as a retroactive change to the preregistered verdict.

| Observation | Baseline | Native | Native-only | Union | Raw repeat | Canonical repeat |
|---:|---:|---:|---:|---:|:---:|:---:|
| 6215698 | 2 | 18 | 16 | 18 | False | True |
| 6221405 | 5 | 17 | 12 | 17 | False | True |
| 6284801 | 3 | 20 | 17 | 20 | False | True |
| 6291502 | 4 | 18 | 14 | 18 | False | True |
| 6291503 | 1 | 17 | 16 | 17 | False | True |
| 6357041 | 1 | 17 | 16 | 17 | False | True |
| 6357042 | 2 | 16 | 14 | 16 | False | True |

Scope: mission-conditioned prospective data selection within CELESTA, not a population-random or universal SatNOGS superiority claim.
