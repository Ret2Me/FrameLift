# Support matrix

[Documentation](README.md) · [Receiver guide](rust-receiver.md)

Scope checked against the native capability inventory on 2026-09-15. Run
`telemetry-yield-rs capabilities` for the build you actually deploy.

**Implemented** means a native path exists. **Tested** refers to specific vectors
or recordings. Neither means every mission using the same modulation is supported.
Demodulation, channel coding, framing and application interpretation are separate layers.

## Inputs and modulation

| Signal / representation | Entry point | Evidence and limits |
|---|---|---|
| Mono WAV; post-FM FSK/GMSK | `decode-audio`, `decode-progressive` | Complete historical CANVAS 9600-baud field comparison; no all-rate claim |
| Mono Vorbis OGG | Same audio commands, FFmpeg/ffprobe preparation | Preserves source/conversion identity; compression is not undone |
| Bell-202 AFSK audio | `decode-audio --mode afsk --baud 1200` | Separate bank; the CANVAS result does not qualify it |
| Complex IQ FSK | Generic `decode` with explicit plan | Phase-first and channel-conditioned frontends |
| Complex IQ Bell-202 AFSK | Generic `decode`; explicit legacy route available | Input format, rate and bank must match the plan |
| Coherent BPSK / QPSK / OQPSK IQ | Generic `decode` / `decode-metadata` | Native synchronization and ambiguity hypotheses; synthetic qualification, no established orbital PSK yield advantage |
| Physical bits without framing | `physical-decode` | Unaligned decisions are not validated telemetry |
| SigMF / raw IQ manifests | `inspect-metadata`, `decode-metadata` | Supported layouts are validated explicitly; not every extension |

## Framing and channel coding

| Layer | Native support | Explicit exclusions / requirements |
|---|---|---|
| AX.25 | Plain NRZI and G3RUH paths, original received FCS | Progressive benchmark uses strict UI PDUs, not all AX.25 services |
| CCSDS Space Packets | Raw and AX.25-wrapped configurations | Packet structure alone has no universal payload checksum; explicit integrity policy required |
| CCSDS TM | Configured transfer-frame layout and FECF | Mission frame length, synchronization and coding must be supplied |
| CSP v1 / v2 | Header parsing and configured CRC32C scope | No extension/security processing or general CSP network stack |
| CCSDS AOS | Configured Issue-5 layout and FECF | No FHEC/SDLS processing or complete mission services |
| CCSDS USLP | Non-truncated frame/TFDF and FECF | No SDLS or cross-frame reassembly |
| Fixed synchronization | Explicit word, layout and validator | Finding a syncword alone is not acceptance |
| Reed–Solomon | GF(256), shortening, interleaving, CCSDS dual basis; RS(255,223) preset | Not all shortening/interleaver choices are mission-qualified |
| LDPC | Soft normalized min-sum with explicit sparse H/output map; TC128 and TC512 presets | Not every CCSDS LDPC family, puncturing or concatenation |
| Derandomization | Explicit none / TM255 / TM131071 / TC255 | Order and settings are part of the link plan |

FEC convergence is different from a received CRC check. Corrected data must retain
its correction and integrity provenance. A header parser is not a mission-specific
temperature, voltage or image interpreter.

## Images and compute

| Component | Scope | Limit |
|---|---|---|
| Geoscan | Packet assembly and reconstruction | Available bytes only; separate image evidence |
| `telemetry-meteor` | Companion Meteor decoding/reconstruction tool | Not evidence of broad SatDump parity |
| `telemetry-sstv` | Companion SSTV tool | Analog image output is not CRC-validated telemetry |
| CPU | Default native runtime, task/sample parallelism | Exactness claims are tied to measured configurations |
| Optional CUDA | Real/complex FIR, including PSK matched filtering | Host/NVRTC checks passed; hardware execution parity remains open |

FFT, carrier/timing recovery, sequence detection and FEC are not CUDA-accelerated
today. Companion image binaries do not expose GPU selection. No gain on Terra,
Aqua, Aura, SAR, DVB-S2 or all satellite modulation families follows from this table.
