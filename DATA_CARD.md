# Data card: telemetry-yield evidence corpus

Version: release-candidate evidence card v1, 2026-09-02.

## Purpose and unit of analysis

The corpus evaluates a generic recorded-IQ telemetry receiver. It is not a
single homogeneous dataset. Every result must preserve its evidence layer and
unit of analysis:

| Layer | Unit | Valid use | Invalid use |
|---|---|---|---|
| Real satellite IQ | one independently scheduled observation | end-to-end trusted-frame yield on identical source bytes | physical-layer BER without transmitted-bit truth |
| RML24 hardware-in-the-loop records | one 2,048-sample record | AMR and physical bit recovery against released labels/truth | SatNOGS frame yield or real space-to-ground generalization |
| Demodulated public audio | one recording | decoder determinism and protocol fixtures | raw-IQ receiver sensitivity |
| Synthetic and null controls | one generated case or exposure interval | regression, false-accept and fail-closed testing | evidence of real-signal recall |

The primary release claim, if its preregistered gates pass, is paired
incremental trusted-frame yield on real IQ. RML24 results are secondary
physical-layer evidence and are never pooled into that frame count.

## Sources

### Real satellite IQ

- CAMRAS/SatNOGS recordings are observation-scoped complex-IQ captures. A
  recording enters a benchmark only after byte hash, sample format, sample
  rate, Doppler state and mission/profile routing have been recorded. Mutable
  SatNOGS status and waterfall labels are timestamped separately.
- The user-supplied PolyITAN bucket was inventoried as 290 IQ objects at the
  2026-09-02 snapshot. Catalogue metadata is used only to freeze cohorts and
  route legal decoder configurations; a catalogue zero is not treated as
  proof that no transmission exists.
- Zenodo record 13371136 is a 4,773,064-byte `cf32_le` SigMF recording at 500
  sample/s from a gr4-packet-modem QPSK test through the Intelsat 37e C-band
  transponder. Its SigMF SHA-512, Zenodo file checksum and local readable
  sample count are verified. It is a separate modem benchmark, not amateur
  satellite telemetry and not part of a SatNOGS-relative claim.
- The strongest completed real-IQ diagnostic before the new independent
  campaign is CANVAS observation 14366383: the standalone native v2 union
  yielded 10 unique strictly valid AX.25+CCSDS payloads and the executable
  source-compatible baseline yielded 6 on the identical `cf32` bytes. This
  capture was inspected before the final transfer test and is therefore
  labelled `transfer_holdout_not_preregistered`, not confirmation evidence.

Raw IQ is not embedded in the software release. Its immutable source URL or
object identity, size and cryptographic digest are kept in observation plans
and result artifacts. Reproduction requires separately obtaining the exact
source bytes.

### RML24

RML24 is from Zhang et al., *Cognitive Radio for Satellite TT & C System: A
General Dataset Using Software-defined Radio*, Scientific Data 13, 860 (2026),
article DOI `10.1038/s41597-026-07182-7`, dataset DOI
`10.5281/zenodo.17800058`.

Both published archives were downloaded and verified against Zenodo size and
MD5 metadata. The safe Pickle conversion produced 1,323 paired memory-mapped
IQ/bit groups and 1,323,000 usable local records across 21 released modulation
labels. This differs from the paper's stated 1,386,000 records/22 schemes and
is reported as observed local artifact content rather than silently imputed.
The HDF5 artifact has IQ and labels but no usable transmitted-bit truth; bit
truth is read only from the paired Pickle-derived shards.

The current receiver supports physical bit recovery for 252,000 BPSK, QPSK,
OQPSK, GMSK/2FSK-labelled records. Runtime recovery receives the routed
modulation and symbol rate for this benchmark; truth bits are isolated to the
scorer. Historical BER scoring permits a bounded truth-aided alignment and
constellation ambiguity resolution, so it is explicitly diagnostic rather
than a blind end-to-end metric. AMR experiments receive IQ-derived features
only, but are post-development diagnostics because the corpus had already been
used by earlier work.

### Demodulated fixtures and metadata

- The public golden corpus contains 92 demodulated recordings; 87 were mapped
  to profiles and executed twice with gr-satellites 5.9.0. It measures adapter
  determinism, not IQ sensitivity.
- Bounded snapshots of SatNOGS transmitter metadata and gr-satellites SatYAML
  definitions support routing audits. They are not signal examples.
- Protocol validators accept only structurally legal frames with the required
  integrity evidence. CRC-valid but structurally illegal candidates remain
  rejected and never contribute to trusted yield.

## Splits, freezing and leakage controls

- A benchmark plan records the exact observation/object list, hashes,
  baseline, native configuration, success rule and null controls before native
  decoding begins.
- Development, transfer and confirmation observations are event-disjoint.
  Previously viewed captures cannot be relabelled as blind confirmation.
- The same source bytes are supplied to every compared executable branch.
  Trusted payloads are deduplicated by payload hash after protocol validation.
- IQ filenames, observation IDs, expected payload bytes, reference hashes,
  demoddata, event timestamps, transmitted bits and scorer outputs are forbidden
  from runtime feature selection unless the experiment explicitly labels them
  as benchmark routing inputs.
- RML24 group-disjoint AMR holds out complete modulation/SNR/symbol-rate cells.
  The record-level AMR split is retained only as a weaker diagnostic.

## Quality, bias and missingness

- Public positive observations and golden fixtures are success-enriched and do
  not estimate prevalence in an unfiltered archive.
- Zero-yield observations can mean no signal, inadequate metadata, an
  unsupported waveform, a weak receiver or a genuinely undecodable capture;
  zero is not assigned a cause without independent evidence.
- CAMRAS sample rate and Doppler processing are heterogeneous and mandatory
  per-recording fields.
- RML24 combines simulated channel impairments with real RF-chain effects; it
  is not a captured satellite propagation link. Its short records do not carry
  an AX.25/CCSDS frame-yield truth contract.
- Current modulation coverage is incomplete. Unsupported PSK/QAM/APSK,
  composite PCM, CCSDS AOS/USLP, AX.100 and mission-specific FEC paths must be
  reported as unsupported, never as decoded negatives.

## Safety, privacy and access

Recorded spectrum may contain unrelated third-party transmissions. Release
artifacts therefore contain hashes, counts and protocol summaries rather than
unreviewed raw payload contents. The user has attested source permissions for
the research. Attribution, a non-disclosing secret scan and an explicit
release allow-list are still mandatory before packaging; these checks are
separate from technical experimentation.

## Reproduction and provenance

Dependencies are locked by `pyproject.toml` and `uv.lock`. Plans and reports
carry schema versions, source/configuration hashes and environment evidence.
Large inputs are processed by bounded readers or memory maps; safe converters
reject truncated, oversized, ambiguous or incomplete artifacts. Primary
results require at least two deterministic repeats plus an independent audit.

Key machine-readable evidence is in:

- `reports/polyitan-iq-comparison-v2.json`
- `reports/rml24-physical-ber-carrier-timing-v2-v1.json`
- `reports/rml24-carrier-timing-v2-independent-audit.json`
- `reports/rml24-amr-independent-audit.json`
- `reports/release-evidence-current-v1.json`
- `reports/release-readiness-current-v1.json`

This card describes the evidence corpus. It does not itself make the current
candidate publishable; the fail-closed release assessment is authoritative.
