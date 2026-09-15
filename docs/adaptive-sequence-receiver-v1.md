# Adaptive sequence receiver v1 — development protocol, 2026-09-10

This is an opt-in Rust implementation, not a replacement of the qualified
receiver and not a claim of a new MLSE algorithm. No frozen campaign is changed.

## Scope and architecture

Input is mono, FM-demodulated WAV/OGG. The first integration supports binary
FSK/GMSK audio with AX.25 UI, with or without G3RUH scrambling. The sequence
detector itself takes real-valued symbols and is independent of the frame
protocol. It is not a coherent-IQ demodulator, and the new command must not be
advertised as a universal CCSDS/PSK/QAM receiver.

1. Run the existing four-path development portfolio unchanged: legacy FIR/DC
   and boxcar/RMS frontends, each with the full 160 fixed timing hypotheses and
   the 16 pre-existing Gardner configurations. Retain their separate frame sets.
2. For fixed timing hypotheses with received CRC-valid, structurally valid UI
   packets, locate the physical symbol spans. Fit a three-tap channel plus DC
   bias on those spans, excluding 20 symbols from either edge. Deduplicate
   overlapping training samples. Retain one quality-gated model per frontend
   per window, selected by model error, not by subsequent target packet yield.
3. For each target window, choose the nearest disjoint anchor window of the
   same frontend, with a one-second guard and at most 60 seconds between window
   starts. Transfer only the channel model and the fitted timing hypothesis's
   rate. Never transfer frame contents or target hard decisions into fitting.
4. Try a fixed bounded timing bank: eight evenly spaced phases at the anchor
   rate plus eight highest-scoring local fixed-bank hypotheses, deduplicated.
   Use gains 0.75, 1.0 and 1.5. A four-state, full-traceback Viterbi detector
   minimizes squared sample error under the learned three-tap model.
   No CRC-guided bit flipping, reference-assisted search or iterative training
   on newly recovered target packets is enabled.
5. Accept supplemental packets only with received FCS and AX.25 UI checks.
   Return both baseline and supplemental sets and their union. Union cannot
   remove baseline frames by construction; this is not proof that MLSE alone
   outperforms the portfolio.

No suitable anchor means skip the supplemental pass for that window. Numerical
or decoding errors are explicit errors, not successful zero-frame results.
CPU parallelism is across windows with deterministic collection. Parameters,
input/executable hashes, anchor identities, model diagnostics, timing/gain
trials, frame provenance and stage wall times are written to a new output
directory. Existing result directories are never overwritten.

## Verification plan fixed before replay

- Synthetic: known-channel fitting, exhaustive short-sequence MLSE cost oracle,
  transfer to different symbol/packet contents, invalid inputs, no-anchor
  controls, and exclusion of overlapping/self-training windows.
- Regression: existing protocol oracle and existing Rust tests; compare the
  integrated baseline's exact full-frame sets with the frozen four-path probe.
- Real development replay: observations 14936407, 14936415, 14936424, 14936444,
  previously used for the September 8 tracking experiment. Replay entire OGGs,
  not selected packet snippets. Report each observation including zero gain.
  These already-exposed files are development data, NOT an independent holdout.
- Report unique received frame sets, payload bytes, additions, losses and wall
  time against the four-path portfolio. Original SatNOGS/gr-satellites comparison
  remains separate; its old gain must not be attributed to this new extension.

The channel is only a short, linear approximation of demodulated audio. Channel
change, nonlinear distortion, colored noise, codec loss and weak/absent anchors
can defeat it. Received CRC is not transmitter authentication, and more trials
require fresh independent null/false-positive qualification before deployment.
Publication and production readiness remain false until independently tested.
