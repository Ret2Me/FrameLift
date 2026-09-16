# FEC-assisted acquisition with a damaged marker

`fec_sync` adds an **optional, bounded relaxed-marker acquisition path** to the
native coded receiver. It is intended for a present but damaged attached sync
marker (ASM). It does not discover arbitrary markerless transmissions, infer
unknown coding parameters, or repair CRC bits to manufacture valid telemetry.

The ordinary `coded_sync` protocol is unchanged. Selecting
`fec_assisted_sync` runs that same hard-ASM baseline first, preserving its frame
order, bytes, and validation labels, and then adds independently validated
frames from the fallback. A fallback budget error fails the attempt: a partial
union is never returned as a successful complete decode.

## What the receiver actually does

1. Decode the existing hard-ASM path, including its original resource limits.
2. Enumerate every offset containing a complete marker and configured codeword.
   Skip offsets already within the baseline hard-marker tolerance. Apply the
   explicitly configured relaxed Hamming and soft-correlation gates.
3. At every surviving offset, reset the configured derandomizer and run FEC.
   LDPC must satisfy all configured parity checks; RS must pass syndrome
   verification and supplies a reconstructed codeword by re-encoding the
   corrected bytes. No payload validator is consulted at this stage.
4. Measure the fraction of received absolute soft evidence contradicted by
   the reconstructed codeword. Reject candidates above the configured distance
   limit. Rank the rest by ascending distance, then descending marker
   correlation, then ascending offset. This ranking is fixed before any CRC or
   FECF check. It determines deterministic processing order, not a hidden top-K
   cutoff: all eligible candidates are evaluated.
5. Require the independently received CRC/FECF and all configured structural
   checks. Deduplicate accepted bytes against the baseline and other accepted
   candidates. Add `fec_assisted_sync` only to frames obtained by the fallback.

Soft evidence is ONE-positive. Both marker correlation and codeword distance
use values after subtracting the caller's decision threshold. Marker
correlation is `sum(expected_sign * soft) / sum(abs(soft))`. Codeword distance
is `sum(abs(soft) for contradicted bits) / sum(abs(soft))`; it is a bounded
reliability-weighted disagreement score, **not** a calibrated probability,
likelihood ratio, or confidence estimate. A wholly erased codeword has no
distance score. Individual erasures remain allowed.

FEC convergence alone is never an accepted telemetry frame. In particular,
an externally encoded codeword with a wrong FECF ranks ahead of a noisier valid
codeword in the regression test, and is then rejected at the integrity gate.
The CRC cannot influence offset selection, FEC corrections, ranking, or the
decision to stop scanning. CRC/FECF checks still have a nonzero false-accept
probability; wider-search operating points need their own empirical
false-accept study and independent frame corroboration.

## Supported profiles and explicit exclusions

The new path accepts the installed `FrameCode::ReedSolomon` and
`FrameCode::Ldpc` configurations, including shortening, explicitly configured
RS byte interleaving/basis, and the explicit LDPC H/output mapping. It requires
an existing `FrameValidator` with received integrity protection: AX.25 UI/FCS,
CCSDS TM/FECF, or the supported CSP/AOS/USLP integrity-aware profiles. The
validator checks the configured structure and length; it does not imply a
spacecraft identity allowlist that the profile never configured.

Concrete regression coverage includes the CCSDS RS(255,223) dual-basis preset
shortened to 32 information bytes, and the installed TC128/TC512 LDPC
primitives. The TC512 demonstration uses a synthetic TM/FECF payload inside a
TC512 coding envelope, **not** a claim that this is a standards-compliant TM
link or a complete telecommand CLTU receiver. TC128 is tested with a synthetic
CSP/CRC envelope. Profile parameters and the position/order of marker,
randomization and coding must match the transmitter.

`none` is rejected explicitly. Convolutional K7, concatenated K7+RS, puncturing,
AR4JA, DVB-S2, TC CLTU acquisition/tails, arbitrary symbol insertion/deletion,
and polarity/carrier recovery are not implemented in this acquisition path.
K7 support elsewhere in FrameLift does not imply support in `CodedSyncConfig`;
an unknown `FrameCode` JSON variant is rejected rather than silently mapped.
Modulation/timing hypotheses are still produced by the selected upstream
demodulator. The marker must pass the configured relaxed gate; an absent or
fully erased ASM is not recovered by this implementation.

