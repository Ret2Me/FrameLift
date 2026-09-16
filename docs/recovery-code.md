# Fixed-frame recovery code adapters

`rust/recovery_code.rs` provides a bounded decoder boundary for iterative IQ
recovery. It accepts one channel log-likelihood ratio per transmitted coded
bit; positive means one. Byte and convolutional-input ordering is MSB first.
No Python executable, external process, or GPU is required.

| Profile | Decoding | Soft feedback | Acceptance |
| --- | --- | --- | --- |
| LDPC | Existing sparse normalized min-sum | Existing code-bit extrinsic messages | All parity checks and received CRC/FECF |
| Uncoded | Hard decisions on an aligned fixed frame | None | Received CRC/FECF |
| Convolutional K7 | Exact 64-state log-MAP, rate 1/2 | Code-bit extrinsic, excluding that bit's own input likelihood | Received CRC/FECF |
| Reed–Solomon | Existing bounded hard-decision GF(256) decoder | None | RS checks and received CRC/FECF |
| Convolutional + RS | Inner K7 log-MAP, optional interstage derandomization, then outer RS | Inner convolutional extrinsic only | RS checks and received CRC/FECF |

The existing `coded::FrameValidator` checks AX.25 UI with its received FCS,
CCSDS TM with received FECF, or the configured CSP/AOS/USLP integrity trailer.
Structure-only validation cannot enable recovery acceptance. RS correction
is **not** converted into invented independent observations or extrinsic LLRs.

`RecoveryOutput.accepted` is the acceptance gate. `parity_verified` is only a
diagnostic: it is false for uncoded and convolutional-only profiles because
they do not perform an independent parity-syndrome test. Accepted frames
include their original integrity trailer. Only an accepted result carries
a complete reconstructed codeword for interference cancellation. RS and
convolutional re-encoding is performed after the received-integrity check.

## Explicit configuration

An uncoded, already aligned, 27-byte frame:

```json
{"kind":"uncoded","frame_bytes":27}
```

Rate-1/2 convolutional coding with seven-bit polynomials 0x4f/0x6d,
known initial state zero, no inverted output, and six transmitted zero tail
bits:

```json
{
  "kind": "convolutional_k7",
  "frame_bytes": 27,
  "config": {
    "generators": [79, 109],
    "invert": [false, false],
    "initial_state": 0,
    "termination": "zero_tail"
  }
}
```

This is an explicit mathematical link configuration, not a satellite preset.
For each input bit the register is `(previous_state << 1) | bit`; each output
is the parity of its mask followed by the configured inversion. Output order
is generator-array order. `unconstrained` omits tail bits and imposes no end
state. Unknown initial state, tail-biting and puncturing are not supported.

LDPC plans retain their original raw-object JSON representation. Tagged
`{"kind":"ldpc","config":...}` inputs are accepted too. Unknown fields and
ambiguous inferred defaults are rejected. The LDPC numerical implementation
is unchanged.

## Bounds and soft-information contract

The adapter validates a maximum of 65,536 coded bits. A caller can impose
narrower waveform, memory or work budgets. Convolutional channel LLRs are
clipped to ±64 before log-MAP recursion. Its output APP and extrinsic LLRs
are bounded to ±64. Extrinsic messages exclude the bit's own channel factor
directly, rather than subtracting from an already clipped APP.

Nonfinite values, wrong lengths, invalid generator polynomials and invalid
FEC dimensions fail validation. Entirely zero channel evidence returns an
unaccepted result. Ordinary decoding failure and CRC rejection are normal
unaccepted results, not fatal errors for a whole recording. CRC is never a
search objective or training signal.

## Scope

This is **not** an HDLC flag/bit-stuffing/NRZI synchronizer. Choosing AX.25
validates an aligned fixed-length UI frame and FCS, not an arbitrary on-air
AX.25 stream. The original streaming AX.25 paths remain separate.

The concatenated profile means `RS -> interstage randomizer -> convolutional`
at transmit. Its explicit `interstage_randomizer` field defaults to `none`;
the existing `ccsds_tm255`, `ccsds_tm131071`, and `ccsds_tc255` sequences are
also available. Derandomization flips information-bit soft signs before RS;
reconstruction reapplies the same randomizer before convolutional encoding.
It resets once per full interleaved RS block, not separately per RS lane.
Convolutional feedback remains in coded-bit coordinates and never includes
hard RS decisions. No convolutional interleaver or mission framing is
inferred. Any outer randomization and bit permutation in the IQ receiver
must independently match the actual transmitter; do not randomize the same
stage twice. This adapter alone does not implement every CCSDS link.

## Verification

The module's Rust tests cover an independently implemented convolutional
tapped-delay-line encoder, a hand-calculated impulse vector, an independent
bitwise-field RS encoder, and published TC128 generator constants. Exact
log-MAP posteriors and extrinsics are compared with exhaustive path
probabilities; changing one bit's own channel LLR must not change its
extrinsic message. Tests also exercise corrupted received CRCs, RS errors
inside and outside the correction radius, generator inversions, initial
states, both terminations, no evidence, nonfinite and extreme finite inputs,
invalid dimensions and legacy serialization.

These are adapter correctness tests, not measured field-yield gains over
Dire Wolf or gr-satellites.
