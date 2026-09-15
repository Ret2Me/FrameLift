# Execution gates

Snapshot: 2026-09-01 UTC.

| Gate | Requirement | Current state |
|---|---|---|
| G0 authorization | Network/install/contact/identity scope approved | **INTERNAL RESEARCH PASS:** user explicitly authorized continued research and represented that source permissions are held or will be sought; external contact and publication remain separate actions |
| G1 resources | Pilot minimum 4 vCPU/16 GiB/150 GB; scale target 16–32 vCPU/32–64 GiB | **PILOT PASS / SCALE FAIL:** 4 vCPU, 32 GiB RAM, 307 GiB filesystem |
| G2 source contracts | Frozen source snapshots, historical C0, label dictionary | **PARTIAL:** observation 12511021 has the full job dictionary and a narrow candidate C0, but its dirty client tree and installed flowgraph tag are unrecoverable; exact C0 remains unavailable |
| G3 licenses | Evidence and publication review per source | **INTERNAL RESEARCH PASS / PUBLICATION PENDING:** user authorization removes this as a compute and ingest blocker; source claims remain recorded and are not independently verified by this project |
| G4 protocol | Cohorts, controls, adjudication, and leakage rules frozen | **PILOT PASS** |
| G5/A0 | Deterministic golden matrix, CRC, SigMF, GPS | **FAIL:** 9 of 87 mapped golden recordings were nondeterministic; controlled runs show different frame sets rather than ordering-only changes; CRC/SigMF/GPS subchecks pass |
| G5/A0.5 | CAMRAS link, signal-state check, byte-identical replay | **CALIBRATED PASS / BLIND PENDING:** a phase-first soft-list replay recovers 4/4 SatNOGS payloads byte-for-byte with full CRC, deterministic repeats, and 0/2 negative-control accepts; timing/localization/lag are P-POS reference-calibrated |
| A1 | SatNOGS DB × SatYAML parameter comparison | **DATA PASS:** 5,010 DB records, 675 SatYAML definitions, 498 conservative matches |
| A2 bounded ingest | Immutable conversion contracts and event grouping | **PLAN PASS:** 1/2 local recordings ready; 12511021 remains fail-closed on spectral sign and time basis |
| A4 bounded sweep | Lazy L0/L1 planner with cost rejection | **PLAN PASS:** 320 attempts fit the pilot budget; a ten-recording expansion fails all three limits |
| G6 scale | Correct pilot, extrapolation, and approved budget | **STOP:** blocked by A0, G2, and missing blind/general A0.5 validation; bounded engineering may continue |
| G7 publication | Legal/ethical review, data card, attribution, secret scan | **STOP:** no publication-ready dataset or model |

## Evidence behind the stop decision

- Golden matrix: 92 WAVs, 87 mapped, 174 successful gr-satellites runs;
  78/87 deterministic and 64/92 both deterministic and nonempty.
- Golden diagnosis: timeout, clock, diagnostic sinks, telemetry submission,
  one-CPU affinity, and fixed `max_noutput_items` were excluded. Throttling
  is case-specific and does not resolve the gate. GNU Radio 3.10.12 exposes
  only the TPB scheduler; `GR_SCHEDULER=STS` falls back to TPB.
- CAMRAS metadata join: 10/10 observation IDs linked, but mutable live labels
  require timestamped snapshots.
- CAMRAS signal contract: observation 9614528 is approximately 48 kS/s and
  pre-Doppler; historical flowgraph evidence proves 12511021 is 57.6 kS/s and
  post-Doppler. A single global ingest setting is invalid.
- Bounded CAMRAS contract audit: 20/20 HEAD requests succeeded without IQ body
  downloads, but only 7 rows could be joined to cached API metadata after the
  live SatNOGS endpoint returned errors/timeouts. The evidence does not support
  archive-wide rate or Doppler defaults.
- End-to-end replay: the reconstructed 57.6 kS/s SatNOGS downstream path,
  component swap, polarity inversion, plain AX.25, and direct gr-satellites
  variants did not match any of four SatNOGS reference frames.
- Exact `gr-satnogs` 2.3.4 decoder sources accept zero frames from 5,294,190
  reconstructed hard symbols. A synthetic control passes byte-identically;
  NRZI, G3RUH, LSB-first byte order, HDLC, and FCS are therefore validated.
- Reference-frame patterns correlate 87.5–95.2% near all four expected event
  windows, while repeated M&M sliced streams agree globally at no more than
  52.62%. The remaining local failure is hard-symbol/clock-recovery stability.
