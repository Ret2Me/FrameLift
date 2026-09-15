# Preliminary receiver methods and claims audit

Audit date: 2026-09-12. This is a source/read-only evidence audit, not a new
decoder experiment. Paths below are relative to the project root
`/home/ubuntu/telemetry-yield`; line numbers refer to inspected current source.
The executable/source identities frozen for the campaign, rather than later
working-tree changes, are authoritative for final reproducibility.

## Snapshot and exact-set check

The paper snapshot in `evidence/summary.json` has cutoff
`2026-09-12T22:52:19.141Z`: 189 complete cases out of 266 planned recordings.
Its reported primary endpoint is 4,541 progressive PDUs against 2,998 in the
union of the controlled Dire Wolf and gr-satellites replays: 1,547 additions,
4 misses, and net 1,543 (+51.4676%). These are distinct full AX.25 UI PDUs
within each observation, summed across observations, not globally distinct
spacecraft messages or application-payload bytes.

Independently comparing sorted, deduplicated `payloads.innovation_v2` and
`payloads.innovation_v1_no_codec` in **every one** of the 189 records in
`evidence/observations.json` found exact equality in all 189 cases. Both arms
total 4,703 PDUs. This is stronger than comparing their aggregate counts.
The optional codec and pooled-codec paths therefore provide **zero incremental
union PDU recovery over the no-codec receiver in this snapshot**. This does
not mean the paths never execute or never independently decode an already
recovered frame; lane-level provenance and matched ablations answer that.

The innovation-v2 union versus progressive is not nested: per-observation
set differences sum to **214 additions and 52 misses**, net +162. Switching
the primary receiver after observing these totals would be a post-result
choice. The current primary endpoint must remain progressive, with the
innovation arms reported separately and any post-hoc union clearly labeled.

The confirmed-waterfall-signal subset contains 16 of 24 planned cases:
136 primary PDUs versus reference-union 87, with 49 additions and no misses.
This label is not the same as archive overall status, nor an independently
measured RF detection probability. Unknown waterfall labels are not negatives.

The snapshot itself warns that this is a repeated historical evaluation after
a performance restart, not a never-exposed holdout. Final artifact-level
campaign analysis is pending. These caveats belong in the manuscript.

## Primary receiver: progressive_v3

This is an offline, resumable post-FM audio receiver portfolio. It is not the
codec-conditioned innovations example and is not a universal IQ demodulator.

| Component | Verified implementation | Source evidence |
|---|---|---|
| Windowing | 6-second windows, 3-second hop | `rust/adaptive.rs:27` |
| Frontend diversity | Legacy FIR/DC frontend; square-pulse filtering, four-boxcar DC rejection and causal RMS normalization | `rust/adaptive.rs:26`; `rust/tracking.rs:7` |
| Fixed timing | Five clock errors −500, −100, 0, +100, +500 ppm and 32 phases, with waveform-derived ranking/threshold | `rust/dsp.rs:38`; `rust/dsp.rs:754` |
| Gardner diversity | Loop bandwidths 0.02/0.06 and eight initial phases | `rust/adaptive.rs:342` |
| Fast prefix | Eight fixed timings and two Gardner configurations per frontend; remaining baseline completes the same bank | `rust/adaptive.rs:546`; `docs/progressive-decoder.md` |
| Native anchors | Received-FCS-checked AX.25 UI spans under plain NRZI and NRZI/G3RUH, retaining physical symbol coordinates | `rust/anchors.rs:29`; `rust/anchors.rs:76` |
| Channel estimate | Ridge-regularized centered three-tap model plus bias; at least 128 distinct guarded training symbols; quality gate | `rust/sequence.rs:21`; `rust/sequence.rs:134` |
| Sequence detector | Exact deterministic four-state Viterbi MLSE, free endpoint states, full traceback; gains 0.75/1/1.5 | `rust/sequence.rs:259`; `rust/adaptive.rs:33` |
| Nearest anchor | Same frontend, within 60 seconds, disjoint source/target windows with one-second guard | `rust/adaptive.rs:590` |
| Supplemental timing | Eight transferred-clock phases plus eight local timings, exact duplicates removed | `rust/adaptive.rs:617` |
| Blind branch | Five-start decision-directed MLSE/least-squares alternation, bounded to five iterations, waveform validation | `rust/progressive_channel.rs:17`; `rust/progressive_channel.rs:153` |
| Multi-anchor branch | Local reliability/time weighting and robust channel-shape compatibility, at most 16 models | `rust/progressive_channel.rs:22`; `rust/progressive_channel.rs:302` |
| Scheduling | Frozen quick anchors for early branches, frozen complete-baseline anchors for final branches | `rust/progressive.rs:203`; `rust/progressive.rs:245` |
| Exact reuse | f64 window frontend/timing cache, reusable sequence workspaces, no cache-driven hypothesis pruning | `rust/adaptive.rs:43`; `rust/progressive.rs:389`; `rust/sequence.rs:240` |

The symbol-rate model is

`y[n] = bias + h[-1] x[n-1] + h[0] x[n] + h[1] x[n+1] + e[n]`,

with bipolar `x[n]`. It is a bounded ISI approximation, not a full coherent
GMSK waveform model. Viterbi here performs sequence estimation, not
convolutional error-correcting-code decoding.

Archive/reference payloads are not supplied to the decoder. CRC-guided bit
repair is not performed. Supplemental recovered frames and blind models do
not recursively become anchors (`rust/adaptive.rs:1`). The receiver retains
its own baseline union; this does not imply retaining every external decoder
frame. A waveform-fit gate is not proof that a signal or spacecraft exists.

