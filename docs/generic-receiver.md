# Generic satellite receiver boundary

Current Rust extension (2026-09-11): native IQ BPSK/QPSK/OQPSK now feeds the
protocol layer, and `coded_sync` adds explicit RS/LDPC plus CSP/AOS/USLP
CRC/FECF validation. See [PSK and channel coding](psk-and-channel-coding.md)
for exact variants, input contracts, examples and synthetic-only qualification.
The historical architectural description below is not a complete capability
inventory of the current Rust executable.

The reusable receiver is not coupled to SatNOGS, a NORAD catalogue entry, a
station, or a mission. Its contract is:

`IQ -> waveform demodulator -> soft timing bank -> protocol adapter -> validated frame`

`GenericSatelliteReceiver` runs an explicit, bounded list of
`ReceiverHypothesis` values. A hypothesis names a demodulator, symbol rate,
clock-error bank, and protocol adapter. The current built-in phase
demodulator covers binary FSK, GFSK, and GMSK at arbitrary valid sample-rate,
baud-rate, and integer-decimation combinations. AX.25 (plain or G3RUH) is one
adapter rather than an assumption in the receiver.

`FixedSyncFrameDecoder` is the generic escape hatch for fixed-length,
syncword-based links. Mission-specific derandomization/FEC may be supplied as
a transform and the complete CRC as a validator. It fails closed: a syncword
match alone is never a decoded frame.

CCSDS is not sent through AX.25 rules by default. The built-in protocol layer
also provides Version-1 TM Transfer Frame validation with exact configured
length and FECF/external integrity, raw Space Packet discovery only when the
caller supplies an integrity validator, and an AX.25-plus-Space-Packet adapter
when the outer AX.25 FCS is the integrity boundary.

Other waveform families require separate demodulator plugins because a phase
discriminator is not physically valid for all of them. BPSK/QPSK/OQPSK now
have coherent native Rust plugins; LoRa/OFDM remain unimplemented. They reuse
the provenance, bounded-plan, and protocol interfaces without adding
network-specific logic to the receiver core.
