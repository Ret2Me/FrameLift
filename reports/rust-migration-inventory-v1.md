# Rust receiver migration: independent scope inventory v1

Updated 8 September 2026. This inventory separates implemented receiver
capabilities, finite-input parity evidence, remaining integration gates, and
research utilities that have not been rewritten. It is not a blanket claim
that every Python file in the repository is now Rust.

## Scope and dependencies

The supported native target is one Rust library and CLI for explicit signal
input, deterministic DSP, protocol/integrity validation, bounded execution,
candidate provenance and comparison. It does not call the original Python
receiver at runtime. Python source and immutable experiment outputs remain
development oracles and historical research records; removing them would
destroy reproducibility, not improve native execution.

WAV and supported raw IQ are read natively. OGG decompression deliberately
uses installed FFmpeg/ffprobe to reproduce the historical PCM; this is an
external native codec dependency, not a pure-Rust Vorbis implementation.
Optional gr-satellites/GNU Radio baselines remain separately installed and
versioned comparison tools; their implementation language is not the language
of our receiver. The separate `src/telemetry_yield/planning/` application,
including scheduling, weather and station-planning TLE work, is outside this
receiver migration.

## Capability comparison

“Fixture qualified” below means the named fixed inputs/test contracts passed,
not every future waveform. Final integration must be tied to the delivered
executable hash; earlier immutable binaries do not automatically qualify later
changes in this shared source tree.

