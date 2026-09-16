# Soft sequence and iterative receiver — experimental increment

This increment adds a four-state log-MAP (BCJR) detector and an opt-in
BCJR/LDPC turbo equalizer. It does not change the default progressive task
bank, the September 16 frozen qualification executables, or the paper results.
BCJR and turbo equalization are established methods, not claimed inventions.

A subsequent [integrated IQ increment](advanced-iq-receiver.md) connects these
primitives to bounded multi-mode acquisition, estimated/varying channels,
protected-repeat combining and checked cancellation. The limitations below
describe this original symbol-block/audio increment, not every later module.

## What is implemented

- Exact bit marginals under a fixed centered three-tap, independent Gaussian
  noise model, with optional bit priors. ONE-positive posterior and extrinsic
  LLRs are separate. The first/last observations are excluded; boundary bits
  have unknown states. Input dimensions, magnitudes, variance and work are bounded.
- LDPC exposes approximate extrinsic information even on nonconvergence. Its
  existing hard API still rejects nonconverged results. Each invocation starts
  with fresh check-node messages. Input evidence is subtracted before clipping.
- Iteration over an explicitly synchronized symbol block, with a bijective
  wire-to-code interleaver and explicit codeword randomizer. Only extrinsic
  messages circulate, with damping. FEC convergence AND an independently
  received configured CRC/FECF are required before exporting frame bytes.
- A separate mono-audio pilot compares BCJR against the existing MLSE lane on
  identical disjoint source anchors, target timing hypotheses and gain trials.
  It retains the adaptive/innovations reference union and reports additions
  **and losses** for the individual new lane. No FEC is invented for AX.25.

This is the first implementation stage of the proposed research direction.
Joint time-varying channel/carrier/timing inference is **not** implemented by
this increment. Nor are IQ interference cancellation, repeated-packet combining,
new mission FEC profiles, RS soft feedback, or CUDA kernels for BCJR. Existing
PSK synchronization and convolutional Viterbi paths remain unchanged. The turbo
block API is NOT a new acquisition/framing path for arbitrary IQ or OGG files.

## Commands

All three are native Rust CPU implementations. Build the normal receiver.

```sh
cargo build --release --bin telemetry-yield-rs
telemetry-yield-rs decode-soft-sequence < soft-block.json
telemetry-yield-rs decode-turbo-block < coded-block.json
telemetry-yield-rs decode-bcjr-audio --input recording.wav \
  --output new-bcjr-session --duration-seconds 90 --threads 1
```

`decode-soft-sequence` accepts:

```json
{"samples":[0,0.5,0],"channel":{"taps":[0,1,0],"bias":0,"noise_variance":1},"prior":[]}
```

`decode-turbo-block` accepts `samples`, `channel`, an explicit `code` matching
`LdpcConfig`, `wire_to_code` (empty for identity), `randomizer`, the existing
mandatory integrity-aware `validator`, `iterations` (1–16), and `damping` (0–1).
Samples are in wire order. Randomization is defined in codeword order, before
wire interleaving on transmission; feedback reverses the same mapping. Different
transmitter layouts need an explicit upstream transform, not guessed settings.
An unaccepted result has `frame_hex: null`, with parity/integrity diagnostics.
CRC is an acceptance/early-stop gate, never a likelihood or bit-repair objective.

The audio pilot accepts original OGG or mono WAV, processes the first requested
1–120 seconds, and refuses an out-of-range crop. Source/executable hashes and
PCM crop identity precede processing. It is not the full progressive portfolio
and does not support progressive checkpoint resume. OGG decoding still uses
the existing external media-conversion contract. Its LLR scale comes from
another window's residual variance and has **not** been field-calibrated.

## Test protocol fixed before development replay

1. Exhaustive short-bit-string oracle for BCJR; analytical memoryless LLR,
   endpoint, prior subtraction, saturation, malformed-input and no-evidence tests.
2. Published CCSDS LDPC generator vectors independent of H; parity-valid but
   CRC-invalid frames must remain rejected; permutation/randomizer round trip.
3. Synthetic benchmark: 64 frames at each of four noise variances
   `[0.16, 0.36, 0.64, 1.0]`, centered taps `[0.6, 1.0, 0.5]`, fixed seed,
   TC512 coding of 32-byte CRC-protected TM test frames and fixed interleaving.
   Compare single-pass BCJR + 12 LDPC iterations, single-pass + 48 LDPC
   iterations, and up to four turbo passes with 12 LDPC iterations each,
   damping 0.7. The true channel/noise are supplied to every arm: an oracle
   engineering simulation, NOT estimated-channel or orbital performance.
   Save every case, exact known-byte matches, false accepted frames and negative
   noise/wrong-CRC controls. No tuning or selecting only winning cases afterward.
4. Exposed CANVAS 14936372 first 90 seconds (the existing metadata-free WAV),
   compared with the unchanged adaptive/innovations baseline and separately
   the archived complete progressive result. Any further records are explicitly
   development data, never the running 96-observation qualification holdout.

Secondary development replay, declared before running the new lane: CANVAS
14936407, the existing `overnight-recovery-20260913-v1` shared 120-second WAV
crop (original samples 7,056,000–12,816,000 at 48 kHz). This is deliberately a
previously inspected positive control (old reference: 16 frames), not a blind
sample or evidence about the population of unsuccessful observations.

Wall times on this shared host are diagnostics, not publication speedup evidence.
Successful unit tests or synthetic gains do not qualify deployment or establish
novelty. A new frozen held-out trial and false-acceptance study remain necessary.

Reproduce the fixed-seed synthetic experiment in a fresh directory:

```sh
cargo run --release --example soft_iterative_benchmark -- \
  --output new-soft-iterative-benchmark
```

The command saves the pre-run plan and all positive/negative outcomes, including
expected bytes and per-pass acceptance decisions. It does not download data or
write into existing qualification sessions. See the separate
[September 16 development results](../reports/soft-iterative-development-20260916.md)
for completed simulation and real-audio comparisons, including the no-gain cases.

## Prior art

- [Tüchler, Koetter and Singer, Turbo Equalization: Principles and New Results (2002)](https://www2.ensc.sfu.ca/people/faculty/cavers/ENSC805/readings/50comm05-tuchler.pdf).
- [Liu et al., Iterative Equalization of CPM With Unitary Approximate Message Passing (2024)](https://arxiv.org/abs/2408.07385).
  That system's cyclic-prefix assumptions are not automatically valid for legacy recordings.

Results from this increment belong in a separate development report, not in the
frozen historical paper or ongoing scheduler qualification aggregate.
