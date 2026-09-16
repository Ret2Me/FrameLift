# Experimental IQ recovery pipeline

`decode-advanced-iq` connects acquisition, channel estimation, soft sequence
detection, FEC feedback, repeat combining and subtractive interference
cancellation. It is an **opt-in development path**, not a replacement for
the existing generic/progressive receivers or a qualified production release.
Existing frozen campaigns and paper results are unchanged.

## Implemented boundary

| Component | Current support |
|---|---|
| File input | Explicit bounded window of ci16_le, cf32_le or cf64_le IQ |
| Waveform | BPSK, QPSK, OQPSK (rectangular or explicit RRC); continuous-phase binary FSK/GFSK/GMSK; FM-carried AFSK |
| Acquisition | Bounded carrier/clock/phase bank; second/fourth-power PSK carrier estimation; discriminator or tone-energy FM observations; 32–128-bit sync (quadrature: 64–128) |
| Channel | Preamble-estimated three-tap Gaussian surrogate; separate I/Q channels; optional damped piecewise updates from BCJR triple marginals |
| FEC | Explicit LDPC, K7 convolutional, Reed–Solomon, concatenated RS/randomizer/K7, or uncoded fixed frames; see [coding contract](recovery-code.md) |
| Integrity | Received AX.25 FCS, CCSDS TM FECF or an existing integrity-aware CSP/AOS/USLP validator |
| Repeats | Legacy CRC32C key or explicit existing mission-header checksum/identity layout; nonoverlapping samples and pairwise likelihood-consistency gate |
| Cancellation | Waveform-specific remodulation of verified codewords; training-only gain/frequency fit, held-out residual and total-energy checks; legacy three-tap rectangular-BPSK path retained |
| Refinement | Optional decoder-assisted PSK timing/clock/carrier/drift fitting; optional coherent finite-pulse CPM with cached channel metrics |
| Execution | Ordered parallel candidate reduction; immutable resumable IQ windows on Linux; CPU default, existing CUDA FIR backend for RRC filtering |

This does **not** make the new pipeline compatible with arbitrary satellite
transmitters. TC LDPC support alone is not CLTU support. A sync plus TC128-coded
CSP payload in the development fixtures is an artificial test link, not a claim
that an identified mission uses it. Configure actual framing, coding, waveform
and integrity rules before applying a profile to a recording.

The new features cover seven digital modulation families, not every possible
satellite waveform or every existing application pipeline. They remain a
**fixed-sync, explicitly configured link**. Fixed-frame AX.25 integrity is not
streaming HDLC acquisition. Ordinary AX.25 audio/generic pipelines, Meteor
mission assembly, CW and analog SSTV retain their existing receivers; no LDPC
is invented for them. RS uses hard correction, not fabricated soft feedback.
PSK refinement and coherent CPM are separate optional branches, not one exact
joint optimization covering every waveform. New BCJR/LDPC CUDA kernels are not
provided; only the existing FIR arithmetic can use that backend.
OGG/WAV FM audio cannot substitute for coherent IQ in this command.

For PSK, memory in I and Q is handled separately; serialized adjacent bits do
not become artificial inter-branch ISI. For FSK/GFSK/GMSK, the BCJR observation
baseline model is a **post-discriminator approximation**. The optional
[coherent CPM lane](coherent-cpm.md) models the finite sampled pulse directly
on IQ. AFSK uses fitted mark/space tone energies. Gaussian LLRs are conditional
on those models; discriminator, pulse-filter and tone-energy noise need not be
independent Gaussian noise on field recordings. The v2 report records the
observation-model label explicitly. Do not claim calibrated probabilities or
universal gain from these engineering tests.

## Data flow and acceptance

1. The unchanged source window is acquired without expected payload bytes.
   Known sync bits estimate channel taps, bias and residual noise. Timing
   variants of one burst are ranked using pilot fit quality, not successful CRCs.