| Existing receiver capability and Python origin | Native Rust interface and evidence | Boundary or remaining gate |
|---|---|---|
| Demodulated mono FSK/GMSK and Bell202 PCM; `audio_receiver.py`, `ogg_refinement_pilot.py` | `dsp.rs`, `receiver.rs`, `decode-audio`; full 93-observation OGG parity passed. Native positive WAV CLI also passes with empty PATH. | Already FM-demodulated audio is not subjected to a second FM discriminator. OGG still needs FFmpeg. |
| Carrier-conditioned and phase-first IQ FSK; `generic_receiver.py`, `clock_recovery.py` | `dsp::iq_frontend`, generic `decode` and `decode-metadata`; exact frontend fixtures and positive CI16 file tests, including ordered 1/4-worker equality. | OGG parity alone does not qualify IQ. Raw sample rate/layout are explicit. |
| IQ Bell202 and explicit USB real-passband interpretation; `afsk1200_plugin.py`, `audio_receiver.py` | Built-in generic routes `bell202_afsk` and `usb_real_passband`; explicit `afsk_legacy.rs` / `decode-afsk-legacy` and generic `bell202_afsk_legacy` preserve the packaged-AFSK search. Six groups pass: 4 same-IQ/full-result fixtures, 20 timing banks, 72 HDLC cases. | Legacy profile uses per-hypothesis lengths, 16-symbol guards, top-N, both polarities and strict rejection records. Existing shared-count/32-guard generic route remains unchanged. No arbitrary-waveform claim from the USB helper. |
| AX.25 HDLC, NRZI, optional G3RUH, original FCS and strict UI address chain; `fast_ax25.py`, `ax25_validation.py`, `crc.py` | `protocol.rs`; 16 test groups, 515 exact ordered AX.25 comparisons. | Full received FCS bytes are retained, not reconstructed merely to match counts. |
| Fixed-sync framing and mission-supplied validators; `generic_receiver.py` | `ProtocolConfig::FixedSync` and explicit library framing/validator API. | No telemetry trust from sync/header plausibility; an integrity policy must be supplied. |
| Raw and AX.25-wrapped CCSDS Space Packets; `ccsds_validation.py` | Native protocol adapters and generic routing; part of 704 fixed CCSDS structural/integrity cases. | Raw packet validation requires explicit integrity; APID and plausible length alone are insufficient. |
| CCSDS Version-1 TM, ASM/length, OCF and FECF; `ccsds_tm_validation.py` | Native TM parser/framer, generic config and CLI; fixed invalid/header/length/FECF fixtures. | CLI requires FECF; custom integrity callbacks are library APIs. AOS/USLP/general channel FEC were not implemented by the old receiver and are not advertised now. |
| Satellite-neutral registration and compatibility contracts; `generic_receiver.py`, `waveform_routing.py` | Public `GenericReceiver::{validate,decode_file,decode_metadata}` and resumable counterparts actually use registered plugins. Input/symbol-kind, advertised modulation families and required features are checked. | Positive custom invocation and incompatibility/ID-shadowing regressions pass. Opaque custom state requires a deterministic `resume_identity()` for reuse. |
| Timing recovery, stable rank and additive diverse bank; `clock_recovery.py`, `diverse_timing.py` | `dsp.rs`; exact ordered journal/discrete provenance across 93 OGG observations and parallel-worker fixtures. | Small floating score differences are recorded separately; they do not excuse a changed rank, frame or journal. |
| Bounded list/syndrome soft repair, stuffing maps and budgets; `soft_sync.py` | `soft.rs`, `soft-decode-symbols`; latest 8 groups pass, 196 frozen full result/error cases, 50 cost fixtures/1630 combinations, plus 7940 ordering checks and a million-dense-flag resource regression. | CRC-constrained repairs remain candidates, not independently trusted telemetry. Resource preflight rejects excessive configurations rather than pruning the search. |
| Blind clipping-robust FSK, selected windows and consensus; `clipping_robust_fsk.py`, `blind_phase_fsk_file.py` | `clipping.rs`, `clipping_file.rs`, `decode-clipped-ci16`; synthetic receiver/projection fixtures and a real selected-IQ window pass. File wrapper has 14 passing tests plus final lock checks. | Real-IQ evidence is one disclosed development window, not the entire old campaign. Resume replays clipping computation; it does not promise generic-style per-window cache reuse. |
| Bandlimited declipping; `iq_declipping.py` | Native projections and `declip-ci16`, fixed projection fixtures. | Saved cf64 reconstructions are labelled synthetic reconstructions, never original recordings. |
| Phase/burst/signal triage and PSD routing; `signal_triage.py`, `phase_window_selector.py`, `burst_window_selector.py`, `waveform_routing.py` | `triage.rs`, `inspect-iq`; 7 tests and 8 Python fixtures pass, including lifecycle/resource hardening. | Discrete scheduling decisions match; floating diagnostics have disclosed tolerances. Hints do not silently prune generic decoding. |
| BPSK/FSK/GMSK/QPSK/OQPSK physical recovery and separate RML24 scoring; `rml24_physical_plugin.py`, `rml24_benchmark.py` | `physical.rs`, `physical-decode`, separately named `score-bits`; 5 groups, 21 physical fixtures, 48 alignment cases, plus 10 positive decode/scoring variants. | Receiver returns unaligned bits. Reference-selected alignment is scorer-only and is not telemetry validation or receiver truth leakage. |
| IQ metadata, SigMF, explicit scalars/order/offset and hashes; `raw_iq_manifest.py`, `sigmf_validation.py` | `formats.rs`, `inspect-metadata`, `decode-metadata`; 11 groups, 48 raw-manifest and 6 SigMF oracle cases plus 258 KISS cases. Positive big-endian SigMF CLI preserves the same four frames. | No silent datatype/rate/endian defaults. SigMF sample origin is not a byte seek. Unsupported extensions/discontiguous layouts fail explicitly. |
| KISS transport, SatYAML/catalogue routing and bounded external adapter; `external_backends.py`, `gr_satellites_backend.py`, `satyaml_registry.py` | `formats.rs`, `backends.rs`; parser/encoder/catalogue contracts, 10 backend test groups and 159 exact contract/number-format fixtures. `baseline-gr-satellites --plan-only` does not launch a decoder. | Shell-free owned process groups, bounded output, malformed/FIFO/symlink KISS rejection. KISS and external PDUs remain candidates; no automatic transmitter attribution or submission. |
| GNU HDLC partial-octet/undersized PDU compatibility; `gr_satellites_pdu_adapter.py` | Separate `compat.rs` / `compat-pdus`; 5 groups and 48 exact Python cases producing 40 candidate emissions pass, including observation 4704 left-padding behavior. | Deliberately separate from strict native AX.25. CRC-valid malformed/undersized PDUs do not become validated telemetry. |
| Event-local candidate union and normalization; `candidate_ledger.py` | `ledger.rs`, `candidate-ledger`; fixed complete legacy ledger oracle and validation-boundary tests. | Requires caller-validated records; grouping is not independent CRC, mission identity or source-attribution validation. |
| Durable OGG campaigns and artifact auditing; `ogg_archive_campaign.py` | `campaign.rs`, `audit.rs`, `batch-audio --resume`, `compare-campaign`; full frozen campaign and lifecycle tests. | Binds input/config/executable, owns locks/process groups, preserves failed attempts. Native workers are not forcibly killed mid-window; outer service limits remain the operator's responsibility. |
| Retry-safe bounded IQ/AFSK file execution; `fsk_file_orchestration.py`, `afsk1200_orchestration.py` | `generic.rs` successful per-window commits, explicit resume, ordered all-origin merge, retained failures and attempts; 14 generic tests pass, one ignored helper is separately exercised by the abrupt-kill lifecycle test. | Modernized JSON checkpoint contract, not the old schema. External baseline union is explicit composition of baseline and ledger APIs, not the old monolithic orchestration wrapper. |
| Narrowband CW/Morse candidate extraction; `cw_probe.py` | Native streaming CI16-LE probe in `cw.rs`, `inspect-cw`; 4 test groups pass: 7 waveform cases, 140 exact NumPy partition cases, 36 exact Morse-run cases and 2048 padding comparisons. | Classifications, selected counts, text/WPM/order and repetitions match. On the noiseless constant-tone fixture, roundoff splits equal envelope centers by about 3e-12: Rust duty/transitions/separation are 0.27/47/2.728 versus Python 0/0/0; both remain non-keyed with zero evidence score and no candidates. All extracted text is untrusted and pending, never native telemetry. |

