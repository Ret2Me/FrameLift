# CCSDS TM Transfer Frame parser/validator — scope and limitations

Status date: 2026-09-02

## Primary standards checked

- [CCSDS 132.0-B-3, *TM Space Data Link Protocol*, Issue 3, October 2021](https://ccsds.org/Pubs/132x0b3.pdf) is the current CCSDS Blue Book for the Version-1 TM Transfer Frame. Section 4.1 defines the mandatory six-octet primary header, optional secondary header and OCF, optional two-octet FECF, and a fixed managed frame length.
- [CCSDS 131.0-B-6 EC1, *TM Synchronization and Channel Coding*, Issue 6, April 2026](https://ccsds.org/wp-content/uploads/gravity_forms/5-448e85c647331d9cbaf66c096458bdd5/2026/06/131x0b6ec1.pdf?gv-iframe=true) is the current channel-coding Blue Book. Section 3.5 requires FECF validation for uncoded, convolutional, or Turbo paths, while R-S, concatenated, and LDPC decoding may provide frame validation. The official [publication record](https://ccsds.org/view/bluebooks/entry/4803/) identifies Issue 6 and Editorial Correction 1.

Only these official CCSDS sources were used to set the protocol and integrity contract.

## Implemented scope

- Typed parser for the CCSDS Version-1 **TM Transfer Frame**, not a CCSDS Space Packet.
- Exact, caller-configured frame length, limited to the TM maximum of 2048 octets.
- Full six-octet primary-header fields and the TM version value `00`.
- Packet-data status invariants, including reserved Packet Order Flag, Segment Length ID `11`, and bounded First Header Pointer when applicable.
- Optional Version-1 secondary-header length parsing, opaque secondary-header data, optional four-octet OCF boundaries, and a mandatory non-empty Transfer Frame Data Field.
- Explicit managed declaration of FECF presence. The FECF implementation uses the CCSDS polynomial `x^16+x^12+x^5+1`, all-ones initialization, MSB-first processing, and a two-octet big-endian field.
- A structural parse is never called validated telemetry. Acceptance requires either a correct configured FECF or an explicitly named external integrity callback. If both are configured, both must pass.
- A `GenericReceiver` protocol adapter that searches a caller-configured sync marker, extracts exactly one configured-length TM frame, and exposes only `ccsds_tm_transfer_frame` capability metadata.

## Deliberate limitations

- No AOS or USLP frame parsing or capability claim.
- No Space Packet extraction, packet reassembly across frames, virtual-channel continuity validation, or mission-specific SCID/VCID allowlist.
- No convolutional, Reed-Solomon, Turbo, LDPC, slicing, SDLS, or pseudo-randomizer implementation. The adapter expects any required channel decoding and derandomization upstream. The sync marker is configuration, not integrity evidence.
- No implicit Attached Sync Marker default and no registration in `default_receiver()`: marker, frame length, FECF presence, and any external integrity rule are managed parameters that must come from a real link profile.
- A valid 16-bit FECF is error detection, not authentication. Campaign-level false-accept measurements and provenance remain necessary.

The resulting module is intentionally a narrow protocol-validation building block. It does not claim an end-to-end CCSDS physical-layer receiver.
