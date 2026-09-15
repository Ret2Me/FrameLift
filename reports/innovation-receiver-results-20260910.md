# Residual / codec-conditioned demodulation: development experiment

Date: 2026-09-10. Status: implementation, all twenty field runs, both control
pilots and all five selected full-progressive comparisons complete.
No publication-readiness or scientific-priority claim is made.

## Implemented change

The opt-in Rust receiver retains the previous adaptive frame union, then
compares three matched supplemental lanes: white-noise sequence detection,
autoregressive innovations detection, and optional codec-conditioned
innovations detection. The channel/noise model comes from independently
received-FCS-valid source frames in a different window. Model fitting is
guarded and nested: neither channel nor AR fitting sees the later codec check
suffix. Target frames are never a tuning objective or a source of reference
bits, and supplemental outputs are not recursively reused for training.

The new core is in `rust/innovation.rs`, original-OGG feature extraction in
`rust/codec_reliability.rs`, and audio integration in `rust/innovation_audio.rs`.
The [experimental interface](../docs/innovation-receiver.md) is separate from
the qualified CLI and the checkpointed progressive task bank. Current scope
is mono post-FM binary FSK/GMSK audio and AX.25 UI; no general modulation/FEC
coverage was added by this experiment. The real CANVAS frames can contain
CCSDS packets inside AX.25; that is not a new CCSDS physical receiver.

## Novelty boundary