- A bounded M&M sweep executed 250 of at most 256 trials in 7.57 seconds and
  recovered zero CRC-valid, byte-identical frames. Its positive-window
  correlation is diagnostic only and is not an acceptance criterion.
- A separate single-process clock-bank replay corrected an audited windowing
  error, then decoded all 288 preregistered phase/rate hypotheses in each of
  the half-open 91--103 s and 329--341 s windows. Five synthetic controls pass,
  both real runs are byte-identical, and the real result remains zero complete
  AX.25 frames with at least 14 payload bytes and valid FCS. The window
  positions were derived from earlier reference correlation, so this is a
  bounded P-POS calibration failure rather than independent blind validation.
- A later clipping-robust receiver computes phase differences before the
  linear low-pass, samples a frozen timing bank, and searches bounded raw-level
  corrections ranked by soft reliability and lag disagreement. On observation
  12511021 it recovers all four reference payloads (200, 250, 101, 101 bytes)
  with complete HDLC unstuffing and valid CRC-16/X.25. Two repeats are equal.
- A reference-separated decode-only process is allow-listed to the soft/level
  artifact directory and serializes four CRC-valid candidates before a second
  process reads the references; the post-decode audit matches 4/4 byte-for-byte.
  Two off-burst controls, each repeated twice through the same candidate path,
  yield zero CRC-valid frames. The 250-byte event uses a left delimiter at
  Hamming distance 3 but an exact right flag and a fully CRC-protected body.
- The successful timing, event localization, and lag choices were calibrated
  using P-POS references. Reference bytes, payload lengths, and expected FCS
  are absent from the candidate search, but this separation does not turn the
  experiment into blind validation. The result establishes calibrated replay
  parity on the benchmark, not archive-wide decoder parity.
- The separated run evaluated 68,924 structured candidate bodies. A uniform
  16-bit-CRC heuristic would expect about 1.05 accidental CRC hits at that
  multiplicity, so CRC alone is not independent validation after tuning. The
  four exact payload matches and negative controls are stronger evidence, but
  a preregistered blind holdout remains required for a general performance
  claim.
- On independent observation 12512778, the frozen front-end with separately
  reference-calibrated timing recovers 2/4 payloads exactly (200 and 101
  bytes). Across 185,251 completed list attempts it also emits one CRC-valid
  101-byte payload that is not the reference; the post-decode audit rejects it.
  This is transfer evidence and an empirical CRC-multiplicity warning, not a
  blind acceptance result. The 250-byte search is unresolved and unledgered
  exploratory work is excluded.
- The corrected deterministic result removes TPB scheduler nondeterminism from
  that substitute path. It does not prove every file offset or front-end; the
  remaining boundary is at or before discriminator conditioning and hard-symbol
  decisions.
- Replaying the tag-1.5 IQ scale 16,768 instead of 16,384 leaves the complete
  frame result unchanged, so amplitude scaling is not the missing correction.
- Historical C0 audit: 10/10 cached records retain observation identity, but
  none retains the full exact decoder contract. For 12511021, the compatible
  tag-1.5 candidate confirms 57.6 kS/s and suggests G3RUH/M&M, but the dirty
  client source, installed flowgraph tag, bit polarity, and binary/runtime
  hashes remain unresolved. H2 is therefore not measurable.
- A2 planning performs no network retrieval: 9614528 is conversion-ready;
  12511021 still lacks spectral sign and an explicit time basis.
- A4 planning is lazy and fail-closed: one representative recording per three
  protocols costs an estimated 320 attempts, 5.33 CPU-hours, and 640 MiB;
  scaling the same plan to ten recordings is rejected.
- A1 found mismatch rates of 60.04% for modulation, 15.93% for baudrate, and
  3.61% for downlink frequency among the conservatively matched definitions.
- A1 input completeness passes. On 2026-08-31 the user authorized continued
  internal research despite unresolved publication paperwork; this is recorded
  in ADR 004 and does not itself prove third-party permissions.

Bulk CAMRAS decoding and large A2+ campaigns must not start until per-recording
capture contracts, historical C0 limitations, and blind transfer performance
are resolved. The benchmark byte-match failure itself is resolved at 4/4 by
the calibrated phase-first path. Bounded manifest building, metadata ingest,
event-grouping tests, and budgeted planner development may continue. The approximately 61
CAMRAS `bad/unknown` rows also cannot by themselves satisfy the later ≥500
P-CONFIRMED target.
