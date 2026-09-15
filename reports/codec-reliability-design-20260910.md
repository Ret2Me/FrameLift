# Codec-conditioned innovations: design and novelty audit

Date: 2026-09-10. Status: experimental implementation; not a validated new scientific result. Owned module: [codec_reliability.rs](/home/ubuntu/telemetry-yield/rust/codec_reliability.rs). Public/default decoding policy is unchanged.

## Technical hypothesis

The proposed combination is a noise-predictive sequence metric whose local residual variance is conditioned on observable evidence from the **original compressed recording**, calibrated using disjoint, previously CRC-valid source intervals. It may improve demodulation of archival Vorbis audio if that evidence predicts the residual distortion left after channel fitting and noise prediction.

This is a hypothesis about statistical calibration, not a claim that packet sizes expose exact quantization error. Neither the original clean waveform nor unknown target bits are available to the field decoder. Decoder output must still pass the ordinary received FCS/structure checks; CRC is not an objective for coefficient tuning or bit repair.

## What is already known

Vorbis uses an overlapping transform and encoder-selected block modes, with floor/residue representations and codebooks. Its packet sizes are variable and are not an error-variance map. A decoded output block combines neighboring transform blocks. These properties motivate testing codec-conditioned residuals, but do not establish that the proposed features predict telemetry decoding errors. [Xiph Vorbis I specification, §§1.1–1.3 and4.3](https://www.xiph.org/vorbis/doc/Vorbis_I_spec.html).

Sequence estimation with ISI and noise memory is prior art. The1998 work by Kavčić and Moura explicitly extends Viterbi treatment to signal-dependent noise memory. The2010 work by Kumar, Ramamoorthy and Salapaka studies ML sequence detection under data-dependent Gauss–Markov noise. Consequently, adding an autoregressive prediction filter or a nonconstant branch variance by itself is **not** a defensible novelty claim. [Kavčić–Moura1998](https://users.ece.cmu.edu/~moura/papers/isit98-kavcic-ieeexplore.pdf), [Kumar etal.2010](https://arxiv.org/abs/1006.5036).

The narrow potential contribution is an explicit, reproducible **codec-evidence-conditioned innovations detector for archived radio telemetry**, with leakage-controlled online calibration and incremental recovery under a fixed compute budget. The current search did not establish priority for that combination. Search strings included `Vorbis demodulation satellite`, `compressed audio demodulation Viterbi`, `codec noise prediction demodulation`, and `compression aware demodulation`; predominantly unrelated compressed-payload transmission and generic receiver results are not proof of absence. A broader bibliography/patent/citation-chain review is still required before asserting novelty.

## Implemented evidence extraction

`CodecMetadata::probe(original_ogg, sample_rate, decoded_samples)` uses a bounded FFprobe invocation with ordered `packets_and_frames`. FFprobe provides separately selectable packet and decoded-frame metadata and supports a file output, permitting bounded logs and a bounded owned temporary metadata file. [FFprobe documentation](https://ffmpeg.org/ffprobe.html).

- Source identity uses the project's SHA-256/byte/path identity and is checked before/after probing.
- Source and probe JSON are bounded at64MiB; extraction has a30second timeout,1thread request, and250000packet limit. Existing Linux owned-process-group cleanup and parent-death handling are reused. Failures are explicit, never empty-success evidence.
- Only a single mono Vorbis stream with expected sample rate, `time_base=1/sample_rate`, and zero start offset is supported. WAV/IQ/Opus, ambiguous stream layouts, missing sizes/durations and unpaired frames are rejected.
- Packet/frame records must pair in output order and agree on packet position, byte size and PTS. Only a single initial priming packet may lack a decoded frame: either zero PTS (observed with FFmpeg's libvorbis encoding) or negative PTS ending at zero (observed in the archived recording).
- PCM positions are accumulated from actual decoded-frame `nb_samples`, with exact equality to the independently decoded waveform's sample count. This requires the same untrimmed, unresampled mono FFmpeg decode. A matching length alone does not validate a caller-supplied unrelated waveform; the integration must bind the waveform to the same original source.
- Features are `log(8*packet_bytes/decoded_frame_samples)` and a change-in-adjacent-output-duration indicator. The latter is **not** a directly parsed short/long-block flag. No floor, residue, mode or codebook coefficients are currently extracted.

### Important observed alignment pitfall

Read-only probing of the real original OGG for observation14967362 found an early packet with `pts=2048,duration=128,size=62`, while its paired decoded frame has `nb_samples=576` and immediately follows cumulative PCM offset1600. Using packet PTS or packet duration as an array index would misalign features by448samples in this location. The prototype preserves these raw fields for audit but uses the matched decoded lengths for indexing. A regression test reproduces this specific case. Input: [original OGG](/home/ubuntu/telemetry-yield/work/satnogs-today20-20260910-v1/acquisition/obs-14967362/capture.ogg).

This is a measured FFprobe behavior in our environment, not a universal statement about all Vorbis tools or a new property of the standard.

## Calibration and API

```rust
CodecMetadata::probe(&Path, u32, usize) -> Result<CodecMetadata, String>
metadata.feature_at(absolute_decoded_sample: f64) -> Result<CodecFeature, String>

CalibrationBlock {
    id: String,
    absolute_samples: Vec<f64>,
    residuals: Vec<f64>,
}
fit_variance(&CodecMetadata, &[CalibrationBlock], &[CalibrationBlock])
    -> Result<VarianceFit, String>
fit.model: Option<VarianceModel> // None when predictive gate rejects
model.multiplier(&CodecMetadata, absolute_sample: f64) -> Result<f64, String>
```

The caller proves CRC provenance and source/target separation and supplies innovations residuals if using the noise-predictive detector. The fitter independently checks unique block identifiers, finite ordered sample coordinates, nonoverlapping source/check intervals, and minimum/maximum partition sizes. It does **not** independently authenticate the claimed CRC or prove that an upstream signal model avoided using check data. The integration must make that provenance explicit.

Training performs a fixed ridge regression of log residual energy on standardized log packet density and the output-duration indicator. Training-only normalization produces bounded variance multipliers in[0.25,4]. At least4packet identities and nontrivial density variation are required. Parameters are not changed by validation. The heldout gate requires Gaussian conditional residual log-score improvement≥0.01nats/sample and no heldout block deterioration below−0.01nats/sample. Rejected candidates have `model=None`. These fixed engineering thresholds are not statistical significance tests and must be frozen before an independent experiment.

Per-symbol features evaluated at frontend support centers are an approximation: FIR, DC removal, AGC, symbol sampling and AR prediction can mix information across neighboring codec blocks. A future exact model would propagate the relevant transform/filter covariance; this prototype does not claim to do so.

## Required experiments and interpretation

1. Independently generated known-bit WAV paired with actual fixed-quality Vorbis encodes: measure added codec distortion and whether the proposed features predict it. Do not assume the direction of density/error correlation.
2. Compare matched white-noise, ordinary colored-noise, and codec-conditioned colored-noise metrics using the same fitted channel and timing hypotheses. Otherwise a gain may come from a changed channel fit or search budget.
3. Evaluate per-bit errors and exact CRC-valid payloads; negative controls and unexpected CRC-valid outputs remain visible. More likelihood on heldout residuals is not a substitute for more correct frames.
4. Test original archived OGG with unchanged search bank and separately score additional/missed frames, cost, and calibration rejection rates. All currently inspected20observations are development data, not an independent holdout.
5. If codec-conditioned weights show no reproducible incremental improvement beyond ordinary colored-noise estimation, report the negative result and keep the branch disabled. Do not rename the known colored-noise detector a new algorithm.

## Executed verification

`cargo test --lib codec_reliability -- --nocapture --include-ignored` passed6/6tests on2026-09-10. The tests cover the timestamp/duration transition mismatch, unsupported geometry/codec and packet/frame pairing failures, a genuinely predictive synthetic variance feature, nonpredictive/overlapping/invalid calibration rejection, actual libvorbis encode→decode→metadata pairing, and the preserved real observation14967362.

The real original14967362 probe matched **32929856decoded mono samples at48000Hz**, mapped33688audio-producing packets, and recorded216packet/frame timestamp-or-duration disagreements. Original-source SHA-256: `db9c4f43f5d707847191b6a1ad9e44d99a4a104ef3234767f401c2707e61bd9a`. All6tests, including actual codec processes and the full real-input probe, took3.68seconds after compilation on this host; this is not a general runtime guarantee or a decoder-speed benchmark.

Independent integration review found and the root agent corrected three issues before field testing: noise fitting previously touched the later codec-validation suffix; source metadata needed direct identity/rate/length checks against the loaded waveform; and insufficient codec-calibration samples needed a rejected optional codec branch rather than losing the unweighted lanes. The repaired source uses a dedicated noise-training prefix and preserves the codec-check suffix. Reviewed frontend support-center formulas agree with the actual FIR decimation and boxcar startup/delay implementation; the broad-support approximation caveat remains.

No frame-yield claim is made in this design document. Synthetic feature fitting demonstrates that the calibration code responds correctly to a constructed statistical relationship, not that real Vorbis packet density predicts errors.
