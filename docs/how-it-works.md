# How Telemetry Yield works

[Documentation](README.md) · [Architecture](architecture.md) · [Results](benchmarks.md)

## The short version

Think of a recording as an imperfect measurement, not a bag of already readable
packets. One timing choice might decode the middle of a pass and miss its edges.
A different filter or channel model might recover different packets. We run a
controlled collection of these attempts, validate their outputs, and keep the
union with a record of which attempt produced each packet.

No neural model is required by this receiver. The main path uses explicit digital
signal processing and finite hypothesis banks. Neither a plausible temperature
nor a visually pleasing image substitutes for received data and integrity evidence.

## First choose the right representation

**IQ** contains two components of the radio signal and preserves phase information.
An IQ plan specifies sample format, rate, modulation and framing. This is the
input needed by the coherent BPSK/QPSK/OQPSK paths.

**Post-FM audio** has already passed through a frequency discriminator. It can
carry FSK/GMSK packet information, but is not interchangeable with IQ. The
progressive receiver works on this mono representation. An OGG file is a
compressed container for those audio samples, not a modulation or a protocol.

For OGG, FFmpeg/ffprobe are external codec tools. The usual progressive path
prepares a content-addressed float32 WAV. The historical comparison instead
deliberately creates one shared PCM16 WAV for every comparator; that choice is
part of its protocol and must not be silently changed.

## The progressive audio portfolio

The evaluated configuration uses six-second windows with a three-second hop.
Overlap avoids making packet recovery depend on arbitrary file boundaries;
duplicate detections are removed at the observation level.

1. **Condition the signal in two ways.** A legacy FIR/DC frontend and a
   square-pulse/DC/RMS frontend retain different responses to distorted audio.
2. **Try complementary clocks.** Each frontend has 160 fixed phase/rate hypotheses
   and 16 Gardner timing-loop configurations. A ranked quick prefix runs first;
   completing the baseline evaluates all 352 attempts per window.
3. **Learn from separately received packets.** A packet with a valid received FCS
   can supply known symbol decisions for a local channel estimate. The target
   window must be disjoint, at least one second away, and within 60 seconds of
   an eligible source window using the same frontend.
4. **Decode through that channel model.** A four-state Viterbi sequence detector
   evaluates the effective three-tap channel at gains 0.75, 1 and 1.5. This is
   equalization/sequence estimation, not convolutional FEC decoding.
5. **Try bounded supplemental models.** Blind fitting can start without a decoded
   anchor; multi-anchor fitting combines compatible nearby estimates. Fit gates
   can reject these models. A successful fit alone is never an accepted packet.
6. **Validate and combine.** The progressive path retains received FCS bytes and
   requires strict AX.25 UI structure. It does not change bits to force a CRC match.

The effective symbol-rate model is:

```text
observed[n] = bias + h[-1]·symbol[n-1] + h[0]·symbol[n] + h[1]·symbol[n+1] + residual[n]
```

This is an approximation to the recorded, post-FM channel. Exact minimization
of its metric does not imply an optimal receiver for every real RF channel.
Implementation: [adaptive.rs](../rust/adaptive.rs),
[sequence.rs](../rust/sequence.rs),
[progressive_channel.rs](../rust/progressive_channel.rs).

## Preventing circular evidence

Reference-decoder outputs and known archive payloads are never supplied as desired
answers to the receiver. The early adaptive stages use a frozen quick-prefix
anchor generation; final adaptive stages use the complete baseline generation.
New supplemental packets do not recursively train new anchors.

Disjoint windows reduce direct training contamination; they do not establish
statistical independence between neighboring portions of a satellite pass.
Similarly, CRC validity reduces accidental errors but does not authenticate the
transmitter or prove a false-positive probability for a large correlated search.

## Progressive execution

`quick`, `deep` and `full` change the available time, not the configured search
bank. Quick defaults to 3,000 ms; deep to 60,000 ms; full has no time limit unless
`--budget-ms` is supplied. A completed short invocation may have only partial
coverage. It is not a claim of the same accuracy as full mode.

```sh
mkdir -p results
target/release/telemetry-yield-rs decode-progressive \
  --input capture.ogg --output results/pass-001 --mode quick --threads 4
target/release/telemetry-yield-rs decode-progressive \
  --input capture.ogg --output results/pass-001 --mode deep --threads 4 --resume
target/release/telemetry-yield-rs decode-progressive \
  --input capture.ogg --output results/pass-001 --mode full --threads 4 --resume
```

The supervisor accounts for preparation and verification, terminates owned
workers at the budget, and retains committed tasks. Linux scheduling and cleanup
mean this is not a hard real-time three-second guarantee. A killed, uncommitted
task may be retried. Completed tasks are reused only after identity checks.

| Artifact | Meaning |
|---|---|
| `manifest.json` | Source, prepared input, executable, policy and backend identities |
| `tasks/` | Immutable committed task results and provenance |
| `result.json` | Reconstructed packet union and completion state |
| `runs/` | Per-invocation options and execution receipts |

Same input, binary and decoding policy are required for resumption. Mode, task
thread count and cache reservation may be changed where the CLI permits it;
switching backend identity is not a transparent resume. See
[compute qualification](enterprise-compute-and-benchmark-v1.md).

## A separate experimental branch

The innovations receiver adds residual prediction and, optionally, Vorbis-derived
reliability proxies. It is an experiment separate from the progressive CLI bank.
The codec-enabled arm additionally sees original-container metadata, whereas all
arms receive identical numerical PCM samples.

In the completed 266-recording study the codec-enabled and codec-disabled arms
returned identical packet sets. Their 6,420 packets cannot be credited to codec
conditioning. They use a different portfolio from the progressive arm, so their
difference from its 6,220 packets is not a clean component ablation either.
[Experimental usage](innovation-v2-usage.md) · [Study](benchmarks.md).

## What this does not solve

An unsupported mission layout, absent transmission, severe clipping or discarded
phase information cannot be fixed by simply increasing the number of attempts.
Unknown modulation discovery, multi-station combining and a general mission
telemetry parser are not implicit features. The best deployment fit today is an
offline, explicitly configured recovery worker alongside an existing station.
