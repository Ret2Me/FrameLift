# Experimental native Meteor LRPT receiver

This is an **opt-in research path**, not a change to qualified receiver defaults,
not a universal SatDump replacement, and not a novel convolutional code.

## Implemented scope

- Original stereo PCM8/PCM16 IQ WAV or headerless little-endian complex
  float32 → native coherent PSK frontend, 72 ksymbol/s. No input quantization
  or resampling is needed for these formats.
- Explicit mission profiles: `meteor-m2x` (default, OQPSK with NRZ-M) and
  `meteor-m2` (original M2, QPSK without NRZ-M). The latter resolves global
  bit polarity at the decoded ASM before derandomization.
- RRC rolloff 0.5, span 8 symbols, four timing phases, signal-only CFO
  acquisition, carrier/timing loop bank, branch/pair/quadrant hypotheses.
- Encoded-ASM acquisition, soft K=7 rate-1/2 Viterbi (0x4f, 0x6d), **then**
  NRZ-M differential decoding. The differential stage belongs after FEC here.
- CCSDS ASM 0x1acffc1d, PN255 derandomization, conventional-basis
  RS(255,223), four interleaved lanes. All four lanes must verify.
- Corrected 1024-byte CADU output and payload-SHA256 deduplication, with
  explicit stream hypotheses, rejected-candidate counts and identity conflicts.
- Strict native AOS M_PDU → complete CCSDS Space Packets, including headers
  and payloads spanning frames, per-channel state and 24-bit counter rollover.
  Gaps abort incomplete packets; missing bytes are never synthesized.

The mission currently has **no independent frame CRC in this path**. Successful
RS decoding is reported as RS verification, not CRC validation or authentication.
The packet parser reports complete framed packets; its image-header check does
not claim successful JPEG entropy decoding.

## Explicit remaining gaps

MSU-MR segment entropy decoding / image assembly still uses **unchanged SatDump**
as a shared instrument renderer. Both experimental image arms must name this
dependency. Native IQ → CADU recovery is independent of SatDump; the optional
external-soft component test is not an independent native IQ result.

These profiles do not implement the
interleaved 80k variant, HRPT, DVB-S2/LRIT, or all instruments supported by
SatDump. The generic CRC-gated receiver's acceptance contract is unchanged.

## Reproduction

```bash
rtk proxy /home/ubuntu/.cargo/bin/cargo build --release --bin telemetry-meteor
rtk proxy ./target/release/telemetry-meteor decode \
  --input /absolute/path/to/stereo-iq.wav \
  --output /absolute/path/to/new-native-run --workers 6
rtk proxy ./target/release/telemetry-meteor packets \
  --input /absolute/path/to/new-native-run/native.cadu \
  --output /absolute/path/to/new-packet-audit
rtk proxy ./target/release/telemetry-meteor compare \
  --baseline /absolute/path/to/satdump.cadu \
  --candidate /absolute/path/to/new-native-run/native.cadu \
  --output /absolute/path/to/new-comparison.json
```

Run from the repository root. Output directories/files must not already exist.
The decoder validates the source hash before and after processing. Inputs are
bounded to 250 million complex samples; use an explicit time range for larger
files. One-second windows overlap by 0.25 seconds. ASM acquisition allows eight
hard errors in the last 52 convolutionally encoded sync bits by default. This
is a **heuristic search**, not a claim of exhaustive or lossless acquisition.

For a channel-only interoperability test, `decode --kind soft` accepts signed
int8 I/Q soft components (144,000 values/s) and resolves quadrant ambiguities.
It must be labelled external-soft and excluded from native-IQ claims.

For the original Meteor-M2 add `--profile meteor-m2`. For raw Gqrx complex
float32 add `--kind iq-cf32 --sample-rate 144000` (using the source's actual
rate). WAV sample rates come from the header; a conflicting explicit rate
is rejected. Partial or nonfinite CF32 samples are rejected. PCM values
are normalized by their original bit depth, not requantized to PCM16.

## Fair benchmark and image rules

1. Freeze the original IQ SHA256 and both executable versions. Same full
   input, no artificially degraded comparator, and no reference payloads fed
   into the native receiver.
2. Keep SatDump's RF/FEC parameters unchanged. In the benchmark working
   directory, `settings.json` disables **automatic product postprocessing**
   only; this avoids the old postprocessing crash without weakening decoding.
3. Use `--fill_missing false` in both shared-renderer arms. Do not interpolate,
   inpaint, sharpen, upscale or recolor one arm to manufacture an advantage.
4. Count unique corrected 892-byte transfer-frame payloads, excluding ASM and
   parity. Report shared, native-only **and baseline-only**, plus wall cost.
5. Separately extract complete packets from each arm with the same strict
   native parser, with hashes and an auditable length-prefixed byte archive.
6. A hybrid union is an augmentation result and must be labelled **SatDump +
   native recovery**, never standalone native superiority. Resolve identity
   conflicts before merging. A selected positive example is not a held-out
   publication benchmark or evidence of general military/commercial advantage.

## Development evidence

`work/meteor-native-20260913/` contains original-source identification,
reference logs, test artifacts and matched-input results. The first 3-second
development clip at t=300 seconds produced 26 native IQ frames; all 26 matched
the full-reference payload hashes. This subset check is not a full-file
benchmark. Complete results are recorded separately when available.

The completed first full-file comparison is in
`work/meteor-native-20260913/README.md`: native 5767 versus SatDump 5727
unique CADUs (73 added, 33 missed, including idle frames). Explicit augmentation
recovers 300 extra complete instrument packets and fills 140 empty 112×8 image
tiles across three channels in their common extent. These are development-set
results, not a held-out general superiority claim.

Primary implementation references: SatDump 1.2.2's installed
`Meteor-M.json`, `module_ccsds_conv_concat_decoder.cpp`, `cc_encoder.cpp`,
and Meteor MSU-MR instrument/CCSDS AOS sources. Native code was implemented in
Rust; SatDump source is used to establish interoperability parameters and as
the independent executable reference, not relabelled as native recovery.
