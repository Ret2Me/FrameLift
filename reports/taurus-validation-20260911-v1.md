# Taurus-1 main-output protocol audit — 2026-09-11

## Result

The previously reported **57 and 82 GR candidates are 81-byte chunks of a KISS
stream, not independently validated telemetry packets**. Ordered reconstruction
finds no packet matching the known Taurus telemetry discriminator, using either
the stricter Rust parser or the actual pinned gr-satellites telemetry parser.
A same-profile 600-second synthetic white-noise control produces 48 such chunks.
None of these counts is new Rust recovery or proof of valid telemetry.

| Input | Raw 81-byte chunks | Strict complete nested segments | Pinned GR KISS segments | Known telemetry magic | Integrity-validated telemetry |
| --- | ---: | ---: | ---: | ---: | ---: |
| Private observation 3208 | 57 | 6 | 14 | 0 | 0 established |
| Private observation 3412 | 82 | 12 | 24 | 0 | 0 established |
| Seeded white noise, 600 s | 48 | 5 | 15 | 0 | 0 established |

“Zero established” means there is no supported integrity proof, not a proof that
the recordings contain no telemetry. A wrong demodulator setting, an unknown
payload type, losses between chunks or an unsuitable audio representation can
still prevent recognition. These recordings alone do not establish a population
false-positive rate or validate the complete BPSK demodulator.

## What this protocol actually emits

