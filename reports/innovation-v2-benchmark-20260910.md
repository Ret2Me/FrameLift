# Innovations v2: implementation and controlled OLD20 benchmark

## Outcome: completed, with no incremental codec-pool gain

All 20 original-OGG regressions and all 100 five-arm shared-PCM runs completed.
The new pooled-codec module added **zero** PDUs over the previous innovations
receiver. It remains experimental and opt-in: neither an improvement in reception
nor a reason to replace the existing production path has been established.

On the identical shared-PCM development comparison, innovations v2 recovered
138 observation/PDU pairs, versus Dire Wolf's 86, gr-satellites' 44, and the
older full native progressive receiver's 139. Its advantage over the external
profiles already existed in v1; it is not a gain produced by this new module.
The original-OGG regression is reported separately below. The fresh 500-recording
acquisition has not yet produced a frozen cohort or DSP results.

## Completed five-arm results

| Receiver | Observation/PDU pairs | Globally distinct PDUs | Observations with a PDU | Mean child wall seconds/observation |
|---|---:|---:|---:|---:|
| Innovations v2, pooling enabled | 138 | 121 | 6/20 | 137.23 |
| Frozen innovations v1, no codec | 138 | 121 | 6/20 | 128.12 |
| Full native progressive v3 | 139 | 124 | 7/20 | 328.26 |
| Dire Wolf, no bit repair | 86 | 83 | 7/20 | 3.67 |
| gr-satellites, stock specified profile | 44 | 43 | 4/20 | 4.15 |

Every denominator is 20; no observation or arm was failed, incomplete or omitted.
The end-to-end runner timestamps span approximately 54 minutes 29 seconds
(21:26:10–22:20:39 UTC). Means above are individual child wall times on the shared
concurrent host, not whole-campaign duration or isolated latency.

V2 recovers 60.47% more observation/PDU pairs than Dire Wolf and 213.64% more than
this gr-satellites profile, but **0.72% fewer** than the full native progressive
receiver. These descriptive ratios are not an equal-compute comparison. The
20 recordings form only four overlapping-pass clusters; the predefined analysis
therefore withholds bootstrap intervals. They are not 20 independent pass trials.

Exact sets matter more than the total:

| V2 compared with | Added observation/PDU pairs | Missed observation/PDU pairs | Globally new PDUs absent from comparator everywhere | Comparator-only global PDUs |
|---|---:|---:|---:|---:|
| Prior innovations v1, no codec | 0 | 0 | 0 | 0 |
| Full native progressive v3 | 8 | 9 | 4 | 7 |
| Dire Wolf | 53 | 1 | 39 | 1 |
| gr-satellites | 94 | 0 | 78 | 0 |
| Union of both external profiles | 53 | 1 | 39 | 1 |

Thus v2 is not a lossless replacement even for the measured Dire Wolf profile.
The 47 globally distinct PDUs added somewhere relative to Dire Wolf must not be
called 47 globally new PDUs: only 39 were absent from Dire Wolf in every recording.

The v2 total is 36,432 PDU bytes across observation/PDU pairs and 31,944 bytes
after global deduplication. Its 39 globally new PDUs relative to the external
union contain 10,296 PDU bytes, while one 264-byte external PDU is missed.
All these quantities include AX.25 headers and exclude FCS; they are not counts
of application-only telemetry bytes. This gain belongs to the existing receiver
combination, not the new pooled-codec module.

Only the following seven observations produced any accepted PDU in any arm;
the other thirteen completed with zero in all five arms:

| Observation | v2 | v1 no codec | Full v3 | Dire Wolf | gr-satellites |
|---|---:|---:|---:|---:|---:|
| 14967361 | 0 | 0 | 2 | 1 | 0 |
| 14967362 | 4 | 4 | 3 | 1 | 0 |
| 14967367 | 8 | 8 | 8 | 4 | 0 |
| 14967376 | 49 | 49 | 52 | 39 | 27 |
| 14967393 | 22 | 22 | 20 | 13 | 8 |
| 14967413 | 48 | 48 | 45 | 27 | 8 |
| 14967432 | 7 | 7 | 9 | 1 | 1 |

