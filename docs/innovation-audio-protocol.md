# Residual and codec conditioned sequence detector — development protocol

Frozen design before the first experiment, 2026-09-10. This is a development
experiment, not a preregistered independent population holdout.

## Question and scope

Does a predictive residual model transferred from a disjoint, native-CRC-valid
packet improve complete AX.25 recovery beyond a matched white-noise detector?
Does original Vorbis packet side information improve this further? The first
question is an application of known colored-noise sequence detection; the
second is a proposed technical contribution, not established priority.

The experimental interface is mono post-FM FSK/GMSK audio. No new PSK, general
FEC, IQ or multi-station combining support is claimed. The qualified receiver
defaults and frozen progressive binary remain unchanged.

## Design

- Run the existing adaptive baseline and retain its entire frame union.
- Reconstruct source symbols from its native CRC-valid anchors. Verify span
  coordinates, original frame SHA256 and the received FCS independently.
- Fit an additional three-tap channel on an early guarded part of each source
  frame; reserve later guarded residuals for noise and codec calibration.
- Fit bounded AR(0/1/2) residual prediction with internal waveform validation;
  never optimize it using the number of recovered target frames.
- Noise fitting and its internal order-selection check are confined to the
  first60% of the reserved residual interval, in both codec/non-codec runs.
  Codec validation uses only the final40% after another64-symbol guard, never
  touched by channel fitting or AR order selection. The codec fit may reuse
  the noise-training prefix; its check suffix is untouched by either fitter.
- For targets, transfer only from the nearest same-frontend source window
  within 60 seconds and separated from the entire target window by 1 second.
  Supplemental output is never recycled as a new training anchor.
- Evaluate a matched white-noise detector, a colored-noise innovations
  detector, and (when separately accepted) a codec-conditioned innovations
  detector on exactly the same timing hypotheses and channel gains.
- The codec prototype uses decoded Vorbis frame sample counts and associated
  compressed packet sizes. It does not parse floor/residue quantizers or know
  the lost waveform. Packet density is a feature, not measured error variance.
- Feature positions use the dominant frontend impulse-response center;
  frontend DC rejection and AGC also have wider support. This mapping is an
  explicit approximation, not exact localization of codec error at a symbol.

Every search output must pass native AX.25 UI checks and independent received
CRC residue. No CRC-guided bit repair, archive payload input or reference bit
input is allowed. An internal residual-validation gate is not calibrated
false-alarm control or an independent publication test.

## Planned field pilot and scoring

Use all 20 already-inspected CANVAS observations in
`work/satnogs-today20-20260910-v1/acquisition`, without further outcome-based
selection. Execute initially in ascending observation ID order; interrupted
or failed inputs remain explicitly incomplete, never zeros. All inputs remain
development data. Hash inputs and executable and freeze per-run plan before
DSP. Never modify old results.

Report complete PDU byte sets, new and omitted frames for each matched lane,
the additive union, payload bytes excluding FCS, source/target provenance,
AR orders, codec fit acceptance, actual CPU/wall/RSS where measured, and errors.
Global unique PDU and observation–PDU occurrences are distinct metrics.

Additional frames relative to the old adaptive interface are provisional:
compare any improvements against the frozen complete progressive v3 decoder
on the identical input, including its blind and multiple-anchor branches,
before calling them improvements over the best current system. Historical
external-decoder results on differently quantized WAV are context only.

## Controls and advancement

Independent known-bit symbol and actual WAV/Vorbis controls are defined in
`innovation-controls-protocol.md`. Oracle timing must be labeled. Include
learned-anchor negative controls, not only no-anchor silence. Preservation
by additive union is not proof that a new lane is as accurate on its own.

Advance a candidate only after identifying its incremental contribution,
reporting regressions and unexpected PDUs, and verifying new field bytes.
Publication readiness, population false-alarm rate, equal-CPU superiority,
and a codec-specific recovery advantage are unproven until separately tested.

Prior art: Kavcic and Moura, *The Viterbi Algorithm and Markov Noise Memory*,
IEEE TIT 46(1), 2000,
https://users.ece.cmu.edu/~moura/papers/kavcic_viterbinoisememory.pdf.
Codec structure: https://www.xiph.org/vorbis/doc/Vorbis_I_spec.html.