Viterbi detection with noise memory and signal-dependent variance is known
prior art, not a new algorithm invented here. See
[Kavčić and Moura, *The Viterbi Algorithm and Markov Noise Memory*](https://users.ece.cmu.edu/~moura/papers/kavcic_viterbinoisememory.pdf).

The candidate technical contribution is using evidence from the original
compressed radio recording to condition an innovations metric, with guarded
calibration and provenance. The prototype observes compressed packet density
and decoded-duration transitions; it does not parse floor/residue quantizers,
recover the uncompressed waveform, or measure exact compression error.
Vorbis transform overlap and variable packet representation motivate this
test but do not prove the proposed features are useful.
[Vorbis I specification](https://www.xiph.org/vorbis/doc/Vorbis_I_spec.html).

Scientific priority is unestablished. A colored-noise gain alone cannot be
reported as proof of codec-specific novelty. The more detailed
[design and prior-art assessment](codec-reliability-design-20260910.md) records
the approximation and calibration limitations.

## Frozen evidence and comparisons

Directory: `work/innovation-field-20260910-v1`.

| Artifact | SHA-256 |
|---|---|
| Frozen probe executable | `47493db215ba9e42e922524f13839596b02a977864802807362acbf23a4a59d0` |
| Frozen twenty-file campaign executable | `94612cd51cc3d921436bc97a1f1302aea0b2eeb5096e2bfae83bb9c0b44ba457` |
| Complete receiver source bundle | `a4adea85d28a0287b441b315e8112d616300274a5f8d4b729ec8746a8bc18a12` |
| Development protocol | `4c69fc05d11bba132d60482266e68219866c93f81b8734b10923253d6323946c` |
| Best previous complete progressive v3 executable | `633f1f4ecaf7d4617f46fda83bf0866223bc123fa5013e3146cc3e32edefbb91` |
| Twenty-file campaign summary | `43f92a6a99c3dca47855524b5aaff133d10d3c016ffe1bdf61cb5d4ac6af9b1f` |
| Post-decode Rust scorer executable | `f9af5d6ab90d91baaef8072bddcf68efc1dbfe3eac7633be24a94d661fedb128` |
| Final independent set/identity/CRC audit | `5e4e2c652cc264fd610814e596740e138cff62ebfb0cc3210a6e94b070192f8b` |

The corpus is all twenty previously inspected CANVAS OGG recordings, not a
new holdout. Original source hashes and codec versions are committed in the
campaign manifest before DSP. No parameter changes are made inside the run.
Raw reports retain per-lane byte sets, source models, target windows, timing
hypotheses, gains, rejected calibrations, skips and execution cost.

Historical Dire Wolf/gr-satellites results use shared PCM16 WAV. This experiment
uses original OGG decoded to float32, so those historical numbers are not
presented as an identical-input comparison. Any gains versus the older
adaptive interface must also be checked against the complete frozen
progressive bank on the identical original OGG. Selected gainer comparisons
are not an all-twenty progressive benchmark.

## Executed controls

The frozen symbol-level pilot contains 32 separately generated signals with
three exact known frames each, each paired with a real fixed-quality Vorbis
encode. The transmitter bits are available only to the post-decode scorer;
the model requires received-CRC anchors. Symbol timing is oracle-provided in
this first diagnostic, not estimated by a complete receiver.

| WAV condition, eight cases each | Matched white target frames | Innovations target frames |
|---|---:|---:|
| Clean | 8/8 | 8/8 |
| Colored noise | 0/8 | 8/8 |
| Clipping | 0/8 | 0/8 |
| Impulsive interference | 0/8 | 0/8 |

Colored-noise target symbol errors fell from 1004 to zero. The gain is eight
distinct synthetic frames, not real satellite data. No unexpected frame was
found in the scored target/null runs; 32 WAV null intervals were processed by
two detectors, i.e. 64 repeated analyses, not 64 independent noise signals.
The 32 compressed signals lacked anchors under the fixed-symbol-center
diagnostic, so no matched OGG detector comparison actually ran there. A
separate full-frontend/timing run on every unchanged OGG completed without
failures: **70/96 frames before and after**, and **8/32 target frames before
and after**. All difficult colored/clipped/impulsive targets remained missing.
Matched-white and innovations produced exactly the same sets; all 132 codec
calibrations were rejected, so no codec-weighted trial ran. No unexpected
frame or baseline loss was observed. These are overlapping development
controls, not independent population false-alarm qualification.

The full OGG follow-up took 373.92 s wall / 321.31 s CPU, with 54,316 KiB maximum
RSS on the shared host. Its source/codec/receiver settings were unchanged.
[Full OGG control report](../work/innovation-controls-20260910-audio-v1/RESULTS.md),
result SHA-256 `f20007bbc362483e4f65cf0d981eb8ec22f323b9d65a6df2db7314ad6ff8be92`.

[Frozen control report](../work/innovation-controls-20260910-v1/RESULTS.md).
Noise-only core tests also exercise 24 distinct signals through six detector
settings each (144 calls); these are not 144 independent exposures and do not
qualify a population false-alarm rate.

## Verification

- Stable full library run: **290 passed, 0 failed, 5 ignored**, 547.89 s on
  the shared host. An earlier simultaneous build/test run invalidated the live
  test executable and caused fixture identity/process errors; that run is not
  silently counted as successful. Repeating without concurrent rebuilds passed.
- Post-freeze style-only cleanup: all 12 innovations/integration tests passed.
  It collapses one nested validation conditional and annotates fixed-order
  three-row elimination to preserve floating-point operation order. The field
  experiment continues using the original frozen executable/source bundle.
- Post-cleanup codec unit/integration tests, including real OGG and an actual
  Vorbis round trip: **6 passed, 0 failed**, 6.71 s.
- Clippy passed for the library and both new entrypoints with
  `-D warnings -A clippy::needless_range_loop`. The exception covers the existing
  indexed-elimination loops in `sequence.rs`; this is not an unqualified
  strict-lint pass. The new fixed-order codec elimination is locally annotated
  for the same reproducible-arithmetic reason.
- The separate Rust post-decode scorer has two passing tests for received-CRC
  rejection, malformed-frame rejection, deduplication and byte-unit accounting.
  It refuses incomplete campaigns or selected full-progressive runs and checks
  both original-source and decoded-float32 hashes for identical-input scoring.
- Formatting checks pass for the changed receiver files and new scoring/entry
  points. Repository-wide `cargo fmt --all -- --check` still reports legacy
  examples/sequence formatting; unrelated historical sources were not rewritten.

Source/target separation is offline, not causal: the nearest valid source can
occur later than the target. Several new field frames use such future anchors.
These results do not establish live three-second streaming recovery. Also,
guarding channel/noise/codec fitter intervals does not turn source calibration
into a fresh population holdout: upstream source timing and CRC-anchor
selection observe the source window. The codec acceptance score is explicitly
an internal predictive gate, not an unbiased significance test.

## Field result and decision

All twenty original OGG recordings completed, with no failed inputs. On the
same decoded float32 samples, the existing adaptive interface returned **128
observation–PDU pairs**, and its union with the new matched lanes returned
**140**, an increase of **12 (+9.375%)** across five recordings. No old adaptive
frame was removed; that preservation is by additive construction.

After deduplicating across all twenty observations, counts are **114 → 121**:
**seven additional distinct PDUs (+6.14%)**. The other five additional
observation–PDU occurrences repeat data already present elsewhere in the
existing twenty-recording union. All twelve added PDUs have 266 received bytes
including FCS, 264 bytes without FCS, and 248 bytes of AX.25 information.
Thus the globally new data is **1848 PDU bytes excluding FCS**, including
**1736 bytes of AX.25 information**. The information still includes embedded
CCSDS headers; it is not exclusively application measurement bytes.

| Observation | Existing adaptive | New additive union | AR-only additions / omissions vs matched white |
|---|---:|---:|---:|
| 14967362 | 4 | 5 | 0 / 0 |
| 14967367 | 6 | 8 | 1 / 0 |
| 14967376 | 48 | 49 | 1 / 0 |
| 14967393 | 18 | 23 | 3 / 1 |
| 14967413 | 45 | 48 | 3 / 1 |
| Remaining fifteen, including 14967432 with seven frames | 7 | 7 | 0 / 0 |
| Total | **128** | **140** | **8 / 2** |

Matched-white recovered 130 observation–PDUs and innovations 136, with eight
additions and two omissions rather than standalone dominance. Changed channel
fitting/search complements the old adaptive receiver too: not all twelve
additions can be attributed to noise memory. **All 243 source-model codec
calibrations were rejected; zero codec-weighted trials ran.** There is no
measured codec-specific contribution in this field pilot.
Of these 243 fits, 193 failed the minimum diversity/variation/energy gate and 50
failed the heldout predictive-score gate. This identifies a calibration-coverage
limitation as well as a negative predictive result; it does not establish that
all codec-derived evidence is useless.

Independent audits checked all twelve additions using bitwise received FCS,
AX.25 UI and embedded CCSDS packet structure, plus 75,744 source–target trial
guard checks with no violations. Three added receptions have exact payload
corroboration in another station's contemporaneous reception. This was
post-decode validation, not multi-station signal combining or extra training
input. Received CRC/structure/corroboration are not source authentication.
Detailed independent checks:
[14967362](../work/innovation-field-20260910-v1/audit-14967362-independent.md),
[14967367/376/393](../work/innovation-field-20260910-v1/audit-additional-field-independent.md),
[14967413](../work/innovation-field-20260910-v1/audit-14967413-independent.md).

The campaign took **1322.77 s wall (22.05 min)** and **4541.45 s CPU**, with
**842816 KiB process-lifetime peak RSS** (about 823 MiB). Shared-host concurrent
experiments were active, and RSS is not a per-recording independent peak.
There is no isolated throughput or equal-CPU superiority claim.

All five selected same-input full-progressive comparisons completed:

| Observation | Complete previous progressive v3 | Experimental union | Additional / omitted |
|---|---:|---:|---:|
| 14967362 | 4 | 5 | 1 / 0 |
| 14967367 | 6 | 8 | 2 / 0 |
| 14967376 | 48 | 49 | 1 / 0 |
| 14967393 | 20 | 23 | 3 / 0 |
| 14967413 | 45 | 48 | 3 / 0 |
| Selected total | **123** | **133** | **10 / 0** |

These are observation–PDU occurrences on **post-development selected gainers**,
not ten new globally unique payloads and not an all-twenty best-progressive
benchmark. The seven-global-PDU increase above compares the twenty-file
adaptive baseline, not an unmeasured twenty-file complete-progressive union.
Eight of these ten additional receptions are innovations-only versus matched
white; two are also found by the refitted white lane. Both original OGG and
actual decoded-float32 PCM hashes match in every selected comparison.

The [machine-readable post-decode audit](../work/innovation-field-20260910-v1/postdecode-audit.json)
retains full byte sets, the separate counting units, input/result/executable
identities and the selected-comparison qualification. The scorer refused
incomplete comparisons during execution rather than treating them as zeros.
Its global counts were independently reproduced with a separate JSON set
calculation. No new Dire Wolf/gr-satellites benchmark was run in this phase.

Deployment defaults remain unchanged. The AR and channel-refit lanes are
useful offline candidates; the codec-specific novelty hypothesis remains
unconfirmed and its branch stays opt-in with predictive rejection. Next
advancement gates are recorded in [the follow-up plan](../docs/innovation-next-experiment.md).
The next integration step is a new versioned additive progressive stage,
retaining every current full-bank result and supporting its checkpoints;
that production integration and independent holdout qualification are not
claimed complete here.
