# Native Rust receiver

The receiver runtime is one Rust library and executable, not Python calling a
Rust extension. The old Python research tree and committed experiments remain
as frozen comparison oracles. They are not imported or executed by this binary.
The separate scheduling/weather/planning project is outside this migration.

## Build and dependencies

The historical migration-qualified executable is
[`work/rust-migration-20260908/release-v1/telemetry-yield-rs`](../work/rust-migration-20260908/release-v1/telemetry-yield-rs).
Use that exact binary for its existing resume artifacts; rebuilding produces
a separately identified executable. Its source snapshot, checksums and usage
are in the [release directory](../work/rust-migration-20260908/release-v1/README.md).

```sh
cargo build --release --locked
cargo xtask check
cargo xtask research-check
target/release/telemetry-yield-rs capabilities
```

The checked-in toolchain is Rust 1.98.1. Linux is the qualified execution
platform. WAV, IQ, DSP, protocol decoding, metadata validation, ledgers and
orchestration use Rust. OGG decompression intentionally uses installed
**FFmpeg and ffprobe**, matching the original experiment's exact PCM samples;
this is not a pure-Rust Vorbis codec. External reference decoders, when used,
remain separately versioned third-party tools, never our native decoder.

## Decode audio

```sh
target/release/telemetry-yield-rs decode-audio \
  --input recording.ogg --output results/new-recording \
  --mode fsk --baud 9600 --threads 4
```

For Bell202 audio, select `--mode afsk --baud 1200`. Inputs must be mono.
Already FM-demodulated audio is not RF IQ: the receiver does not apply a second
FM discriminator to this representation. The defaults preserve the 6-second
windows, 3-second overlap stride and additive timing bank of the frozen OGG
experiment. No pruning, early-success exit or relaxed CRC is used for speed.

The output directory must be new. `plan.json`, `windows.jsonl` and `result.json`
retain input identities, complete window coverage, exact received frame/FCS
bytes and ordered provenance. A failed window makes the run failed; it is not
reported as a successful empty observation.

### Experimental local synchronization

`--bank burst` retains the default `diverse` bank as an unchanged prefix, then
adds independently fitted clocks/thresholds for overlapping 4096-symbol regions
of the conditioned waveform (50% overlap, with an end-aligned final region).
Each local region uses four top-ranked clocks plus one representative per rate.
These fits use signal samples only, never known payloads, archive matches or CRC
search results. There is no continuous clock/carrier loop or calibrated
confidence claim. Extra hypotheses are not independent confirmations.

The default remains `diverse`. The experimental option is additive with respect
to its hypotheses, but preservation of all decoded frames still requires
regression testing. A larger bank costs more work and does not itself establish
better yield at equal CPU budget. `--bank full` is a separate diagnostic that
tries every original fixed phase/rate combination; it does not include local
regions. Results from known development observations are not a fresh holdout.

### Experimental frontend and continuous timing

The separate `tracking_audio_probe` development example now evaluates two
frontends (legacy FIR/DC and square-pulse/32-symbol DC/RMS AGC) against two
timing methods (full fixed bank and a bounded continuous Gardner loop).
It records each variant's received frames separately and their additive union.
It does not replace the default decoder, accept reference bytes, implement IQ
MLSE, or claim deployment readiness. Build with
`cargo build --release --example tracking_audio_probe`; invoke with
`--input capture.ogg --output new-directory --observation-id ID --threads 2`.
The output directory must be new; this probe has no campaign-resume interface.
See [the development results](../reports/receiver-followup-20260908.md).

## Explicit IQ or generic protocol plans

```sh
target/release/telemetry-yield-rs decode \
  --input recording.ci16 --plan config/rust/iq-fsk-9600.json \
  --output results/new-iq --threads 4

target/release/telemetry-yield-rs inspect-metadata \
  --metadata recording.sigmf-meta --kind sigmf

target/release/telemetry-yield-rs decode-metadata \
  --metadata recording.sigmf-meta --kind sigmf \
  --plan your-metadata-format-plan.json --output results/new-sigmf --threads 4
```

