# Corrected-rate real-IQ retrospective v2

**PASS_RETROSPECTIVE_NOT_PREREGISTERED** — developer diagnostic only.

Across 30 observations and 9 missions, the strict per-observation sum is baseline **99**, native **111**, net **+12**.

| Mission | Observations | Baseline | Native | Net |
|---|---:|---:|---:|---:|
| CELESTA | 1 | 3 | 15 | +12 |
| DELFI-PQ | 1 | 4 | 4 | +0 |
| FIREBIRD 4 | 1 | 32 | 32 | +0 |
| GRBAlpha | 1 | 0 | 0 | +0 |
| Kashiwa | 2 | 0 | 0 | +0 |
| MCUBED-2 | 1 | 55 | 57 | +2 |
| PAINANI-1 | 1 | 4 | 2 | -2 |
| RSP-03 | 21 | 0 | 0 | +0 |
| TIGRISAT | 1 | 1 | 1 | +0 |

The prior v1 result remains failed because 23/30 observations used the wrong sample-rate contract. This v2 does not silently overwrite it and is not a preregistered publication endpoint.

The native JSON binds every IQ hash, size, sample rate, and completed result. The interactive baseline KISS rerun did not retain a manifest binding argv and the input hash, so the same-IQ comparison remains an operator-backed retrospective rather than fail-closed proof.