The pinned **gr-satellites v5.9.0** LilacSat-1 chain uses two alignment branches
of continuous rate-1/2, constraint-length-7 Viterbi decoding, differential decoding,
32-bit CCSDS attached-sync-marker detection (up to four differing bits), additive
descrambling and fixed-position voice/data demultiplexing. Viterbi always chooses
a path; its presence is not an independent integrity check. There is no CRC or
Reed-Solomon rejection in this deframer. Its Python definition explicitly describes
the main output as chunks of a KISS stream.
[Pinned deframer](https://github.com/daniestevez/gr-satellites/blob/v5.9.0/python/components/deframers/lilacsat_1_deframer.py),
[Viterbi implementation](https://github.com/daniestevez/gr-satellites/blob/v5.9.0/python/hier/ccsds_viterbi.py).

For each 116-byte block after the four-byte ASM, the demultiplexer separates five
7-byte Codec2 voice slots and emits the remaining 81 bytes on its main port. The
voice port is separate and was deliberately not attached to a UDP/audio sink in
these offline replays. Its absence is not evidence of lost telemetry processing.
The primary decoder author's description confirms that unused data positions are
filled with KISS delimiters and explains the absence of Reed-Solomon for latency.
[Pinned demultiplexer](https://github.com/daniestevez/gr-satellites/blob/v5.9.0/lib/lilacsat1_demux_impl.cc),
[Original design explanation](https://destevez.net/2017/05/low-latency-decoder-for-lilacsat-1/).

The satellite definition requires **KISS without a control byte** downstream of
the main output. The offline raw-output profile omitted that application transport,
so its file records need another KISS parsing pass before telemetry interpretation.
[Taurus-1 SatYAML](https://github.com/daniestevez/gr-satellites/blob/v5.9.0/python/satyaml/Taurus-1.yml).

The pinned `taurus1` parser skips an opaque 12-byte prefix, then selects one of
14 telemetry layouts by a two-byte magic value. It neither defines that prefix's
CCSDS semantics nor checks a checksum. Some subordinate layouts contain fields
called `crc` or `checksum`; merely reading those fields does not validate them.
We therefore do not invent a CCSDS sequence counter, CRC polynomial or generic
AX.25 interpretation for these bytes.
[Pinned telemetry structures](https://github.com/daniestevez/gr-satellites/blob/v5.9.0/python/telemetry/by70_1.py).

## Bounded Rust implementation and checks

`examples/taurus_validation_probe.rs` preserves raw outer-capture order and
duplicates, separates timestamp commands from data, requires complete outer KISS
records and 81-byte main chunks, and reconstructs nested KISS without stripping
a control byte. It checks the original source, prepared WAV, GR entrypoint and
selected-profile identities, and independently reproduces each recorded candidate
set from the actual KISS file. Source hashes pin six v5.9.0 reference files;
the installed Python files and Taurus YAML matched upstream bytes exactly.

The strict reassembler requires delimiters on both sides, rejects invalid escapes,
and retains leading/trailing incomplete segments in explicit counters. Its 4096-byte
segment cap is an **audit resource bound**, not a claimed protocol maximum. It
recognizes all 14 pinned magic values and minimum readable structure sizes,
distinguishing truncation, exact size and uninterpreted trailing bytes. Even a
structure match remains integrity-unresolved.

A second, explicitly diagnostic implementation reproduces the less strict pinned
GR KISS parser. The original parser accepts a leading fragment and silently drops
invalid escaped bytes; it is not a checksum verifier. Its actual installed `work`
method was run on all three raw captures, and its output bytes exactly matched
the Rust compatibility implementation. Its actual `taurus1.parse` accepted zero
of the 14, 24 and 15 resulting segments. All 14 structure sizes were also checked
against the installed reference objects.
[Pinned KISS parser](https://github.com/daniestevez/gr-satellites/blob/v5.9.0/python/kiss_to_pdu.py).

The strict parser reports 8/11/10 invalid-escape segments and 398/295/71 leading
unframed bytes for observation3208/observation3412/noise respectively. Each capture
ends with one incomplete nested segment. No oversized segment was encountered.
The two Viterbi branches are merged at the main message port without branch or RF
sample-position metadata. File timestamps are capture-time annotations, not proof
of RF continuity. We preserve arrival order but cannot independently establish
missing-chunk continuity or meaningful telemetry sequences.

Tests: **10/10 regular unit tests passed**; the optional actual-reference
integration was run explicitly and verifies all three captures and 14 layout
sizes. Tests include all escaped octets across chunk boundaries, duplicates,
leading/trailing fragments, dangling/invalid escapes, oversize rejection,
no-control-byte handling, all layout sizes, shifted magic and idle delimiters.
No frozen decoder/library source was changed by this protocol audit.

## Noise control and interpretation

`examples/taurus_noise_control.rs` binds the existing mono48kHz, 600-second float32
WAV with SHA-256 `271761e41c34cf8c032961cdc90511f8f822a7d34a11af083c39881261a4107f`.
It was generated with FFmpeg white noise, amplitude0.2 and seed2026091101, with no
intentionally encoded packets. The same Taurus profile and GR entrypoint used for
observation3208 were checked before and after the run. Network submission was
disabled. The control completed successfully in 9.13 seconds of concurrently
loaded wall time; that is not an isolated performance benchmark.

The 48 outputs demonstrate empirically that main-output chunks alone are not
evidence of telemetry. As a rough sanity check only, allowing four mismatches in
a 32-bit marker admits 41449 of 2^32 uniformly random bit patterns. Such marker
hits are plausible in long noise streams; correlated branches, resynchronization
and scheduler behavior prevent interpreting that simple calculation as a measured
false-positive distribution. The actual seeded control is the relevant evidence.

## Artifacts and safe next step

All outputs are under `work/taurus-validation-20260911-v1/`; original campaign and
replay files were unchanged. `combined-evidence-v2.json` contains source identities,
ordered chunk provenance, exact nested payloads, layout accounting and limitations.
The `white-noise-600s-v1/` directory preserves plan, process logs and raw capture.
`source-v1.tar.gz` preserves the earlier v1 analyzer source; current v2 adds C++
source hash enforcement and the optional reference integration test.

The defensible next improvement is a branch-aware, sample-position-preserving
LilacSat/Taurus transport path and a verified positive control with documented
telemetry/checksum semantics. No protocol-unresolved chunk should enter a unique
telemetry gain denominator. Broader support should be added protocol by protocol,
with separate integrity tiers, rather than forcing every payload through AX.25 UI.
