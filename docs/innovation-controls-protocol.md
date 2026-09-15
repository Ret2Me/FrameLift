# Innovation detector development controls — frozen design v1

Frozen 2026-09-10 before generating or scoring this experiment. This is a bounded
development pilot, not independent publication holdout or a calibrated false-alarm
rate experiment. All outcomes, including regressions and null gains, are retained.

## Cases and transmitted data

32 independent seeds: four impairments (clean, colored Gaussian, clipping,
impulsive), eight repetitions each. The master seed is `0x20260910c0decafe`;
case seed is master XOR `(case_index+1)*0x9e3779b97f4a7c15` with u64 wrapping.
PRNG is xorshift64 (13,7,17), open 52-bit uniform and Box–Muller Gaussian.
Each case draws two distinct AX.25 UI anchor payloads and a distinct target
payload (256 random payload octets, independent PRNG domains). Received X.25
FCS is appended by an independent bitwise encoder, verified after every decode
by an independent bitwise residue check. No accepted frame absent from truth
is silently dropped from scoring.

Each waveform is 18 seconds of mono float32 WAV at 48 kHz, 9600 symbols/s.
G3RUH-scrambled, NRZI-coded physical levels use five PCM samples per symbol,
900 opening/closing HDLC flags. Packet starts are 0.25, 3.25 and 10.25 seconds;
an equally long target-free segment starts at 14.25 seconds. Gaussian background
standard deviation is 0.001. A centered three-tap channel is `[0.06,0.65,0.06]`
with bias 0.0; symbol-rate noise is held over five samples. Anchor noise standard
deviation is 0.025; target/null standard deviation is 0.24 in impairment cases
and 0.025 in clean cases. Colored Gaussian uses AR(1) coefficient 0.90;
clipping saturates received signal at ±0.30; impulsive noise adds random-sign
amplitude 0.90 with probability 1/512 per symbol. Anchor impairment uses the
same category but keeps the low noise level; impulses are absent from anchors.

The waveform is encoded once with actual ffmpeg/libvorbis quality 3, one thread,
and decoded to float32 WAV without normalization, resampling or gain changes.
The exact command, binary identity/version and all file hashes are retained.
WAV and OGG variants share transmitted data and pre-codec noise, so comparisons
are paired; they are not independent observations.

## Primary bounded symbol experiment

The signal values are sampled at the known center of each symbol. This is
**oracle timing**, not an end-to-end synchronization test or field result.
Channel parameters and noise parameters are never passed to the detectors.
Strong anchor hard-sliced samples must actually decode with valid received FCS.
Only independently validated CRC frame spans produce binary training levels,
channel fits and residual spans. No target truth enters fitting or selection.
Anchor payloads are distinct from each other and from the target.

On the independently drawn target and target-free segment compare ordinary
three-tap white-noise MLSE with the new innovations detector using its fixed
internal model-selection gate. Score exact received frame+FCS equality, missing
and unexpected CRC frames, symbol error count (known bits used only after decode),
fit rejection, selected noise order and elapsed time. If anchors fail, preserve
the result as unavailable training, not zero recovered telemetry.

## Codec diagnostic and possible full audio follow-up

Compare exact decoded PCM to its paired source WAV. Measure compression residual
power per actual decoded codec block and its association with packet bit density;
do not assume bit density predicts distortion or any particular direction.
Record unequal decoded lengths/alignment failures explicitly. Metadata parsing
does not receive transmitter payload truth. This diagnostic does use the paired
pre-codec samples to measure distortion and is not a production calibration.

After the frozen primary pilot, the generic audio receiver can run the same
artifacts with at most two worker threads. Full audio tests pass only PCM/source
codec metadata to the receiver; all payload scoring is afterward. Such results
must be labeled separately from oracle-timing symbol results. No result-dependent
seed deletion, parameter tuning or quality-level selection is permitted within
this v1 experiment. New choices require a separately frozen version.

## Interpretation

This controlled test checks mechanism, transfer across different payloads and
codec behavior. It cannot establish literature novelty, general improvement
on satellite observations, superiority at equal CPU, all-protocol support, or
publication/deployment readiness. Null segments are signal-free targets in files
that also contain real anchor packets; they are not pure-noise complete files.
