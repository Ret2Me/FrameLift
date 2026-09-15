# Explicit frame FEC support

Implemented in pure Rust (`rust/fec.rs`, `rust/fec_rs.rs`,
`rust/fec_ldpc.rs`). No runtime subprocess, FFI, Python, dependency addition,
or external encoder/decoder is used. This is ordinary channel coding, not a
novel FEC algorithm and not a claim of universal CCSDS interoperability.

## Contract and integration

`FrameCode` is an explicitly tagged (`kind`) configuration with `none`,
`reed_solomon` and `ldpc` variants. `validate()` rejects malformed parameters;
`encoded_bits(decoded_bytes)` checks exact dimensions before scanning frames;
`decode(soft, decoded_bytes)` returns bytes and correction/iteration metadata.

Soft values are **positive for bit ONE**, negative for ZERO. Byte order is
MSB-first. The LDPC implementation internally changes sign to the conventional
zero-positive message convention. No sync marker, line coding, randomizer,
puncturing, or modulation is implicitly guessed inside FEC. The caller must
apply those operations in the actual transmitter's order and pass exactly one
configured codeblock. Reed–Solomon interleaving is explicitly configured.

Successful RS/LDPC decoding means the entire configured syndrome passed.
It does **not** prove the transmitted payload was recovered: beyond their
correction region, both decoders can select another valid codeword. A separate
CRC/FECF and protocol-structure validator remains mandatory for accepted
telemetry. `parity_verified` is false for `none`; it must never be substituted
for an independent CRC. Errors return no partially decoded bytes.

## Reed–Solomon

The GF(256) decoder supports explicit primitive field polynomial, first root,
primitive root step, even parity count 2–128, leading-zero shortening, symbol
interleaving 1–8 and conventional/CCSDS-dual symbol bases. It uses
Berlekamp–Massey locator estimation, Chien root search, a small GF linear solve
for magnitudes, and all-syndrome verification. It does not yet use erasures,
soft reliability lists, symbols wider than eight bits, or implicit padding.

`ReedSolomonConfig::ccsds_rs255_223(interleaving, shortening)` selects field
`0x187`, first root 112, primitive step 11, 32 parity symbols and dual basis.
Those conventions, the basis matrix and standard symbol interleaving are
specified in [CCSDS 131.0-B-5, section 4 and annexes F/G](https://ccsds.org/Pubs/131x0b5.pdf).
The generic parameter names also agree with
[Phil Karn's libfec RS interface](https://github.com/ka9q/libfec/blob/master/rs.3).
General interleaving depths 6/7 are accepted configurations, not advertised as
standard CCSDS-managed choices. A lane corrects up to parity/2 unknown erroneous
symbols when the input is within that codeword's bounded-distance region.

`encode(bytes)` is also provided on `ReedSolomonConfig` for interoperability
and synthetic link tests; it does not participate in decoding decisions.

## LDPC

The generic normalized-min-sum decoder takes a sparse binary parity-check
matrix as variable-index lists and **explicit information-bit output positions**.
Normalization of min-sum check messages is an established reduced-complexity
method, not a new algorithm; see
[Chen et al., Reduced-complexity decoding of LDPC codes](https://research.ibm.com/publications/reduced-complexity-decoding-of-ldpc-codes).
It does not guess systematic coordinates, prove the rank/dimension of an
arbitrary supplied matrix, or infer punctured/shortened bits. Caller-provided
H and output mapping must describe the actual encoder. Duplicate coordinates,
out-of-range coordinates, unconstrained variables and malformed rows are
rejected. Redundant check rows are permitted. Soft clipping, normalization
factor and iteration limit are explicit parameters; nonconvergence is failure.

The two named systematic telecommand presets come from
[CCSDS 231.0-B-4, section 4](https://ccsds.org/Pubs/231x0b4e1.pdf):

| Preset | Code dimensions | Information bytes | H circulants | Output positions |
|---|---|---:|---|---|
| `LdpcConfig::ccsds_tc128()` | (128,64) | 8 | 16×16 | 0–63 |
| `LdpcConfig::ccsds_tc512()` | (512,256) | 32 | 64×64 | 0–255 |

These presets are **not** the telemetry AR4JA family, the 8176/7156 code, DVB-S2,
or a complete TC CLTU receiver. A generic H decoder does not automatically
support their puncturing, interleavers, generators, or framing conventions.
The circulant choices are also tabulated in
[CCSDS 230.1-G-3, tables 4-2/4-3](https://ccsds.org/Pubs/230x1g3e1.pdf).
No `tc256` preset is claimed: it is not one of the two codes in this Blue Book.
TC randomization must be selected separately from TM randomization; the TC
sequence starts `ff399e5a68`. Selecting TC FEC alone does not configure it.

## Resource bounds

- Uncoded frames: 1–65,536 bytes (including a maximum-length USLP frame).
- RS: at most 255 symbols per lane, eight lanes; shortening must leave at least
  one information byte per lane. The dimension check precedes allocations.
- LDPC: at most 65,536 coded bits, 65,536 checks, 1,048,576 edges, 200 iterations
  and 100,000,000 edge-iterations. The requested output is whole bytes and
  shorter than the codeword. LLR clipping is positive finite and at most 100;
  normalization is positive and at most one. All soft input must be finite;
  wholly zero evidence is rejected, while individual zero-valued erasures are
  allowed. These are computational bounds, not statistical confidence levels.

## Verification and limitations

The RS independent oracle is the **literal published generator polynomial**
from CCSDS131 Annex G: a unit symbol in the final systematic position yields
that polynomial as a complete codeword. Tests check all 255 single-error
positions, 1–16 errors at multiple distinct position sets, and an explicit
17-error failure. Literal dual-basis wire bytes, derived independently from
the normative matrix, also check conventional/dual conversion and shortening.
Additional round trips cover two primitive fields, three first roots, two
root steps, parity counts 2/16/32/64/128, interleaving and boundary shortening.
Those additional round trips are not presented as independent oracles.

For LDPC, **all 64 and 256 published generator basis rows**, respectively,
from CCSDS231 tables4-1/4-2 must satisfy the separately constructed H matrices.
This tests circulant direction, bit ordering and systematic extraction without
generating codewords from H. Each soft decoder also recovers a damaged
nontrivial published-generator word at every possible one-bit position (128 and
512 positions, respectively). Other tests cover explicit
output reordering, nonconvergence, nonfinite input and malformed/work-heavy H.

These are deterministic coding/unit tests. They do not establish coding-gain
curves, real-observation yield, performance parity with other receivers,
false-positive rates, or deployment/publication readiness. Wider LDPC profile
coverage and real PSK captures remain separate integration/benchmark work.