Quick/deep/full choose an execution budget for the same finite bank. The
nominal three-second quick budget is not a hard real-time return guarantee.
Checkpoint identities bind input, executable and algorithm policy; changing
cache budget or worker count is not changing the hypothesis bank.

## Separate experimental receiver: innovation_v2

`docs/innovation-receiver.md:19` states that this opt-in library/example is not
integrated as a checkpointable progressive task. Its additive union preserves
the adaptive baseline, **not** all blind/multi-anchor progressive stages.

The explicitly separate lanes are matched-white MLSE, innovations MLSE,
codec-innovations and pooled-codec-innovations
(`rust/innovation_audio.rs:438`). Residual AR orders 0–2 are fitted with
disjoint waveform validation and bounded stable coefficients
(`rust/innovation.rs:127`). The finite-memory metric is documented exactly at
`rust/innovation.rs:332`:

`sum_n (e[n] - sum_j a[j] e[n-j])^2 / v[n]`.

AR(0) with constant variance delegates to the original detector, preserving
its arithmetic and tie behavior. Optional variance multipliers are bounded
to 0.01–100. Parameters come from disjoint trustworthy source receptions, not
target reference bits.

Codec features are log encoded bits per decoded sample and an adjacent
decoded-frame-length transition indicator (`rust/codec_reliability.rs:43`,
`:530`). They are **proxies**, not known compression error, a complete parsed
Vorbis state, or oracle reliability. Feature coordinates use cumulative
decoded-frame lengths; packet timestamps need not equal PCM boundaries
(`rust/codec_reliability.rs:1`). A held-out residual-likelihood gate accepts
the variance regression (`rust/codec_reliability.rs:468`).

Pooling requires at least two disjoint source windows with distinct native
received frames and source/frontend/geometry validation
(`rust/codec_pool.rs:85`). Training-only RMS normalization is explicit
(`rust/codec_pool.rs:50`). Failed calibration disables the optional weighted
lane, not the entire observation.

Only innovation-v2 receives `--codec-sideinfo --pooled-codec --codec-source`
in the five-arm runner (`examples/innovation_benchmark_run.rs:366`). All arms
receive the shared numerical PCM16 WAV; innovation-v2 additionally receives
original OGG metadata. The common conversion is `pcm_s16le`
(`examples/innovation_benchmark_run.rs:1872`), not the standalone progressive
CLI's float32 OGG preparation. Individual arm times exclude that common
conversion and are shared-host elapsed times, not isolated throughput.

## Input and qualification boundary

The main scientific population is historical CANVAS 9600-baud GMSK/G3RUH
AX.25 UI post-FM Ogg/Vorbis audio. This is not a raw-IQ comparison and not a
representative all-modulation SatNOGS benchmark. The reference union is a
controlled local replay of Dire Wolf and gr-satellites, **not** the native
SatNOGS decoder's original archived output. Strict UI acceptance is narrower
than all received telemetry. Native received FCS is rechecked independently;
external outputs strip FCS and rely on the respective decoder's internal
check. CRC is not cryptographic authenticity or proof of spacecraft identity.

Separate generic receiver paths implement BPSK/QPSK/OQPSK on complex IQ,
CSP v1/v2, AOS Issue-5, non-truncated USLP, GF256 Reed–Solomon and sparse-H
soft LDPC with specific presets. These are not the progressive audio bank
(`docs/psk-and-channel-coding.md:11`). Orbital QPSK/OQPSK yield remains
unqualified; no claim of universal baud acquisition, modulation classification
or all CCSDS coding is justified. PSK IQ paths reject mono post-FM OGG/WAV.

## Claims and ablations required before a strong final paper

Defensible preliminary framing: a reproducible, resumable multi-hypothesis
receiver recovering additional validated AX.25 UI PDUs from archived satellite
audio through complementary timing frontends and guarded channel-conditioned
sequence detection. The engineering combination and empirical study can be
contributions without claiming every component is a new algorithm.

Do not claim first/new Gardner, MLSE, AR whitening, weighted regression or
multi-hypothesis search. Do not call the system revolutionary, universally
superior, or lossless. Codec reliability is a proposed technical direction
whose current union endpoint shows no incremental recovery. Any first-use
claim requires a separately documented prior-art review.

Required controlled comparisons:

1. Frontend/timing diversity, nearest-anchor MLSE, blind branch and multi-anchor
   branch separately, with identical accepted frame criteria and search budgets.
2. Matched-white versus residual AR innovations on identical eligible windows,
   timings and anchors; do not substitute unequal receiver unions for this.
3. Codec weighting versus matched unweighted innovations, and pooled weighting
   versus its matched accepted-pool control. Report rejected/insufficient
   calibrations and eligible denominator, including zero union gain.
4. Fixed complete-bank exact-output performance comparison, plus explicitly
   labeled budgeted yield/time curves. Shared CPU, conversion, thread counts,
   reset/window schedules and comparator tuning must be visible.
5. Complete the predeclared cohort and resolve incomplete cases without scoring
   failure as zero. Retain gain and miss counts separately. Use dependence-aware
   uncertainty and preserve the exposure/restart history.
6. Noise/invalid-frame controls and mission/frame plausibility checks to qualify
   the many-hypothesis CRC search. More modulations and satellites require new
   explicitly defined populations, not extrapolation from this one.

WA8LMF TNC test results may contextualize established audio-decoder evaluation,
but are a different workload. They cannot be combined numerically with this
satellite cohort or described as a benchmark run here without actually running
the identical corpus and documenting its protocol, configuration and grading.