Adapt the example rates and datatype to actual metadata. For `decode-metadata`,
the plan explicitly uses `"format":"metadata"`; its sample rate must agree
with the manifest. The parser checks required hashes, endian, scalar type,
interleaving, Q polarity, scaling and file geometry. SigMF `core:offset` is a
sample-index origin, not a byte seek. Unsupported layouts/extensions fail
explicitly. `--kind raw-manifest` accepts the original `raw-iq-input-v1`
evidence contract, including its clipping/A-B requirements.

The generic Rust registry is satellite-neutral. Built-in frontends include
PCM FSK/Bell202, phase-first and channel-conditioned IQ FSK, IQ Bell202 and
explicit real-passband analytic conversion. Protocol adapters cover AX.25
(plain/G3RUH), AX.25-wrapped CCSDS Space Packets, raw Space Packets, CCSDS TM
and fixed sync with explicit integrity policy. CCSDS TM requires actual frame
length/ASM and FECF for CLI use; custom integrity callbacks are a library API.
Explicit coherent BPSK/QPSK/OQPSK plans, CSP, configured AOS/USLP, Reed–Solomon
and LDPC are also implemented. Mission layouts, coding and integrity are never
silently inferred. These interfaces do not establish real-satellite PSK parity
or complete AOS/USLP services; use `capabilities` for the current limits.

`bell202_afsk_legacy` is a separate explicit IQ route for the earlier packaged
AFSK search: per-hypothesis symbol lengths, 16 guard symbols and global top-N
ranking. It requires `mode=afsk` and `bank=global`; both timing selection and
soft-symbol extraction switch together. See
`config/rust/iq-bell202-legacy-1200.json` for its original 57.6 kHz/1200 baud
profile. It does not change the existing audio or FSK banks. The separate
`decode-afsk-legacy` command accepts one explicit IQ segment and reproduces
the original full accepted/rejected-candidate counters and first-origin result.

Generic results preserve every ordered waveform/timing origin for each unique
frame. This is an additive metadata improvement over the old generic API's
first-origin-only rule. The `decode-audio` qualification path retains the
original rule so its historical journal/provenance comparison remains exact.

Both generic commands support explicit `--resume`. Successful windows are
immutable checkpoints bound to the input, executable, metadata, plan, thread
count and registry configuration. Failed or unfinished windows are retried;
completed results are deterministically reassembled and verified. All attempts
are retained. Hash verification detects corruption and mismatched inputs; it
does not authenticate evidence against a party rewriting an entire local tree.

Library clients can construct `generic::GenericReceiver`, register their own
`Demodulator` and `ProtocolDecoder`, then call instance decode/validation
methods. Compatibility checks cover input and symbol kind, declared modulation
families and required features. Custom implementations must provide a stable
`resume_identity()` covering all opaque state before checkpoint reuse is allowed;
fresh non-resumed runs may omit it. This is a plugin author's contract, not an
automatic inspection of hidden mutable state.

## Other receiver interfaces

- `physical-decode`: explicit BPSK/FSK/GMSK/QPSK/OQPSK configuration and bounded
  IQ segment to **unaligned bits**, not validated telemetry. `score-bits` is a
  separately named, development-only reference-bit scorer; truth never enters
  the physical decoder.
- `soft-decode-symbols`: bounded list/syndrome repair, with outputs labelled
  candidates. A CRC constrained by a repair search is not independent proof.
- `decode-clipped-ci16`: clipping-robust FSK, explicit selected windows or blind
  selection, native detections separate from repaired candidates. Consensus
  grouping is a separate library API, not an independent validation step.
- `compat-pdus`: exact symbol-stage GNU HDLC quirks, including left-padded or
  undersized CRC-valid PDUs. Input is explicit decoded bits or NRZI/G3RUH
  levels; output remains candidate-only, separate from strict native decoding.
- `declip-ci16`: bounded bandlimited projections saved as cf64le reconstructions,
  explicitly not original recorded IQ.
- `parse-kiss`, `inspect-satyaml`: transport/catalogue inspection, not evidence
  that a payload is valid or a modulation has been identified.
- `candidate-ledger`: stdin `{"detections":[...]}`; deterministic event-local
  union and baseline/candidate overlap. It accepts caller-validated records
  only and is not an independent integrity or source-attribution validator.
- `audit-archive-candidates`: hash-check original OGG experiment evidence,
  reconstruct archived PDUs from downloaded files and baseline PDUs from KISS,
  independently verify native received FCS, and check the existing secondary
  Rust replay. It inventories globally archive-absent candidates and baseline
  omissions. An address match and repeated replay do not establish transmitter
  identity; the resulting ledger preserves that unresolved attribution.
