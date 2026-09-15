# Native Geoscan support

Implemented in Rust as `ProtocolConfig::Geoscan`, with `config/rust/pcm-geoscan-9600.json` and CLI `assemble-geoscan`.

## Scope

- Reuses existing FSK demodulation and timing bank; this is a new protocol adapter, not a new RF algorithm.
- 32-bit sync `930B51DE`, configurable 0–4 sync-bit errors, both polarities.
- 66-byte frames; reset PN9 for each frame; CRC-16/CC11xx (polynomial 8005, init FFFF, no reflection or final xor, big-endian received checksum).
- Native result retains all 66 bytes, including received CRC. Image assembler rechecks this CRC.
- External KISS is explicitly labelled as relying on the external decoder's integrity checks. KISS alone is not validation.
- Single-image reassembly uses received start/address fields, rejects conflicting bytes and ambiguous starts, sorts implicitly by addresses, deduplicates packets and writes a byte-coverage map.
- Missing bytes are zero-filled ONLY in the partial JPEG output and are explicitly false in `coverage.json`, with half-open missing ranges in `report.json`.
- A received EOI with no missing bytes is reported as byte completeness, not as independent JPEG semantic validation. No missing tail or start is invented, no image enhancement is applied.
- Experimental mission support, not an across-mission performance claim. Existing frozen release binary and campaigns are unchanged.

## Usage

```sh
telemetry-yield-rs decode --input capture.ogg --plan config/rust/pcm-geoscan-9600.json --output NEW-native --threads 8
telemetry-yield-rs assemble-geoscan --input NEW-native/result.json --output NEW-image
telemetry-yield-rs assemble-geoscan --kiss --input reference.kiss --output NEW-reference-image
```

Both output directories must be new. Do not mix recordings of different images into one assembly. The on-air frame integrity check is not authentication.

## Sources

- https://github.com/daniestevez/gr-satellites/blob/main/python/components/deframers/geoscan_deframer.py
- https://github.com/daniestevez/gr-satellites/blob/main/python/hier/pn9_scrambler.py
- https://github.com/kng/geoscan-tools/blob/main/process_simple.py
- https://network.satnogs.org/observations/6922885/

The project graph was consulted, but returned legacy Python protocol nodes; actual integration was checked directly against the current Rust registry.

## Real recording result: observation 6922885

All receivers used `work/geoscan-implementation-20260913-v1/input.wav`, SHA256
`6b44fbbdef6024eb7d3234710b4a441a292342cbb419b4db87ee192f9bf74cf3`.
The source OGG and observation metadata remain in `work/image-search-20260913-v2`.

| Receiver | Received JPEG bytes | Missing JPEG bytes | Reconstruction |
|---|---:|---:|---|
| gr-satellites defaults | 30007 | 504 (9 ranges) | partial |
| gr-satellites `--disable_dc_block` | 30511 | 0 | complete through received EOI |
| Native Geoscan / diverse FSK | 30511 | 0 | complete through received EOI |

The native image is **byte-identical** to the better reference JPEG (verified with
`cmp`), SHA256 `c3d770149c2d204285f1b38dab6a864f1d267b57b1a986241e6b06ca2d6c674c`.
It also decodes without FFmpeg errors. Both images are 640×480. No enhancement,
resampling, noise injection or replacement of content was used.

The native run completed all 139 windows with zero failed windows, yielding 593
distinct CRC-valid radio frames (586 image PDUs, 7 excluded non-image PDUs).
This first run took 170.048 s in an **unoptimized development build** on a busy
host; do not compare this time with frozen release benchmark timing.

Conclusion: successful native format implementation and parity on this example,
**not evidence of superiority over properly configured gr-satellites**. The
damaged default-reference image must always be labelled with its configuration;
the complete no-DC reference must not be hidden from a publication.

Artifacts under `work/geoscan-implementation-20260913-v1/`:

- `native/result.json`, `native-image/recovered.jpg`, `native-image/report.json`
- `reference.kiss`, `reference-image/recovered.partial.jpg`
- `reference-no-dc.kiss`, `reference-no-dc-image/recovered.jpg`
- Each image directory includes byte coverage and missing-range metadata.

Verification: 7 Geoscan unit tests and 2 generic receiver registry/file tests
passed; library check and CLI build succeeded. The public source/config are
integrated, while `target/release/telemetry-yield-rs` remains deliberately untouched.
The tested CLI is `target/debug/telemetry-yield-rs`.