The shared-PCM v2 arm accepted eight pooled calibration applications but added
zero PDUs versus both its same-run old-lane union and the frozen no-codec v1.
Matched weighted/unweighted comparisons also had zero additions and zero losses.
Its summed child CPU was 5,007.27 seconds versus v1's 4,878.98 seconds, with maximum
single-process RSS 617,264 KiB versus 545,452 KiB. This run supplies no evidence
that the extra computation is worthwhile. Relative overhead is not isolated from
the codec-source binding, metadata extraction or concurrent host effects.

An explicitly **post hoc, set-only** union of the two native receivers would
contain 147 observation/PDU pairs and 128 globally distinct PDUs. This identifies
complementarity and a possible next integration, not an implemented 147-PDU
receiver, new demodulation algorithm, equal-budget gain or heldout result. No
portfolio latency is claimed.

## Implemented change

The opt-in Rust receiver now supports pooled codec calibration: it combines
residual evidence from multiple disjoint windows containing independently
received-CRC-valid frames, then tests whether codec metadata predicts local
noise variance. Target windows and their one-second guards are excluded from
fitting. Source selection is geometric and deterministic, with explicit
nonoverlap, received-frame deduplication and memory limits. Training-only scales
and the pre-existing predictive acceptance thresholds are preserved.

The weighted sequence detector runs only after that gate passes. Previous
decoder lanes remain in the reported union. No reference payload enters search;
no CRC-guided bit repair, manufactured FCS or recursive pseudo-label training is
used. The codec features are proxies, not measured quantization noise. The
residual check is conditional on source frontend normalization and CRC-selected
timing, not an untouched raw-waveform holdout.

Implementation and usage:

- `rust/codec_pool.rs`: bounded source selection, fitting and provenance.
- `rust/innovation_audio.rs`: optional lane and exact original-OGG/shared-WAV
  binding.
- `examples/innovation_audio_probe.rs`: `--codec-sideinfo --pooled-codec`, with
  optional `--codec-source ORIGINAL.ogg` for canonical shared WAV input.
- `examples/innovation_pool_development.rs`: fixed OS observation workers; this
  corrects the nested-Rayon work-stealing concurrency overrun in an explicitly
  interrupted earlier development attempt.
- `examples/innovation_benchmark_run.rs`: frozen five-arm runner, durable commits,
  per-arm process costs, independent native FCS verification and explicit
  incomplete-observation handling.

The main production defaults have not changed. This experimental branch is
mono post-FM FSK/GMSK audio with native AX.25 G3RUH validation, not a general
PSK/CCSDS decoder or a replacement for a full IQ station chain.

## Completed original-OGG regression

On the previously exposed 20-recording development set, the frozen v1 and v2
original-OGG paths both recovered 140 observation/PDU pairs and 121 globally
distinct FCS-stripped PDUs. Exact sets matched on every observation: **zero
additional and zero lost PDUs**. This validates preservation, not improved yield.

Eight of 867 pooled calibration decisions passed, reusing two fitted source
configurations across eight target windows in one observation. Their 384 weighted
trials recovered two distinct PDUs; the matched unweighted lane recovered exactly
the same two. Predictive model acceptance therefore did not generate additional
telemetry. The separate trace audit checked 4,749 source/target relationships and
found no recorded geometry, SHA, overlap, deduplication or attribution violations.

The fixed 4-worker, 2-DSP-thread run took 743.573 seconds wall time. It is not an
isolated latency measurement or proof of a speedup over previous campaigns.
See `reports/innovation-pool-development-20260910.md` for the complete evidence.

## Five-arm benchmark protocol

The cohort contains all and only the original 20 observations from September 10,
2026, in their frozen order. All are the CANVAS GMSK-9600 profile. Their previous
outcomes were already known: this is a **development** comparison, not a fresh
holdout, random sample of the whole SatNOGS archive, or scientific novelty test.

Each arm receives the same mono 48 kHz PCM16 WAV, decoded once from the original
Vorbis without resampling, downmix, gain manipulation or hand-selected cropping.
Bitexact FFmpeg flags omit container metadata incompatible with installed
Dire Wolf `atest`; a separate smoke confirmed unchanged numeric PCM samples.
V2 additionally receives original codec metadata, explicitly disclosed as extra
treatment information, and independently recreates and hashes the complete WAV
before accepting that metadata binding.

Arms:

1. `innovation_v2`: the new experimental receiver with codec pooling enabled.
2. `innovation_v1_no_codec`: frozen prior innovations receiver without codec
   information, not the entire previous codec-aware OGG stack.
