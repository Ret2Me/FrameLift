# Pooled codec calibration: completed OLD20 development experiment

## Outcome

The new optional pooled-codec branch preserved every previous innovations-v1
frame on all 20 original Vorbis recordings. It recovered **zero additional PDUs**.
Both versions recovered 140 observation/PDU pairs and 121 globally distinct
FCS-stripped AX.25 PDUs. No improvement in telemetry yield is established by this
change on this dataset. It remains opt-in, not a changed production default.

This is the previously exposed OLD20 **development** set, not the fresh
500-observation cohort and not a comparison with all SatNOGS modulation chains.

| Measure | Frozen v1 | Pooled v2 |
|---|---:|---:|
| Completed original-OGG observations | 20 | 20 |
| Distinct observation/PDU pairs | 140 | 140 |
| Globally distinct PDUs | 121 | 121 |
| Additional/missed exact PDUs relative to v1 | — | 0 / 0 |

The exact old-lane union in the new build equals the archived frozen-v1 union on
every recording, not merely in total count. All native frames were independently
checked against their received FCS and the common strict AX.25 UI structure.
These checks do not authenticate the transmitter or establish zero false alarms.

## What the new branch actually did

Pooling produced 867 target-window/frontend calibration decisions:

- 248 had insufficient disjoint source evidence;
- 611 were rejected by the unchanged statistical gate;
- 8 passed and enabled 384 weighted timing/gain trials.

All accepted applications occurred in observation 14967376. They reused two
distinct fitted source configurations across eight target windows; they are not
eight independent statistical experiments. Of the 384 trials, 34 emitted valid
frames, but deduplication left only two distinct PDUs. The matched unweighted
control recovered exactly those same two PDUs. The recording's full old and new
unions both contained 49 PDUs.

Thus the implementation overcomes the earlier complete lack of accepted codec
calibration on these original OGGs, but it does **not** yet improve reception.
Predictive residual likelihood and successful model activation must not be
reported as new telemetry bytes.

## Isolation and checks

The postdecode trace audit covered all 20 results, 867 calibration records and
4,749 selected source/target relationships. It found zero violations of source
SHA/sample-rate binding, same-frontend anchor identity/geometry, one-second
whole-target guards, pairwise source-window nonoverlap, frame-hash deduplication,
fit-status consistency, or accepted-pool trial attribution.

The check suffix is conditional on already selected source frontend normalization
and CRC timing; it is **not** an independently untouched source waveform. Target
windows are excluded from fitting. No reference payload enters decoder search,
and no CRC-guided bit repair or recursive training is used.

The successful fixed-worker run took 743.573 seconds wall time, 5,061.287 seconds
self CPU plus 100.087 seconds waited-child CPU; process-lifetime peak RSS was
2,644,256 KiB. Four observations ran concurrently with two DSP threads each.
These are shared-host development measurements, not isolated receiver latency.
Do not compare its wall time directly with the older sequential campaign.

An earlier attempt was deliberately stopped after nested Rayon scheduling
admitted more observations than the intended outer job limit. Its unfinished
files remain in `work/innovation-pool-development-20260910-v1` with an explicit
interruption receipt; none is counted here. The replacement uses fixed OS workers
and completed in `work/innovation-pool-development-20260910-v2`.

## Reproduction and evidence

- Result: `work/innovation-pool-development-20260910-v2/summary.json`, SHA256
  `5a7129289e6fa34e7810fd4ad1d60a8d6f642a905d9b991ec71a51579cc171f0`.
- Plan: `work/innovation-pool-development-20260910-v2/plan.json`, SHA256
  `1ffc13f80060b501cdd9d894738916cac5597330ae9bb14147007bd222c83aaf`.
- Fixed development driver SHA256:
  `e6eccc9f16ce1f4826c0ff36c65690f25d4461ff7b96629bce7c2535871b6167`.
- Improved standalone probe SHA256:
  `1ba812f746a4fa65f53625c568e75188bd0c069f6c65f86878811f2c742895b6`.
- Frozen receiver sources, updated driver sources, compiler/version and focused
  test receipts are in `work/innovation-benchmark-20260910-v2`.

The separate five-receiver benchmark uses one bitexact PCM16 WAV per observation,
including a source-bound check for the new receiver's additional original-OGG
metadata. Its counts must not be mixed with these original-OGG/float32 results.
Neither this development experiment nor the synthetic compatibility smoke
establishes scientific novelty, publication readiness or deployment qualification.