2. The receiver retains a single-pass result and adds fixed-channel turbo and
   optional varying-channel attempts. Only genuine extrinsic messages pass
   between BCJR and LDPC/K7 invocations; RS/uncoded do not invent feedback.
   New non-LDPC branches include endpoint observations while marginalizing
   unknown exterior symbols; legacy LDPC arithmetic remains unchanged.
   CRC is an acceptance/early-stop gate,
   never a channel-fitting or bit-repair objective.
3. On original IQ only, independently received copies may supply additional
   evidence. Combining uses channel-only extrinsic LLRs, **not** LDPC posterior
   values or feedback. Every copy must carry the same verified repeat key.
4. A separately integrity-validated frame with a checked reconstruction witness
   can be remodulated. Uncoded/convolutional links do not claim an LDPC syndrome.
   New waveform fits use alternating symbol-duration intervals anchored at
   source-window sample zero. Fit complex gain and bounded residual frequency
   on training intervals, test the other intervals, then check total energy
   before subtraction. AFSK additionally fits audio-tone phase on training
   samples of the verified waveform and ranks nine nearby timing hypotheses on
   training samples only. Exactly one winner receives the residual check;
   holdout or CRC results never select a different timing fit. Initial
   acquisition has already used the sync: this is a reconstruction guard,
   not an independent statistical test of the entire receiver. Rejected
   reconstructions leave IQ unchanged. Reacquire and retain the accepted union.

The varying-channel update solves regularized normal equations from posterior
triple moments. Eight pilot-equivalent observations anchor each fit; damping is
limited to 0.5, parameter changes are bounded, and residual variance stays
between one quarter and four times its pilot estimate. This is an approximate
iterative receiver under a stated model, not a field-calibrated probability
model. It neither guarantees improvement nor guarantees convergence.
The sync pattern must identify all four fitted parameters (bias and three
taps); a rank-deficient pilot is rejected rather than assigned invented taps.

### Repeat identity is a transmitter contract

When repetition is configured, the frame layout is:

`sync | key (4–32 bytes) | CRC32C of key (4 bytes, big-endian) | LDPC codeword`

That is the legacy layout. `repetition.mission_header` instead describes an
existing 4–256-byte header after sync, its protected byte interval, checksum
location/type (`crc32c_be` or `crc16_ccitt_false_be`), and disjoint identity
fields inside the protected interval. The payload code is configured separately.
It does not append a new header, treat a Golay length field as an identity, or
infer a repeat guarantee from a spacecraft ID. A documented immutable-codeword
contract is mandatory. No real mission profile is certified by this adapter alone.

The transmitter must guarantee that a key identifies one immutable coded
payload within `maximum_gap_symbols`; an arbitrary timestamp or satellite ID
does not satisfy this contract. This header is not silently inferred from a
mission's payload. Profiles lacking independently verifiable repetition identity
must leave repetition disabled. `combine: false` retains identical header
parsing for a fair non-combining control.

Overlapping sample ranges, phase variants and copies from SIC residuals cannot
reinforce one another. Code/interleaver/randomizer are common to a run. A
correlation gate rejects inconsistent copies even with matching keys. Payload
FEC and CRC still have to pass. CRC checks are error-detection evidence, not
cryptographic authentication of a transmitter. Combined-only packets are not
used for further cancellation in this implementation.

## Running and reproducing

### Waveform profiles

`bpsk_rectangular` retains the original v1 arithmetic and result schema. New
profiles use `bpsk`, `qpsk`, `oqpsk`, `fsk`, `gfsk`, `gmsk` or `afsk` and produce
v2 reports without extensions. Non-LDPC or recovery extensions produce v3
reports. PSK may omit `waveform` for rectangular pulses; shaped/FM profiles
must explicitly supply their transmitter parameters:

| `modulation` | `waveform` example |
|---|---|
| `bpsk`, `qpsk`, `oqpsk` | `{"type":"psk","pulse":{"type":"rectangular"}}` |
| `bpsk`, `qpsk`, `oqpsk` | `{"type":"psk","pulse":{"type":"root_raised_cosine","rolloff":0.35,"span_symbols":8}}` |
| `fsk` | `{"type":"fsk","deviation_hz":1100,"gaussian_bt":null}` |
| `gfsk` | `{"type":"fsk","deviation_hz":1100,"gaussian_bt":0.5}` |
| `gmsk` at 3000 baud | `{"type":"fsk","deviation_hz":750,"gaussian_bt":0.5}` |
| `afsk` | `{"type":"afsk","mark_hz":1200,"space_hz":2200,"fm_deviation_hz":3000}` |