3. `progressive_v3`: frozen full earlier native progressive receiver.
4. `direwolf`: installed `atest -B 9600 -F 0 -h`, without bit repair.
5. `gr_satellites`: installed stock FSK-9600 AX.25 G3RUH profile; not a recreated
   historical full `gr-satnogs` IQ chain.

Four observations run concurrently, with two native DSP threads per observation
and a fixed cyclic arm order. The outer arm limit is 900 seconds; the progressive
worker receives 880 seconds. Incomplete searches are not interpreted as zero
frames. Exact PDUs exclude FCS and are deduplicated per observation and globally.
Native received FCS is independently rechecked. External FCS is decoder-attested
and stripped by that decoder; it is never fabricated for the audit.

Cost includes process startup; the shared audio conversion is recorded
separately. The host is shared and an auxiliary low-priority metadata-recovery
build occurred during this run. These are neither isolated nor equal-compute
timings. The original-OGG/float32 regression and shared-PCM16 results must not be
mixed, and differences between them are not by themselves a controlled estimate
of a quantization effect.

## Fresh-cohort status

Outcome-blind acquisition aims at 500 previously unexposed observations with
station/day stratification and explicit exposure exclusions. The actual API
field `ground_station` was corrected before any fresh waveform selection.
Recovery preserves and verifies the previous metadata prefix and requires full
interval EOF before selecting waveforms. It does not turn a partial prefix into
a new cohort.

At the recorded recovery snapshot, 119 pages / 2,975 metadata rows were preserved.
Another HTTP 429 at 22:03:46 UTC required a 3,226-second wait, approximately until
22:57:33 UTC. The bounded user service
`telemetry-innovation-acquire-20260910-v4.service` honors that delay. It only
acquires metadata/OGGs; it does not launch a fresh DSP benchmark. See the
acquisition audit for service limits, receipts and source hashes.

## Reproducibility and remaining qualification

Frozen binaries, source archives, focused tests and exact commands are recorded
in `work/innovation-benchmark-20260910-v2`. The frozen OLD20 comparison is in
`work/innovation-benchmark-old20-20260910-v2/comparison`. The ordered observation
ID hash is `f7ed5dc3888052f404c81958ba769541e4a9558f4b8909762923ea2e4956ee67`.

After completion, the frozen runner passed `--resume --analyze-only` against all
20 observations. It revalidated runtime/source/arm identities and common input,
and reproduced every summary field except the deliberate update timestamp. The
SHA256 digest of the 100 `sha256sum` commit-pointer lines remained
`f3d5e846c4cd1cb77a379be02b17cc2d3ce07be997309ca7c93989c4c66eddf5`.
No DSP rerun or retry was required. Final summary SHA256:
`2417b2bdbbe2c003d0205f5e9d42548054949c4d4b3e35c921420bf4af150ce6`.
Manifest SHA256:
`e7dd7bef52ac3b866b1581962892cb9f86f0a7f3c49998b7c9ff3c3f899d13d7`.
Frozen cohort SHA256:
`350352c83353d846c3260ff9207f5343b019f514748c66d672f3135782323485`.

Reanalysis command, run from the repository root:

```sh
work/innovation-benchmark-20260910-v2/bin/innovation_benchmark_run \
  --acquisition work/innovation-benchmark-old20-20260910-v2/acquisition \
  --output work/innovation-benchmark-old20-20260910-v2/comparison \
  --improved work/innovation-benchmark-20260910-v2/bin/innovation_audio_probe \
  --workers 4 --threads 2 --decoder-seconds 900 --acquisition-wait-seconds 0 \
  --resume --analyze-only
```

Focused tests cover pool isolation and normalization, original-Vorbis/PCM16
binding and mutation rejection, fixed development aggregation, runner identity
and state checks, incomplete denominators, common PCM and bootstrap behavior.
The final runner's 24 tests passed; the pool module's 10 tests passed. The complete
library suite was not rerun against this final freeze. A synthetic five-arm smoke
completed and resumed without changing its five committed arm records; it is
compatibility evidence, not field-performance evidence.

This work does not establish publication readiness, scientific priority,
archive-wide generalization, zero false positives, real-time three-second
latency, or production deployment qualification. A fresh frozen cohort, matched
ablation, independent noise/negative controls and broader modulation/FEC coverage
remain necessary before broader claims.
