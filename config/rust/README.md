# Explicit receiver plans

These are syntax examples, not automatic satellite identification. Set sample
rate, datatype, symbol rate and representation from recording metadata. The
receiver rejects incompatible input instead of silently resampling or guessing.
The two IQ FSK frontends in the example are additive: both are executed.

`pcm-fsk-9600.json` expects **already FM-demodulated** mono audio. It is not
the same representation as complex RF IQ. `pcm-bell202-1200.json` expects
Bell 202 tones in the mono audio. `iq-fsk-9600.json` expects little-endian
interleaved signed16 I,Q pairs; sample rate 48000 is an example, not a default
to apply to unrelated recordings.

`iq-bell202-legacy-1200.json` is an explicit 57.6 kHz legacy IQ timing profile,
including 16 guard symbols and per-hypothesis lengths. It is not interchangeable
with the PCM plan. `afsk-legacy-window.json` configures the separately exposed
one-window exact-result command `decode-afsk-legacy`; its window/stride fields
do not select an input range for that command: `--start-sample` and
`--sample-count` do.

`cw-ci16.json` configures `inspect-cw`; it produces pending Morse candidates,
not CRC-validated telemetry. Adapt its sample rate to real recording metadata.

`physical-qpsk-example.json` produces unaligned bit candidates only. It does
not demonstrate AX.25 or CCSDS telemetry recovery and accepts no reference bits.
Reference-bit alignment is a separately named scoring command.

CCSDS TM and raw Space Packet plans must specify their actual mission framing
and integrity policy. No example invents a frame length or treats a plausible
header as an integrity check. Custom validators are available through the
Rust library rather than arbitrary dynamically executed configuration code.

`pcm-geoscan-9600.json` enables native Geoscan/CC1125 PN9 + CRC framing for
48 kHz demodulated audio. Use `assemble-geoscan --input RESULT_JSON --output NEW_DIR`
to assemble one image, or add `--kiss` for explicitly external baseline PDUs.
The output includes received-byte coverage and missing ranges; partial images
are named `recovered.partial.jpg`. See `reports/geoscan-native-20260913.md`.