## Measured parity and speed, not new telemetry yield

The [full 93-observation audit](../work/rust-migration-20260908/full-93-threads4-v1/audit.json)
passes: 477/477 per-observation unique PDUs, 313 globally distinct, zero missing
or additional PDUs, zero full-FCS/validation differences, and zero ordered
window/discrete-provenance differences. This reproduces the existing result;
it does **not** demonstrate additional frames beyond the former decoder.
The immutable reference contains 100 selected observations, of which 93
completed. Six missing-OGG and one duration-rejected observations are not
successful empty decodes. The original reference-summary SHA-256 is
`bda79d5ecf99b31911b324f2d556463002c51b8f277009adf46677aeb0aecec2`.

The [matched benchmark](../work/rust-migration-20260908/matched-comparison-v1.json)
passes on the same loaded 90-second PCM segment: seven full-FCS frames and
29 ordered windows. Three-run medians are Python one worker 40.3755 seconds,
Rust one worker 6.9425 seconds, two workers 3.8973 seconds, and four workers
2.2930 seconds: 5.82× for the single-worker port and 17.61× overall with four
workers. This boundary excludes codec conversion, file input/output and noise
controls. It is one positive segment on a shared host, not a corpus-wide or
randomized performance distribution. Six cross-language floating scores differ
by at most 8.88e-16; selected ranks, bytes and journals still match.

The [real clipped-IQ audit](../work/rust-migration-20260908/clipping-real-iq-13168691-window2-v1/output/audit.json)
passes for observation 13168691, start 444.75 seconds, length 2.75 seconds:
three full-FCS detections reproduced, one native and two repaired candidates,
with exact discrete provenance and independently checked original CRC/UI.
Its 729598 search attempts took 67.14 seconds. There is no matched Python
timing for this selected window and no full-recording/held-out claim.

The exact immutable executable identities and final delivery gate belong in
the [release result report](rust-migration-results-20260908.md). The OGG and
real-IQ audits explicitly decline publication/deployment certification;
the matched timing measurement provides no such certification either.
Saved-output OGG comparison does not replay the original noise controls.
Separate native null smoke is useful regression evidence, not a population
false-alarm-rate estimate.

## Checkpoint and plugin trust boundary

Generic file checkpoints bind source SHA-256, executable SHA-256, complete
explicit plan, metadata representation, thread count, registry capabilities
and plugin configuration identities. Successful windows are immutable,
hash-verified records; missing/failed windows are recomputed. Full ordered
origins are retained across overlapping windows and hypotheses. Completed
result and journal are reassembled and compared on resume. The original
audio qualification path retains its historical provenance rule separately.