- `inspect-iq`: bounded streaming signal, waveform or burst diagnostics with
  explicit sample rate. These are scheduling hints, never automatic pruning.
- `inspect-cw`: CI16 narrowband tracking and the existing Morse WPM search.
  Text remains `pending`, not verified telemetry, including a plausible SOS.
- `baseline-gr-satellites`: an explicit separately installed external decoder,
  shell-free argv, process ownership, bounded output and candidate-only KISS.
  `--plan-only` exposes the invocation without launching it; no submission.

Use each command's `--help` for the exact bounded arguments.

Soft-repair CLI resource policy is intentionally stricter than the unchanged
research library: excessive preparation/combinatorial-memory estimates are
rejected, never silently pruned. List mode rejects options the legacy search
does not implement. Both modes independently check original candidate FCS and
AX.25 UI structure at the CLI boundary; rejected candidates are counted. The
reported working-set estimate is an algorithmic planning bound, not an OS/RSS
guarantee. The exact same selected soft regions are now obtained without
materializing every possible flag pair first.

`decode-clipped-ci16 --resume` requires the identical input/configuration and
executable. Its plan, lock and no-clobber staged publication preserve failed
attempts rather than turning them into successful empty results. The default
working-set estimate is bounded at 512 MiB. Resume revalidates/replays the
clipping computation deterministically; it does not promise per-window cache
reuse. The former SQLite selector storage is replaced by an explicitly
versioned streaming/in-memory selector, different storage with fixture-proven
selection equivalence.

## Durable local campaigns and comparisons

```sh
target/release/telemetry-yield-rs batch-audio \
  --reference-summary frozen-summary.json --output results/new-campaign --threads 4

# The exact same binary, config, reference and source identities are required:
target/release/telemetry-yield-rs batch-audio \
  --reference-summary frozen-summary.json --output results/new-campaign --threads 4 --resume

target/release/telemetry-yield-rs compare-campaign \
  --reference-summary frozen-summary.json --results results/new-campaign \
  --output results/new-campaign/audit.json
```

Reference frame bytes are read by the **auditor only**, not supplied to DSP.
The replay coordinator reads IDs, input hashes and geometry from committed
metadata. Failed/interrupted attempts are preserved. Commits/checkpoints bind
the executable/configuration/source and no-clobber result artifacts. An owned
file lock prevents competing campaign writers; no PID-based global killing.

SIGINT/SIGTERM checkpoint after the current bounded observation. Native Rust
workers cannot safely be killed mid-window in-process: use an owned service
cgroup for an outer hard wall timeout. Codec subprocesses have independent
timeouts, output limits and owned-process-group cleanup. This is process
ownership/resource control, not a sandbox for malicious third-party decoders.

## Accuracy and speed qualification

`compare-campaign` rejects incomplete/failed/unexpected observations, missing
journals, changed source hashes, CRC/FCS/payload differences and changed
discrete provenance. Floating timing-score differences are reported separately;
small score differences do not excuse a changed selected rank or decoded byte.

```sh
target/release/telemetry-yield-rs benchmark-audio \
  --input recording.ogg --output results/thread-benchmark \
  --workers 1,2,4 --repetitions 3 --first-seconds 90

target/release/telemetry-yield-rs null-smoke \
  --output results/null-smoke --per-kind 30 --threads 4
```

The timing boundary is loaded PCM to frames+ordered journal, excluding input
conversion and noise controls. It records process CPU and wall time and checks
complete output equality for every worker count. Worker-stage elapsed sums
overlap and are **not CPU time**. Historical Python campaign timings included
two controls per observation; historical/new ratios are not automatically an
isolated matched speedup. Synthetic null smoke does not establish a population
false-alarm rate; repeated silence is not independent statistical exposure.

Finite-corpus equivalence is evidence of zero regressions on that corpus, not
a mathematical guarantee for every future waveform. Publication readiness and
station deployment need separate held-out telemetry/false-positive qualification.

See [measured migration results](../reports/rust-migration-results-20260908.md)
for immutable executable identities, the full 93-observation comparison and
the matched 90-second performance measurement.
