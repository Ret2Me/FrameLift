# Native archival experiments

`framelift-campaign` owns the archival workflow in Rust: exposure inventory,
metadata acquisition, cohort selection, executable registration, audio download,
paired execution and packet-level reporting. It does not import or invoke the
FrameLift Python package. Curl, FFmpeg and the separately installed reference
decoders remain explicit external dependencies. The gr-satellites reference
implementation still uses its own Python/GNU Radio runtime.

This workflow currently qualifies **mono, post-FM FSK/GFSK/GMSK 9600-baud audio
with strict AX.25 UI framing**. It is not an IQ benchmark and does not qualify
the other native modulation/FEC paths. The full-repository migration remains
[incomplete](rust-migration-status.md).

## Build and inspect

```sh
cargo build --release --locked --bins
target/release/framelift-campaign --help
target/release/telemetry-yield-rs decode-progressive --help
```

The workflow uses new output paths and never silently replaces a cohort,
registration or result. JSON documents with a `sha256`/`content` envelope are
content-bound local artifacts, not cryptographic signatures or trusted timestamps.

## Order of operations

1. Write a protocol defining dates, mission/profile sources, sample size,
   diversity limits, grouping, ranking salt, budgets and worker count. The
   checked-in [September protocol](../config/archive-qualification-20260916.json)
   is an experiment definition, not a success report.
   Its frozen [96-recording selection](../config/archive-qualification-20260916-selection.json)
   is retained separately from results.
2. Run `inventory --work-root ROOT --prior-manifest PRIOR --output EXCLUSIONS`.
   This conservatively combines earlier resolved exposures with bounded local
   filename and metadata scans. It does not read waveforms or hidden environment
   files. It excludes neighboring mission-days and known IDs. Renamed copies and
   exposures elsewhere are not globally proven absent: the generated inventory
   deliberately leaves `complete_inventory_attested` false.
3. Run `metadata --protocol PROTOCOL --output NEW_DIRECTORY`. Only API metadata
   is downloaded. Eligibility checks the actual `ground_station` field, declared
   representation/profile, date, duration and audio availability. Decoder counts,
   `demoddata` and observation success labels do not enter ranking. Raw API
   responses remain as evidence; the projected catalogue omits decoder outcomes.
4. Run `freeze --catalogue CATALOGUE --exposures EXCLUSIONS --output COHORT`.
   The entire pagination chain must have completed. Connected temporal groups
   are formed **before** excluding exposed records, so an excluded observation
   cannot split its pass into supposedly untouched neighbors. At most one
   observation per group is selected. Mission/station caps and minimum diversity
   must all pass; the program does not lower the target after seeing outcomes.
   A metadata-feasibility change must instead be explicit: `amend --catalogue
   ORIGINAL --protocol REVISED --reason REASON --output AMENDED`. It preserves
   the original and forbids changes to dates, missions, grouping, rank salt or
   decoder/resource policy. It must precede waveform-outcome access. See the
   [declared September amendment](archive-sampling-amendment-20260916.md).
5. Copy the qualified receiver and campaign executable to a new versioned
   location outside the mutable Cargo target directory. Run `register --cohort
   COHORT --receiver RECEIVER --direwolf ATEST --gr-satellites GR_SATELLITES
   --ffmpeg FFMPEG --dependency FILE --output RELEASE`. Repeat `--dependency`
   for relevant runtime/configuration files. This binds named files, not every
   library in the operating system. A hermetic baseline image is a separate gate.
6. Run `acquire --cohort COHORT --output AUDIO_DIRECTORY`. Every selected input
   is attempted. Download errors are retained; no better-looking replacements
   are selected. HTTPS hosts, redirects, bytes, retries and disk use are bounded.
   Server `Retry-After` is respected. There is no partial-catalogue fallback.
7. Run `run --cohort COHORT --release RELEASE --acquisition AUDIO_DIRECTORY
   --output RESULTS_DIRECTORY`. Each observation is converted once to PCM16
   at its original sample rate, with no gain, resampling or cropping. Both
   schedulers and both external baselines receive those exact bytes. Preparation
   omits FFmpeg's optional WAV metadata chunks because Dire Wolf's `atest`
   reader does not accept its default `LIST` chunk; PCM samples are unchanged.
   The runner owns a file lock, records processes and counterbalances arm order by ID.
   `--resume` reuses terminal rows; an observation interrupted mid-run is retained
   as attrition, not silently retried until it succeeds. A finished run also
   writes its audited `report.json` automatically.
8. Run `report --cohort COHORT --release RELEASE --results RESULTS_DIRECTORY
   --output REPORT`. The report rechecks source identities, committed native
   frames/FCS and external output parsers. Failed/missing arms never become
   successful empty decodes. A report can describe partial campaign coverage.

Do not rebuild or replace registered executables while running an experiment.
The runner verifies their identities and rejects drift. Download receipts,
common WAV files and task/session artifacts are evidence and must be retained
for the report audit. Acquisition failures and disk-guard stops are visible
operational outcomes, not signal-absence evidence. The disk guard preserves
4 GiB plus the next operation's allowance; this is not a full-filesystem quota.

## What is measured

- Observation-local unique payloads, separately from globally distinct payloads.
- Added **and** missed packets/bytes, with zero-denominator percentages left null.
- Adaptive versus fixed ordering at identical requested budgets/thread counts.
- Each ordering versus the union of Dire Wolf and gr-satellites component
  baselines. Baselines have a separate 120-second completion guard: this is not
  advertised as an equal-compute cross-product comparison.
- All valid pairs, metadata-reported signal, and reference-decoder-positive
  observations as separate strata. Neither positive stratum is transmitter truth.
- Process wall time, available CPU accounting, memory, timeout status and
  preparation cost. Interrupted process-tree CPU totals may be incomplete.
- Descriptive paired bootstrap intervals preserving connected station/pass
  groups. Group independence and representativeness are not automatically proven.

The auditor verifies received FCS for native frames. External KISS/atest
payloads have their FCS stripped; their CRC status is decoder-attested. Counts
alone cannot certify false-acceptance performance, mission attribution, global
nonexposure, broad waveform coverage or publication readiness. The report keeps
those certification flags false.

## Adaptive ordering

```sh
target/release/telemetry-yield-rs decode-progressive \
  --input recording.wav --output session \
  --mode quick --scheduler marginal-yield --threads 4

target/release/telemetry-yield-rs decode-progressive \
  --input recording.wav --output session \
  --mode full --scheduler marginal-yield --threads 4 --resume
```

The default remains `--scheduler fixed`. `marginal-yield` v1 ranks eligible tasks
by observed new validated payload bytes relative to measured task cost, with
explicit priors and quick-window weighting. The session manifest records all
constants. Four-task barriers and precommitted decisions make feedback causal;
the initial quick sweep samples interval midpoints breadth-first to cover the
whole recording before filling gaps. In later phases every fourth batch retains
original order for exploration. Full mode keeps every
task. The quick and full-baseline anchor generations remain unchanged. Changing
policy mid-session is rejected.

Measured costs are invocation/hardware dependent; scheduling is not promised to
be identical across machines. Resume replays the stored causal evidence and
finishes an incomplete batch before learning from it. `schedule/` belongs to
the checkpoint, alongside `tasks/`. The mechanism is a testable resource-allocation
hypothesis, not a demonstrated new demodulation algorithm or an assumed speedup.