Fresh output must not exist. Explicit resume acquires an owned file lock,
refuses symlinks and mismatched identities, preserves all attempts and does
not signal arbitrary PIDs. Interrupted publication cannot become a successful
empty window. A known source/executable mutation writes a permanent
`source-invalidated.json` marker, preserves evidence and forbids reuse even
after the original input bytes are restored. Ordinary checkpoint artifacts
are bounded to 512 MiB, with a small emergency invalidation marker allowed
even when that budget is consumed.

Inputs are required to remain immutable during execution. Start/end full
hashes plus per-window inode/size/mtime/ctime checks detect ordinary changes;
they are not proof against deliberate coherent concurrent rewriting or
same-tick mutation-and-restoration. Local content hashes also cannot detect an
attacker coherently replacing an entire checkpoint/reference tree and its
hashes. These are hash-verified local artifacts, not cryptographically
authenticated evidence. A plugin author's `resume_identity()` must cover
all opaque configuration; the library cannot inspect hidden mutable state.

## Retained utilities and concrete non-parity boundaries

- The standalone packaged AFSK path is now an explicit native compatibility
  profile, not an unannounced change to the qualified shared timing bank.
  `bell202_afsk_legacy` requires `mode=afsk, bank=global` and pairs variable
  per-hypothesis lengths with 16-symbol guarded extraction. The dedicated
  `decode-afsk-legacy` interface also retains first-origin metadata, both
  polarities, forced initial NRZI bit zero, exact original FCS, sorted complete
  accepted/rejected results, and legacy counters. Same-IQ and same-soft full
  results match all four fixtures. The generic route retains its explicitly
  different all-origin/strict-protocol output contract.
- The native file runner replaces old per-window storage and first-origin-only
  generic results with immutable commits and an all-origin merge. It does not
  preserve the old FSK/AFSK monolithic result schema, address-rendering report,
  rejection-diagnostic schema or embedded external-baseline invocation. Strict
  decoding, candidate-only compatibility and ledger composition are explicit
  separate native interfaces instead. This is interface modernization, not
  byte-for-byte legacy orchestration-output parity.
- `iq_analysis.py` remains an unported optional empirical-analysis utility:
  headerless-IQ sample-rate inference from observation timing, amplitude/ridge
  diagnostics, TLE-predicted Doppler frequency tracks and state inference.
  It is outside `planning/`, so it must not be mislabelled as migrated planning
  code. No core receiver module imports it; native decoding instead requires
  explicit validated metadata. Native triage is not a full replacement for
  that source-attribution analysis.
- CAMRAS/archive/S3 acquisition, RML24 download/pickle conversion, prospective
  cohort selection, train/holdout construction, old sweep/report/plot/manuscript
  wrappers and historical publication/readiness metrics remain reproducible
  research utilities. They are not runtime dependencies of the native decoder
  and are not counted as Rust ports. Existing network archives or baseline
  software were not rewritten as part of signal processing.
- Native candidate union does not replace the entire historical campaign
  report, PCAP audit or mission/source-attribution workflow. Physical bits,
  Morse text, repaired frames and transport PDUs have separate validation
  requirements. No new universal modulation, AOS/USLP or channel-FEC support
  was created by changing implementation language.

The final cross-module delivery gate is complete on the immutable release
executable SHA256 `9ec64fc7bec9fe54d9730320d0d8c9fb29e7c381494bbf64b3a75e6dcf08b7eb`:
193 library tests and 13 CLI groups pass, all-target Clippy/fmt pass, the real
clipped-IQ window passes again, and a new full 93-observation/eight-worker
replay reproduces all 477 PDUs and 15306 ordered windows exactly in discrete
decisions and bytes. Completed campaign resume reuses all 93 observations.
The final 1/2/4/8-worker 90-second outputs are identical, including floating
scores, and both 90-case native null smoke suites pass. Detailed identities,
timings and numerical caveats are in the release report; this is not inherited
qualification from an earlier binary. Neither finite regression equivalence
nor faster execution alone establishes publishability or unattended station
deployment readiness. The retained utilities and scope limits above still apply.
