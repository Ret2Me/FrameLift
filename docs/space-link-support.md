# CSP, AOS, and USLP frame support

`rust/space_link.rs` parses complete, already-delimited link frames. It does not perform
demodulation, synchronization-marker acquisition, channel decoding, de-randomization, or
stream reassembly. `SpaceLinkConfig::decode(&[u8])` returns the decoded primary header,
separately delimited insert/data/trailer regions, and the names of validations actually
performed. `validate()` rejects unsupported managed configurations before any frame is used.

The configuration is a strict Serde tagged enum (`type`: `csp_v1`, `csp_v2`, `aos`, or
`uslp`). Unknown fields are rejected. `has_integrity_check()` distinguishes integrity-bearing
configurations from structural-only parsing; callers processing recovered telemetry should
require it.

## CSP v1 and v2

Supported:

- the libcsp v2.1 packed network-byte-order v1 header (4 octets: 2/5/5/6/6/8 bits);
- the libcsp v2.1 packed network-byte-order v2 header (6 octets: 2/14/14/6/6/6 bits);
- frames with no CRC, explicitly configured as `crc32: absent` (structural validation only);
- a required big-endian CRC-32C trailer with exactly one configured scope:
  `required_header_and_payload` (the libcsp 2.1 transmit default) or
  `required_payload_only_legacy` (the libcsp 1.x compatibility form).

The decoder never implements libcsp's permissive receive fallback. A payload-only CRC cannot
silently satisfy a header-and-payload configuration, or vice versa. The CRC is reflected
CRC-32C/Castagnoli (polynomial `0x82f63b78`, init/xorout `0xffffffff`). The CSP CRC flag must
exactly agree with the selected mode.

Rejected:

- fragmented, HMAC, RDP, reserved, or otherwise unknown flag bits;
- a missing/unexpected CRC flag or trailer;
- truncated headers/trailers and CRC mismatches;
- the optional CSPv1 little-endian ZMQ compatibility representation (only the normal network
  representation is accepted).

Primary implementation sources:

- [libcsp v2.1 `csp_id.c`](https://github.com/libcsp/libcsp/blob/v2.1/src/csp_id.c) defines both
  packed layouts and their network-byte-order conversion.
- [libcsp v2.1 `csp_types.h`](https://github.com/libcsp/libcsp/blob/v2.1/include/csp/csp_types.h)
  defines the flag values.
- [libcsp v2.1 `csp_crc32.c`](https://github.com/libcsp/libcsp/blob/v2.1/src/csp_crc32.c)
  defines the CRC-32C table/initialization, big-endian trailer, v2.1 transmit scope, and legacy
  verification fallback. This implementation requires the caller to choose one scope instead
  of reproducing that fallback.

## CCSDS AOS

Supported subset of CCSDS 732.0-B-5 (October 2025):

- a complete AOS Transfer Frame without SDLS;
- the six-octet Issue-5 primary header, including TFVN `01`, 24-bit VC frame count, replay and
  frame-count-cycle signaling;
- the Issue-5 ten-bit Spacecraft ID. Its two most-significant bits are primary-header bits
  42–43; they are not discarded;
- exact managed frame length, managed insert-zone length, managed OCF presence, and managed
  FECF presence;
- an opaque Transfer Frame Data Field (returned as `payload`) and an opaque four-octet OCF;
- when configured, the final two-octet FECF using the existing
  `protocol::validate_tm_fecf` CRC-16/CCITT-FALSE implementation.

Issue 4 and earlier used only the lower eight SCID bits. An old-compatible frame with header
bits 42–43 equal to zero has the same numeric SCID in this Issue-5 parser. If either bit is set,
this parser incorporates it, and labels the validation
`ccsds_aos_primary_header_issue_5_semantics`;
it never silently applies the old eight-bit interpretation.

Rejected:

- optional Frame Header Error Control: it is a shortened RS(15,11) code, not the frame FECF;
- SDLS Security Header/Trailer configurations and an SDLS Frame Security Report OCF;
- the reserved Only-Idle-Data VCID 63, because this subset does not validate the required idle
  contents;
- a nonzero cycle value when its cycle-use flag is zero;
- missing data-field octets, inconsistent managed layout, wrong version, or FECF mismatch.

Primary source: [CCSDS 732.0-B-5](https://ccsds.org/wp-content/uploads/gravity_forms/5-448e85c647331d9cbaf66c096458bdd5/2025/10/732x0b5ec1.pdf),
especially sections 4.1.1–4.1.6 (frame order, Issue-5 primary header, insert/data/OCF/FECF) and
section 6.3 (SDLS fields). This is structural frame support, not M_PDU, B_PDU, VCA_SDU, CLCW,
or SDLS-content decoding.

Example configuration:

```json
{
  "type": "aos",
  "frame_length_bytes": 1115,
  "insert_zone_length_bytes": 0,
  "operational_control_field": true,
  "frame_error_control_field": true,
  "frame_header_error_control": false,
  "security": false
}
```

## CCSDS USLP

Supported subset of CCSDS 732.1-B-3 (June 2024):

- a complete, non-truncated Version-4 (`TFVN=1100`) USLP Transfer Frame without SDLS;
- all 13 non-truncated primary-header fields, including zero-to-seven-octet VC frame counts;
- mandatory verification of the encoded total frame length (`length field + 1`), with an
  optional additional managed exact-length constraint;
- managed insert-zone length, the signaled OCF, and managed FECF presence;
- the one- or three-octet TFDF header, all eight TFDZ construction-rule values, UPID, and the
  optional first-header/last-valid-octet pointer for rules `000`, `001`, and `010`;
- the TFDZ returned as `payload`, an opaque four-octet OCF, and optional FECF validation via
  `protocol::validate_tm_fecf`.

Rejected:

- truncated Transfer Frame Primary Headers (end-of-header flag one);
- SDLS Security Header/Trailer configurations and an SDLS Frame Security Report OCF;
- the reserved bypass/protocol-command state `0/1`, nonzero spare bits, and VCID 63 OID frames;
- truncated TFDF pointers or a non-sentinel pointer outside the returned TFDZ;
- encoded/managed length mismatch and FECF mismatch.

UPID payload semantics, packet extraction/reassembly, COP commands, OCF reports, OID pseudo-noise
contents, and SDLS are intentionally not decoded. A successfully parsed TFDZ is not a claim that
its UPID-specific payload has been validated.

Primary source: [CCSDS 732.1-B-3](https://ccsds.org/Pubs/732x1b3e1.pdf), especially sections
4.1.1–4.1.6 (full-frame layout, complete primary header, TFDF header/rules, OCF, FECF), annex D
(the rejected truncated format), and section 6.3 (the rejected SDLS fields).

Example configuration:

```json
{
  "type": "uslp",
  "expected_frame_length_bytes": null,
  "insert_zone_length_bytes": 0,
  "frame_error_control_field": true,
  "security": false
}
```

## Validation labels

Only executed validations are reported. In particular, no-CRC CSP and no-FECF AOS/USLP frames
receive structural labels but no CRC/integrity label. CRC-bearing modes add exactly one of:

- `csp_crc32c_header_and_payload`;
- `csp_crc32c_payload_only_legacy`;
- `ccsds_aos_fecf_crc16`;
- `ccsds_uslp_fecf_crc16`.

The `csp_no_unsupported_extensions` label is derived from received CSP header flags. The
`ccsds_aos_configured_without_sdls` and `ccsds_uslp_configured_without_sdls` labels instead mean
that the caller supplied the required managed `security: false` configuration; SDLS absence is
not inferable from an untrusted radio frame alone. None is a cryptographic security validation.
