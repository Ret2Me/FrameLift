# MTCube-2 cross-mission transfer v1

Verdict: **PASS**. On two preselected MTCube-2 recordings, the executable baseline recovered 4 strict AX.25 UI frames, the fixed native CELESTA-developed branch recovered 24, and the validated union contained 24. Native-only yield was 20 frames across 1/2 recordings. Null controls produced 0 strict accepts.

| Observation | Status | Baseline | Native | Native-only | Union |
|---:|:---:|---:|---:|---:|---:|
| 6215697 | good | 4 | 24 | 20 | 24 |
| 6221404 | bad | 0 | 0 | 0 | 0 |

Both recordings were selected from the frozen CAMRAS index before IQ acquisition, including the one labelled bad/without-signal. No payload references, demoddata, waterfall pixels or signal samples were inspected.
