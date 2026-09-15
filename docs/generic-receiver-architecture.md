# Generic telemetry receiver architecture

## Purpose and boundary

The system is a satellite- and network-neutral engine for recovering telemetry
from recorded IQ. SatNOGS is one compatible backend and one reproducible
baseline; it is not the product boundary. The core must also accept in-process
DSP plugins, other command-line decoders, and future learned waveform models
without introducing catalogue- or mission-specific logic into orchestration.

The architectural acceptance path is:

```text
immutable IQ + capture contract
              |
              v
 high-recall candidate selector -----> deterministic audit of rejected windows
              |
       padded CaptureSegments
              |
              v
 capability routing and bounded hypothesis plan
              |
      +-------+----------------------+----------------------+
      |                              |                      |
 waveform/demod plugins       external backends     SatNOGS-compatible
      |                              |                  baseline branch
 soft symbols + metrics       byte/soft candidates          |
      |                              |                       |
      +---- FEC / derandomize / sync / deframe as declared -+
                              |
                  protocol-specific validation
                              |
             candidate union and deterministic dedupe
                              |
             validated frames + complete provenance
```

The order of FEC, derandomization, synchronization, and deframing is declared
by a link profile rather than fixed globally. Different links place coding and
scrambling at different layers.

## 1. High-recall candidate selection

An inexpensive scorer divides a recording into fixed, overlapping windows and
ranks the likelihood that each window contains a transmission. Its score is a
routing hint, never evidence that a frame is valid. The selector is optimized
for recall: a false positive consumes compute, while a false negative can make
telemetry irrecoverable.

Selected intervals receive configurable padding before and after the estimated
activity. Nearby padded intervals are merged so burst edges, preambles, clock
acquisition, and trailing checksums are not cut away. Frequency and waveform
hints accompany the interval but do not become hard facts; an `unknown` route
must fan out to a wider hypothesis bank.

Every run records scores and selection reasons for all windows. A deterministic
sample of rejected windows is also sent through the expensive receiver bank.
This rejection audit estimates selector misses and detects recall regressions.
Thresholds, top-k limits, padding, merge distance, scorer identity/version, and
the audit seed are part of the effective configuration.

The current `candidate_selection` module implements this contract, padding,
merging, and deterministic rejection sampling. Its built-in energy scorer is a
test and fallback heuristic; it is not an AI detector or a calibrated
probability model. A sensitive learned waterfall/IQ scorer remains future work.

## 2. Capability routing and waveform plugins

The planner matches a `CaptureSegment` and its hints to declared plugin
capabilities. A waveform hypothesis contains at least the modulation family,
sample and symbol rates, decimation, carrier/timing search bounds, polarity,
and plugin-specific parameters. Plans are explicit and bounded so an experiment
can be repeated exactly even when generous compute is available.

A demodulator plugin converts IQ into a typed intermediate representation,
normally soft symbols plus synchronization and quality metrics. It declares:

- modulation families it can process;
- its output symbol kind;
- features required by downstream adapters, such as a binary timing bank;
- its stable identity and version through the campaign configuration.

Protocol names and satellite identities do not belong in waveform plugins.
Conversely, a hint such as `GMSK` must not select an AX.25 decoder implicitly:
modulation and framing are independent axes.

The current in-process waveform implementations are the phase-discriminator
path for binary FSK, GFSK, and GMSK and the independent `bell202_afsk` path for
FM-audio AFSK. The AFSK plugin performs noncoherent mark/space energy detection
and is explicitly paired with the plain-NRZI `ax25_plain` adapter. Both paths
perform bounded soft timing/rate/threshold searches. Neither is physically
applicable to PSK, QAM, CSS, or OFDM and neither may be presented as universal.

## 3. FEC, synchronization, and deframing

Link adapters consume the typed demodulator output and declare their input
requirements. A profile composes only compatible stages, for example:

