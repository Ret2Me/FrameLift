# Coherent CPM for variable-length HDLC

The optional `receiver.coherent_cpm` lane connects received complex IQ directly
to the finite-pulse CPM log-MAP detector and then to strict AX.25 UI deframing.
Frame lengths are discovered from received HDLC flags. No fixed codeword
length, artificial synchronization header, FEC, reference payload or repaired
checksum is introduced. Existing baseline and anchor-trained sequence results
are retained unchanged and unioned with the new results.

CPM bit marginals are hard-decided before NRZI/descrambling/HDLC processing.
This is not a joint maximum-likelihood decoder over every HDLC packet and
line-code state.

## Acquisition and state assumptions

Acquisition searches only the declared integer sample offsets and carrier
centers. It integrates the received phase increments per symbol and looks for
at least eight consecutive, exact decoded HDLC flags. The final declared number
of flags in each maximal run forms the pilot immediately before the following
traffic. There is no CRC-directed ranking, timing adjustment or candidate
pruning. More candidates than the declared bound cause a resource error.

For plain NRZI, protocol flag bits and the received predecessor level determine
a candidate physical pilot. For G3RUH, the receiver additionally infers the
preceding 17 coded bits from received acquisition decisions. It propagates that
state through the subsequent known logical flags and checks agreement with
the observed physical pilot. It **does not assume an all-zero register**, a
known transmitter reset, or a state taken from archived payloads.

This is a **conditional, signal-derived line-state hypothesis**, not exact
marginalization over all G3RUH states and not an independently authenticated
preamble. Acquisition errors can prevent admission. The candidate pilot must
also pass a complex-waveform coherence gate. Only this pilot estimates complex
gain, residual carrier and noise. Payload bits have uniform priors and must
pass the original received FCS and AX.25 UI validation after detection. The
same 16-bit checksum is checked by two implementations, not two independent
16-bit constraints.

The CPM kernel marginalizes its unknown exterior phase/history symbols and
uses every actual IQ observation in the candidate window. It assumes a known
rational modulation index, an integer sample clock, a fixed gain/carrier, and
either a rectangular frequency pulse or the explicitly truncated Gaussian
pulse. Gaussian pulse support is +/- two symbols; this is not a claim of an
exact model for an infinite pulse or arbitrary real radio distortion.

## Profile

Add the following to an FSK-family `decode-recovery-hdlc` receiver profile. This
example describes h=0.5, BT=0.5, six samples per symbol and an already centered
recording. It is an engineering profile, not a new verified mission binding.

```json
"coherent_cpm": {
  "modulation_index_numerator": 1,
  "modulation_index_denominator": 2,
  "gaussian_bt": 0.5,
  "phase_offsets_samples": [0, 1, 2, 3, 4, 5],
  "carrier_centers_hz": [0],
  "max_residual_carrier_hz": 100,
  "preamble_flags": 8,
  "maximum_candidate_symbols": 4096,
  "maximum_candidates": 32,
  "minimum_training_coherence": 0.8
}
```

Set `gaussian_bt` to `null` for rectangular CPFSK. Omit `coherent_cpm`, or set
it to `null`, for the original receiver and v1 report representation. The new
lane is admitted only for FSK-family IQ, integer 2–32 samples per symbol,
configured zero clock error, rational 0<h<=1 and at most eight coarse carrier
centers. Carrier drift and fractional timing are not silently approximated.
The discriminator pilot search requires a coarse center close enough to
preserve the physical level decisions; the residual carrier fit comes later.

The maximum candidate length includes the pilot. It is a resource cap, **not
the packet length**. A packet longer than the remaining window cannot pass
complete-flag deframing in this lane; the baseline can still recover it. The
4096-symbol CPM kernel limit therefore does not cover every 1023-byte AX.25
frame. Records without a sufficient flag preamble receive no CPM candidate.

## Accounting

Both acquisition and the complete candidate trellis are charged to the explicit
work budget before their expensive operations. Candidate-limit or work-limit
failures fail the run; low pilot coherence is a recorded rejected hypothesis.
Receipts retain pilot and candidate lengths, integer timing, carrier center,
conditional line-state source, fitted channel diagnostics and source bounds.
Frame provenance covers the whole candidate IQ window, including the training
pilot; symbol intervals identify the received stuffed frame inside it.

Tests use an independent transmitter with variable payload lengths, actual
NRZI/G3RUH state, Gaussian filtering and phase integration. They include
captures starting mid-stream, wrong transmitted FCS, truncated closing flags,
noise, baseline preservation and disabled-profile serialization. These
development checks are not evidence of multi-mission field gain or calibrated
false-alarm probability. The main [HDLC documentation](recovery-hdlc.md)
describes the unchanged hard-slice and separate anchor-trained BCJR lanes.

One controlled GMSK test adds an isolated two-pi phase excursion to four
received IQ sample intervals, returning to the original phase afterward.
On that identical disturbed input, the declared phase-discriminator baseline
recovers zero frames and coherent CPM recovers the transmitted 118-byte frame,
including its received FCS and 100 information bytes. The transmitter's bytes
are used only by the test assertion, never by acquisition or detection. This
is a deterministic development counterexample showing retained phase evidence,
not an estimate of an average gain on satellite recordings.