The RS conventions are specified in
[CCSDS 131.0-B-5](https://ccsds.org/Pubs/131x0b5.pdf). The two installed TC LDPC
generator matrices are from
[CCSDS 231.0-B-4, Tables 4-1 and 4-2](https://ccsds.org/Pubs/231x0b4e1.pdf).
This implementation combines established acquisition and coding techniques;
it does not establish a novel coding theorem or algorithmic priority.

## API and file-pipeline integration

```rust
let report = fec_sync::decode(&soft_bits, threshold, &coded_profile, &recovery)?;
// report.frames contains the baseline-preserving union.
// report.candidates retains every relaxed-marker FEC trial and its outcome.
```

The existing generic IQ/audio file receiver accepts the following protocol
entry. A waveform hypothesis refers to it using its normal `protocol_id`.
This is a synthetic 32-byte TM test profile, not a mission configuration:

```json
{
  "type": "fec_assisted_sync",
  "config": {
    "syncword": [0,0,0,1,1,0,1,0,1,1,0,0,1,1,1,1,1,1,1,1,1,1,0,0,0,0,0,1,1,1,0,1],
    "maximum_sync_hamming": 1,
    "frame_bytes": 32,
    "code": {
      "kind": "reed_solomon",
      "config": {
        "field_polynomial": 391,
        "first_root": 112,
        "primitive_step": 11,
        "parity_symbols": 32,
        "shortening": 191,
        "interleaving": 1,
        "basis": "ccsds_dual"
      }
    },
    "randomizer": "none",
    "validator": {
      "type": "ccsds_tm",
      "config": {"frame_length_bytes": 32, "fecf_present": true}
    },
    "maximum_candidates": 32
  },
  "recovery": {
    "maximum_marker_hamming": 6,
    "minimum_marker_correlation": 0.6,
    "maximum_codeword_distance": 0.25,
    "maximum_offsets": 1048576,
    "maximum_fec_trials": 128,
    "work_budget": 1000000000
  }
}
```

`recovery: {}` uses the same defaults shown above. Unknown recovery fields are
rejected. Changing the recovery settings changes the generic receiver's
checkpoint identity. Resumed coded frames undergo received CRC/FECF/structure
replay and must retain their required FEC provenance, just as ordinary coded
frames do. A cached frame alone cannot replay the original channel syndrome;
the immutable run contract binds the source, configuration, and executable.

## Bounds and diagnostics

Input limits remain 8,388,608 finite soft values and the existing coded-frame
limits. Additional explicit limits are 1–8,388,608 complete offsets,
1–4,096 relaxed-marker FEC trials, and 1–1,000,000,000 conservative work units.
The hard-marker baseline retains its separate original 1,000,000,000-unit
decoder-work cap. Units are complexity estimates, not elapsed time or CPU
instructions. The fallback charge includes a full marker pass over all
offsets plus FEC decoding, reconstruction and linear-pass allowance per trial.

The report records baseline and extra frame counts, scanned offsets, FEC
trials, added work units and a complete fallback candidate ledger. Each entry
contains marker offset/Hamming/correlation, syndrome convergence, correction
or iteration metadata, codeword distance, pre-integrity rank and outcome:
FEC rejection, distance rejection, integrity rejection, duplicate or accepted.
The generic receiver keeps its existing frame-result schema; callers needing
the detailed ledger should use the report-returning Rust API.

## Verification and evidence limits

The frozen RS codewords in `rust/tests/fec_sync_tests.rs` were generated by the
independent **gr-satellites 5.5.0-2build2** `encode_rs(true, 1)` block running
on **GNU Radio 3.10.9.2-1.1ubuntu2**. The input FECF was calculated using
Python's independent `binascii.crc_hqx(data, 65535)` during fixture generation.
The installed encoder library SHA-256 was
`cd29749f3d9ad598c03b5d8a87f0c348afc51b3dc0d074b90209eae1a1d23162`.
Both a valid frame and a frame with its last FECF bit flipped were separately
encoded. The committed tests consume literal bytes and require no Python,
GNU Radio, network or external library at runtime.

LDPC fixture encoders implement the published generator circulants separately
from the production parity-check decoder. Tests cover four marker errors plus
correctable codeword damage, baseline parity, wrong phase/bit offset,
wrong randomizer/output mapping, invalid TM structure with valid FECF,
bad FECF with valid FEC, noise/erased inputs, distance limits, every resource
budget, malformed configuration, protocol dispatch and configuration identity.
An independent rectangular BPSK generator also writes a temporary CF32 IQ
file and exercises the actual generic file receiver and checkpoint replay.

These are deterministic development and interoperability tests. The damaged
marker fixtures demonstrate baseline **0 → 1** accepted frame on those
constructed signals. They do not establish additional yield on historical
SatNOGS captures, a population false-accept bound, superior speed, or universal
receiver superiority. The separate field pilot remains unchanged; its null
gain versus the previous native decoder is not replaced by these fixtures.