```text
soft symbols -> Viterbi -> derandomizer -> ASM search -> Reed-Solomon -> frame
soft symbols -> slicer -> NRZI/G3RUH -> HDLC unstuffing -> AX.25 frame
```

Each stage retains both hard output and available confidence information.
Failed hypotheses are recorded rather than silently dropped. Expensive list
decoding or bit correction may generate candidates, but it cannot itself mark
them as telemetry; acceptance remains the job of an integrity-aware protocol
validator.

`FixedSyncFrameDecoder` is the current generic hook for fixed-size,
syncword-based links. It accepts an explicit transform and a caller-provided
validator. A syncword resemblance alone is never sufficient acceptance.

## 4. Protocol-specific validation

The receiver distinguishes a *candidate* from a *validated frame*. Validation
is selected from the declared framing/protocol stack, not by applying AX.25
rules to every byte string. Examples include a complete CRC/FCS, an authenticated
field, a mission-defined checksum, or an independently verified coding
syndrome. Plausible headers, printable callsigns, APIDs, and expected lengths
are structural evidence only.

Current built-in protocol support is deliberately narrower than the target:

- AX.25 UI over plain or G3RUH/HDLC, accepted with CRC-16/X.25;
- AX.25 carrying one complete CCSDS Space Packet, with the outer AX.25 FCS,
  AX.25 structure, and inner CCSDS primary-header/length checks;
- raw CCSDS Space Packet discovery only when the caller supplies an additional
  integrity validator. The six-byte CCSDS primary header has no checksum, so
  APID, version, sequence count, and length alone never establish a valid raw
  telemetry frame;
- CCSDS Version-1 TM Transfer Frames behind a configured acquisition marker,
  exact frame length, strict primary-header checks, and either the standard
  FECF or a caller-supplied integrity validator;
- fixed-sync mission adapters with an explicit transform and validator.

For AX.25 plus CCSDS, an optional inner packet validator can add a packet error
control field or mission rule. Without it, the integrity claim comes from the
complete outer AX.25 frame; the inner CCSDS check remains structural.

## 5. External file backends

`ExternalFileBackend` provides a neutral boundary for decoders that cannot or
should not be linked into the Python process. It receives a `CaptureSegment`
containing a file path, sample format/rate, start/count in complex samples, and
routing hints. Each backend advertises modulation, framing, FEC, and accepted
sample-format capabilities.

An argv-builder converts the segment into an argument vector. The runner never
uses a shell, applies an explicit wall-clock timeout and combined stdout/stderr
byte limit, and parses output through a backend-specific callback. Input,
invocation, launch, timeout, output-limit, non-zero-exit, and parser failures
become structured results with zero candidates instead of exceptions that stop
the campaign. Successful byte candidates retain backend version, exact argv,
capture coordinates/hints, and parser metadata.

This boundary is process isolation for campaign reliability, not a security
sandbox. Executables and argv builders must still be trusted and provisioned by
the operator. Backend output is candidate material and is subject to the same
protocol validation policy as in-process output.

## 6. Union, deduplication, and provenance

All branches feed a common candidate ledger. The ledger first preserves every
detection, then groups identical content within a declared transmission event
and protocol interpretation. A stable content digest is the payload identity;
event identity prevents the same periodically repeated bytes from being
incorrectly collapsed across time. When framing bytes or FCS are removed, both
the original frame and normalized payload digest must remain traceable.

Deduplication never discards origin records. One accepted frame can therefore
show that it was independently recovered by the SatNOGS-compatible path, a
phase-first hypothesis, and an external decoder. Deterministic ordering uses
content and canonical provenance rather than process completion order. The
final record includes:

- capture hash and segment coordinates;
- backend/plugin identity and version;
- complete waveform, timing, FEC, and framing hypothesis;
- validation layers and their results;
- parent candidate identities and all independent recovery paths;
- effective configuration fingerprint.

Raw CRC collisions or structurally plausible packets remain visible as
rejected candidates, but are not counted as recovered telemetry.

## 7. SatNOGS-compatible baseline and no-regression policy

