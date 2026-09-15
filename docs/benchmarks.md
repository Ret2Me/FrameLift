# Benchmark results

[Documentation](README.md) · [Comparison scope](comparisons.md) · [Paper](../publication/decoder-paper-v2/README.md)

## Complete historical CANVAS study

**266/266 observations**, 15 UTC dates, 161 station identifiers, 34.11 hours of
recorded audio. This is a previously exposed single-mission cohort evaluated
again after a performance-only restart, not an unseen or representative SatNOGS
holdout. The tested signal is mono post-FM GMSK/FSK 9600-baud audio with AX.25 UI
packet acceptance. The frozen receiver build predates the September 14 refactor.

One common PCM16 WAV was prepared from each original OGG. No per-arm resampling,
hand-picked crop or gain adjustment was used. The codec-enabled experimental
arm also received original OGG side information; equality of waveform samples
does not mean equality of all available information.

| Arm / set | Per-observation unique PDUs | Sum of final arm wall times (s) | Mean per recording (s) |
|---|---:|---:|---:|
| Dire Wolf | 4,029 | 982.38 | 3.69 |
| gr-satellites | 2,514 | 1,047.27 | 3.94 |
| External union | 4,066 | — | — |
| Progressive P | 6,220 | 51,175.17 | 192.39 |
| Innovations I1, codec disabled | 6,420 | 38,350.82 | 144.18 |
| Innovations I2, codec/pooling enabled | 6,420 | 39,130.71 | 147.11 |

Times are concurrent-host process wall measurements, including startup but
excluding shared conversion/acquisition. Summing them is work accounting, not
campaign elapsed time. Different parallelism and host contention prevent an
isolated efficiency or equal-compute claim. P takes roughly 52 times the summed
Dire Wolf time and 49 times the gr-satellites time in this experiment.

### Progressive receiver versus the reference union

| Metric | Result |
|---|---:|
| Additional observation/PDU pairs | 2,158 |
| Reference pairs missed | 4 |
| Net additional pairs | 2,154 |
| Net increase over 4,066 reference pairs | 52.98% |
| Observations with at least one addition | 141 / 266 (53.01%) |
| Observations with at least one loss | 4 / 266 |
| Bytes in additional PDUs, excluding FCS | 402,192 |
| Bytes in missed PDUs, excluding FCS | 1,056 |

An identical packet repeated inside a recording counts once. The same packet
bytes in another recording count again. The byte totals include protocol headers;
they are **not application-only telemetry**. None of these values measures the
fraction of all transmitted packets recovered, because transmitted ground truth
is unavailable. A recording may have both additions and losses.

### Signal-labelled subgroup, kept separate

The definition is frozen archive metadata: `waterfall_status == with-signal`.
It is not selected on our successful decoding. All **24** such observations are
now complete: **174** PDUs versus **111** in the reference union, **63 added,
zero missed**, a **56.76%** net increase. Six of the 24 observations have additions.
The remaining labels are three without-signal and 239 unknown; unknown does not
mean absent signal.

This post-hoc subgroup is small and exploratory. Metadata confirms a reported
visible signal, not transmitter identity or independent ground truth. Its final
counts were rederived from all committed results and cross-checked against the
final yield analyzer, not copied from the stale progress monitor.

### Experimental result that did not improve yield

I1 and I2 produce identical observation-level packet sets in all 266 cases:
**zero additions and zero losses** from enabling the codec/pooling arm. Its
larger 6,420 count compared with P cannot be attributed to codec conditioning.
I1/I2 use a different portfolio from P, so that comparison does not isolate one
algorithmic improvement.

Against the reference union, I2 adds 2,359 PDUs and misses five. It does not
strictly dominate the reference, and its output is not a superset of P either.
The system should therefore be evaluated as a complementary recovery workflow.

### Statistical interpretation

The archived analyzer includes descriptive component-bootstrap sensitivity
intervals using 10,000 draws. At a 7,200-second grouping gap, the net percentage
interval is 43.14–63.39%, from 44 components. The analyzer explicitly marks
independence as unverified: this is **not** a confirmatory confidence statement
for a population of all satellite recordings. Station, pass, day and repeated
payload dependence, prior exposure and cohort selection remain limitations.

## Evidence and provenance

The compact [summary](../publication/decoder-paper-v2/evidence/summary.json) and
[266 per-observation rows](../publication/decoder-paper-v2/evidence/observations.json)
were exported by [paper_evidence.rs](../examples/paper_evidence.rs). The exporter
recomputed set differences from recorded exact PDU sets, matched them against
the final analyzer, and checked time sums against the cost report.

The portable bundle contains counts, source/result hashes, public source URLs,
profiles and frozen executable identities. It does **not** contain all raw
waveforms, FCS bytes, logs or decoder task artifacts. Its verification proves
bundle consistency, not independent replication of the RF experiment. The final
analyzer rechecks native received CRC and external hex/KISS sets but relies on
separate runner/control audits for full task/runtime admission. Its
`publication_ready` and `sampling_and_independence_certified` fields remain false.

## Current-build engineering regression

The separate [September 14 quality report](../reports/repository-quality-20260914.md)
records 20 identical full frames and 1,372 identical scientific tasks on one
known development recording, across the pre-refactor build and current CPU runs.
It also records 195 bit-exact FIR cases. These checks support that specific
refactor; they neither rerun the 266-recording comparison on the latest build nor
establish GPU acceleration or a new scientific gain.

See [reproduction levels](reproducibility.md) and the [remaining gates](roadmap.md).