Rates are **symbol rates**: quadrature transports two serialized bits per
symbol; the other families transport one. OQPSK tests both half-symbol-delayed
branches; quadrature acquisition resolves quadrant/conjugation ambiguities
against the received sync. RRC rolloff is 0.01–1 and span 2–16 symbols.
Gaussian BT is 0.2–1, with a centered unit-gain Gaussian truncated at ±2
symbols. GMSK enforces deviation = symbol rate / 4 (h = 0.5).
AFSK requires at least 8 samples per bit; other modes require 2–128.
Arbitrary discontinuous-phase FSK and unknown pulse shaping are not implied.

These distinctions follow the conventional
[GNU Radio GMSK definition](https://wiki.gnuradio.org/index.php/SignalProcessing)
and the [FM discriminator/audio representation documented by gr-satellites](https://github.com/daniestevez/gr-satellites/blob/main/docs/source/components.rst).
They are waveform contracts, not claims of a newly invented modulation.

### Commands

```sh
cargo build --release --bin telemetry-yield-rs
target/release/telemetry-yield-rs decode-advanced-iq \
  --input recording.cf32 --profile mission.json --output new-iq-session
```

The JSON file has strict `format`, `sample_rate_hz`, `start_sample`,
`sample_count` and `receiver` fields. `receiver` is the public
`advanced_iq::Config`; unknown fields and unsupported formats fail explicitly.
Use `ci16_le`, `cf32_le` or `cf64_le` for `format`. Counts/rates, all hypotheses,
LDPC matrices, permutations, iteration counts and aggregate work are bounded.
An exceeded work budget is an error, never a successful truncated result.
The supplied output directory must not exist. This command does not yet resume
progressive sessions or impose the existing progressive wall-clock deadline.

Generate the complete reproducible development cohort and example profiles:

```sh
cargo run --release --example advanced_iq_benchmark -- --output new-iq-benchmark
target/release/telemetry-yield-rs decode-advanced-iq \
  --input new-iq-benchmark/collision-00/input.cf32 \
  --profile new-iq-benchmark/collision-00/all-profile.json \
  --output new-collision-replay
```

The benchmark retains 112 generated recordings: 80 positive cases (clean,
three-tap ISI, collisions, complementary repeated erasures) and 32 negative
controls (noise or valid FEC with invalid received CRC). Each of three arms sees
the exact same retained cf32 samples. Channels are estimated from the received
pilot, unlike the earlier oracle-channel block experiment. These are still
synthetic experiments, with known modulation and framing, not orbital evidence.
The single, turbo/joint and all-features arms have different compute budgets.

Each CLI run records source, runtime and configuration identities before
decoding, an exact consumed-IQ hash, and frame/sample provenance afterward.
Source/runtime changes are checked before result publication. After a session
directory is created, failures produce an explicit receipt; invalid configuration
is rejected before creating that directory. Reported absolute sample ranges include the file-window
offset. No downloaded reference frames enter demodulation.

The original 112-file experiment remains reproducible with the legacy profile.
Generate the separate ten-waveform, 240-file matrix with:

```sh
cargo run --release --example multimode_iq_benchmark -- --output new-multimode-benchmark
```

It covers seven families and three additional PSK/RRC shapes, with four cases
per clean/collision/repeat/constructed-erasure/invalid-CRC/noise group. It retains
exact cf32 inputs, profiles, truth (scorer-only), executable identity and both
receiver reports. The baseline is the **same new frontend with optional
mechanisms disabled**, not the entire previous FrameLift release or an external
decoder. Symbol-erasure stress fixtures are explicitly artificial, not a
validated satellite channel model. More methods also mean more computation.

See [original development results](../reports/advanced-iq-development-20260916.md)
and [multi-mode results](../reports/multimode-iq-development-20260916.md).
