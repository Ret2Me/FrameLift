# Native PSK and channel-coding integration — 2026-09-11

## Outcome

The Rust generic receiver now demodulates BPSK, QPSK and OQPSK IQ into validated
frames. It includes native CSP v1/v2 and configured CCSDS AOS/USLP frame parsers,
Reed–Solomon and soft LDPC decoding, and explicit CCSDS randomizers. This is an
implemented, opt-in receiver path, not merely a capability label or an external
decoder wrapper. It does not extend the progressive FSK/audio scheduler.

The new modes have deterministic synthetic end-to-end and independent coding
vector coverage. **No real-satellite PSK yield improvement, baseline parity,
publication readiness or station deployment is established by this change.**

## Delivered scope

| Component | Implemented scope |
|---|---|
| BPSK/QPSK/OQPSK | IQ carrier recovery, rectangular/RRC receive filtering, finite full timing/rate bank, soft decisions, polarity/IQ ambiguities and OQPSK de-staggering |
| CSP | Network-order v1/v2, explicit CRC-32C scope, received flag/CRC agreement |
| AOS | Issue-5 primary header and managed transfer-frame layout with received FECF |
| USLP | Non-truncated primary header, TFDF layout/pointers, length and received FECF |
| Reed–Solomon | Configurable GF(256), shortening, byte interleaving, conventional/CCSDS dual basis; RS(255,223) preset |
| LDPC | Explicit sparse H and information-bit mapping, normalized min-sum; CCSDS TC(128,64) and TC(512,256) presets |
| Randomization | Separate CCSDS TM255, TM131071 and TC255 sequences, reset at configured codeword boundaries |
| Integration | Generic `decode`/`decode-metadata`, resumable checkpoints, deterministic independent-window workers, explicit provenance, parser/FEC CLI utilities |

The actual pipeline is IQ → soft bits → configured sync → derandomization →
optional FEC → received CRC/FECF and frame validation. Neither FEC convergence
nor a plausible header is accepted as independent telemetry validation. The
standalone parser can label structural-only data, but the generic coded-frame
receiver refuses configurations without received CRC/FECF.

## Verification

The complete enabled release-mode library suite passed: **363 passed, zero
failed, six ignored**. The complete CLI integration suite passed: **17 passed,
zero failed**. These totals include existing functionality; the extension adds
42 library tests and two CLI tests, not 380 new PSK performance experiments.
Reproducibility metadata and captured summaries are in
[the verification record](../work/psk-support-20260911-v1/verification.json).

Coverage includes:

- All three PSK families to AX.25 with frequency/phase offsets, fractional
  timing, clock error, noise and sign/conjugation ambiguities; both OQPSK
  delayed branches. A separate boundary fixture verifies retention of the
  final valid symbol.
- All three PSK families crossed with CSP v1, CSP v2, AOS and USLP: 12 synthetic
  coded links, including shortened RS, damaged symbols and TM derandomization.
- All three PSK families to TC512 LDPC and CRC-checked CSP, using a separate
  published-generator encoder, a damaged coded bit and TC derandomization.
- Independent RS generator/basis vectors, every single-error location and
  bounded multi-symbol correction; all published LDPC generator basis rows
  and every single-error position for both named presets.
- Wrong packet CRC inside an otherwise valid RS/LDPC codeword, malformed
  layouts, unsupported flags, non-finite input, silence/tone/noise controls,
  resource exhaustion and adversarial parser inputs.
- One/four-worker frame equality; normal checkpoint resumption and rejection
  of changed configuration or missing FEC provenance in a rehashed checkpoint.
- The checked-in QPSK/CSP/RS plan through the CLI with an empty runtime PATH,
  demonstrating that this native path needs no external decoder or Python.

The six ignored entries are the pre-existing frozen-archive audit, one real-IQ
window fixture, an OGG development fixture, two subprocess helper entrypoints
and a manual timing microbenchmark. Helpers are invoked by their parent tests;
the ignored archive/benchmark entries are not counted as passed or as new-mode
qualification. No new test is ignored. Formatting checks passed.

## Review fixes and research controls

Independent review found and prompted correction of a last-symbol indexing
error, a multiplicative candidate/FEC work-budget gap, and incomplete checking
of FEC validation labels during resume. Configuration and per-candidate limits
now include an aggregate effort cap. Exhaustion returns an explicit failure,
not a partially successful silently pruned result. These computational limits
are not calibrated false-alarm probabilities or wall-clock deadlines.

Existing generic non-PSK demodulation remains on its previous front-end/timing
path. The frozen campaign executable and previous optimization artifact were
not modified; their SHA256 values are retained in the verification record.
No campaign observation, station configuration or production service was changed.

## Limits and next qualification gate

PSK requires complex IQ and an explicit symbol rate/link profile. Ordinary
FM-demodulated mono OGG/WAV is not silently treated as complex PSK input.
For QPSK/OQPSK, configured baud means complex symbols per second, not the
serialized bit rate. The example is an illustrative link, not a claimed
satellite profile.

Not implemented: CSP HMAC/RDP/fragment handling; AOS header-error correction,
SDLS or service reassembly; truncated USLP; convolutional concatenation;
LDPC AR4JA/DVB-S2 presets, puncturing or complete TC CLTU processing. Generic
output retains syndrome validation but not per-codeword iteration/correction
diagnostics; those are available through `decode-codeword`.

Standard PSK synchronization and FEC algorithms are not claimed as scientific
novelty. The next evidence gate is held-out, correctly characterized real IQ
with independent receiver baselines, matching link profiles, controls and
reported unique CRC-valid frames/time/resources. No extra recovered real
telemetry count is asserted here.

See [usage, source standards and exact contracts](../docs/psk-and-channel-coding.md)
and the [runnable example plan](../configs/native/qpsk-csp-rs-example.json).
Graphify identified historical integration boundaries; primary standards and
the actual Rust implementation determined the supported subsets.