The existing SatNOGS-compatible flowgraph remains an independent branch for
recordings and modes where it applies. It serves two purposes: a reproducible
baseline for experiments and a no-regression fallback in deployed ensembles.
New plugins do not replace or modify its frames.

For a supported capture, campaign metrics are reported separately as:

```text
baseline yield
new-branch yield
union yield = validated(baseline candidates union new candidates)
incremental yield = union yield - baseline yield
```

The operational output is the validated union. Consequently, a weaker new
demodulator cannot reduce an otherwise successful baseline decode; it can only
add validated frames. Baseline failure is isolated like any other backend and
does not prevent other branches from running. This policy is about preserving
known receiver behavior, not coupling the generic engine to SatNOGS.

Historical reconstruction and exact-version limitations must remain in the
provenance. A source-compatible replay is not described as bit-identical to an
old live deployment unless its complete runtime and input placement were
reproduced.

## Current implementation versus roadmap

| Layer | Available now | Roadmap |
|---|---|---|
| Candidate selection | Pluggable scorer contract, deterministic energy fallback, padding/merge, rejected-window audit | Calibrated high-recall AI scorer, dataset-level recall monitoring and automatic threshold governance |
| Waveforms | Binary FSK, GFSK, GMSK and Bell-202 AFSK; native IQ BPSK/QPSK/OQPSK with bounded timing, mapping and carrier recovery | Higher-order PSK/QAM, CSS/LoRa, OFDM and further families through capability plugins; blind symbol-rate estimation and broader real-capture qualification |
| FEC and transforms | G3RUH/NRZI/HDLC; configurable GF(256) Reed–Solomon including CCSDS dual basis and byte interleaving; sparse-H LDPC with CCSDS TC128/TC512 presets; three CCSDS randomizers; fixed-sync adapter | Reusable convolutional/Viterbi, BCH/Golay, Turbo, AR4JA/DVB-S2 LDPC and further interleaving profiles |
| Protocols | AX.25; AX.25 plus CCSDS Space Packet; CCSDS TM; CRC-checked CSP v1/v2 and configured CCSDS AOS/USLP transfer-frame subsets through coded sync; raw CCSDS Space Packets only with external integrity validation | Additional CSP modes, AOS/USLP reassembly, security/header-correction profiles and mission-specific adapters |
| External programs | Bounded file backend with capability metadata, provenance, safe argv, timeout/output limits, and structured failure | Backend discovery/configuration, containerized isolation where required, soft-symbol interchange and resource scheduling |
| Aggregation | Deterministic dedupe within current receiver/backend results; campaign evidence already demonstrates baseline-plus-new union analysis | One persistent cross-backend candidate ledger with event-aware dedupe and complete multi-path provenance |
| Baseline | Existing SatNOGS-compatible replay/comparison path | First-class campaign adapter executed alongside every applicable generic plan |

The native PSK and channel-coding additions are opt-in generic receiver paths,
not extensions of the progressive FSK scheduler. See
[the explicit supported subsets and verification limits](psk-and-channel-coding.md).

“Support all protocols” therefore means an open, typed plugin architecture and
a growing tested capability registry, not sending every signal through one
algorithm or claiming complete coverage today. A new modulation, FEC, or frame
format becomes supported only after its plugin declares a reproducible contract,
has positive and negative fixtures, and ends in an appropriate integrity check.

## Campaign invariants

1. Raw IQ and its capture contract are immutable and content-addressed.
2. Selection hints may prioritize work but never validate frames.
3. No single modulation, framing scheme, network, or satellite is assumed by
   the orchestration core.
4. Header plausibility without an integrity mechanism is not decoded telemetry.
5. Every search is bounded and its complete effective configuration is stored.
6. A backend failure affects one attempt, not the rest of the campaign.
7. Deduplication preserves every independent provenance path.
8. The SatNOGS-compatible branch is retained wherever applicable, and reported
   baseline yield can never be silently replaced by a weaker experimental path.
