# Receiver improvements and candidate audit — 2026-09-08

Follow-up: the full 93-recording bank run and the new frontend/Gardner
experiment are now complete; see [the later report](receiver-followup-20260908.md).
The running-campaign section below describes the earlier state, not live status.

## Scope and scientific status

Development work on previously exposed recordings, not a new holdout. No data
were sent to a telemetry service and no production receiver was replaced. The
qualified Rust migration release and original experiment artifacts are intact.
The default receiver remains the old `diverse` timing bank.

The four diagnostic observations (14936424, 14936415, 14936407, 14936444) were
selected **because** each contained one baseline-only PDU. Their results must
not be extrapolated to an unbiased population. Payloads and reference frame
times were not supplied to any demodulator; comparisons happen after decoding.

## Reconstructed candidate evidence

New native command: `audit-archive-candidates`. It checks the original frozen
commit/artifact hashes, received native FCS with the separate bitwise validator,
downloaded reference bytes, baseline KISS and the already completed Rust replay.
Declared set counts and byte counts must agree with the reconstructed sets.
Incomplete reference collections and replay mismatches fail closed.

Evidence:

- Original summary:
  `work/satnogs-ogg-archive-week-20260831-v1/summaries/0ff4bc3197c74436abc0e1b51ac479ec.json`
  SHA-256 `bda79d5ecf99b31911b324f2d556463002c51b8f277009adf46677aeb0aecec2`.
- Existing secondary replay:
  `work/rust-migration-20260908/release-v1/full-93-threads8/`.
- Audited ledger: `work/receiver-improvements-20260908/candidate-inventory-v2.json`
  SHA-256 `485d20ff10c060b98eb2964957947cef6da25613afd602f385ac2098bfd67137`.

Results of this evidence audit (not new receiver yield):

- All **93** completed observations pass secondary replay comparison.
- **46** observation/PDU pairs are absent from their local archive; **41** are
  absent from both that local archive and the same-audio baseline.
- **23 globally distinct PDUs / 6,072 bytes** are absent from the entire frozen
  archive. **19** of these are also absent from the union of all cohort baseline
  outputs. The remaining four are benefits of reprocessing, not globally
  exclusive advantages of this algorithm over that baseline.
- All 23 have AX.25 source **LASP-0**, destination **CANVAS-0**, a pair also
  present in archived traffic. All contain an exact-length **248-byte CCSDS
  Space Packet**, version 0, telemetry type, secondary header present, APID 32,
  sequence flags 3. This is structural consistency, not independent integrity.
- Two of the 23 PDUs appear in two distinct recorded audio files. Different
  files are not automatically independent stations or transmissions.
- No bit repair was used for these 23 PDUs. The source experiment is explicitly
  restricted to the reference-free `fast` native decoder in this audit.

Mission attribution remains **unverified**. Matching addresses and CCSDS headers
are not transmitter authentication. Rust replay establishes reproducibility,
not another independent reception. These statements concern this complete
frozen snapshot, not all SatNOGS observations or all transmissions worldwide.

## Experimental local timing bank

Implemented opt-in `--bank burst` in Rust. It preserves the exact ordered legacy
`diverse` prefix and appends waveform-only clocks fitted to 4096-symbol local
regions, 50% overlap, plus an end-aligned final region. Local selection retains
four top hypotheses and one representative per rate. CRC and reference bytes
are not used for ranking. This is local acquisition, not continuous tracking,
MLSE, confidence calibration, or a newly invented demodulation principle.

Frozen experimental binary:
`work/receiver-improvements-20260908/burst-v1-release/telemetry-yield-rs`
SHA-256 `c0eaae619e6e08f62e57b557c6aad03e0e88dcc42e877700238cacc33fbd628d`.
Its source archive is adjacent. Later audit tooling does not change this binary.

## Four-observation developmental ablation

Counts are deduplicated within each observation; all runs use the same OGG and
the original FFmpeg PCM conversion. Each run records its input/executable hash
and full received FCS. The old frozen binary runs the `full` variants; the frozen
experimental binary runs `burst`. Each used two decoder workers.

| Observation | Old diverse, 6 s | Full bank, 6 s | Burst bank, 6 s | Full bank, 1 s | Baseline PDUs still missing with full bank |
|---|---:|---:|---:|---:|---:|
| 14936424 | 18 | 18 | 18 | 18 | 1 |
| 14936415 | 14 | 16 | 14 | 16 | 1 |
| 14936407 | 15 | 16 | 15 | 16 | 0 |
| 14936444 | 23 | 24 | 24 | 24 | 1 |

Every variant retained all old PDUs on these four recordings. The full bank
recovers one of the four baseline omissions and three other observation/PDU
results. Shortening the full-bank window to 1 s / 0.5 s hop gives the same counts
and does not resolve the other three baseline omissions. Burst adds one PDU on
one recording but leaves all four original baseline omissions unresolved.
It is therefore **not promoted as the preferred/default receiver**.

Of the four observation/PDU additions from the full bank, two are absent from
that observation's archive and baseline. **None** is absent from the entire
frozen cohort archive. They improve local recovery but are not new globally
archive-absent telemetry. Do not add these four to the earlier global count 23.

Raw outputs are below `work/receiver-improvements-20260908/` in `full-bank/`,
`burst-v1/`, and `full-bank-1s/`. Recorded times are not a matched isolated CPU
benchmark: jobs ran concurrently and some overlapped compilation. Extra search
cost has to be measured before claiming an efficiency improvement.

## Framing diagnosis and tests

An isolated native Rust probe, `examples/compat_audio_probe.rs`, applies the
strict and already implemented GNU-compatible deframers to the **same** soft
symbol streams. Compatibility-only emissions retain padding/truncation and
delimiter provenance and remain candidates. They do not enter trusted yield.
All four completed probes emitted **zero compatibility-only candidates**;
their strict counts are 18, 16, 16, 24, matching the full-bank runs. Thus the
GNU HDLC quirks do not explain the remaining misses on these same sampled
symbol streams. Results are in `compat-full/<observation>/result.json`.
Probe binary SHA-256:
`7949a62e3ad426586c77ca4879479364bc2d1204ccb5305d05eb43cc5aa957bd`.

The hash-pinned baseline FSK source was inspected. Unlike our fixed FIR/DC and
clock-bank frontend, it uses a square-pulse filter, a 32-symbol DC blocker, an
RMS AGC with approximately 50-symbol time constant, and a Gardner clock loop
with default relative bandwidth 0.06 and relative clock limit 0.004. These are
concrete next ablation dimensions, **not proof of which causes a given miss**.

Final verification with source/build artifacts held stable:

- `cargo fmt --check`: pass.
- `cargo clippy --all-targets -- -D warnings`: pass.
- `cargo test --release --all-targets -- --test-threads=2`: **202 library tests
  and 13 CLI tests pass**, four explicit nonordinary tests/helpers ignored.
- New tests cover unchanged legacy timing prefix, end-aligned local regions,
  translated sample coordinates, an independent positive framing fixture,
  corrupted/replaced evidence, incomplete references, and avoiding automatic
  promotion of replay/address consistency into mission authentication.
- A prior debug-suite attempt was invalidated by rebuilding its running test
  executable concurrently: subprocess launch/source-identity checks failed.
  The complete stable release-suite rerun above passed; do not overlap builds
  that replace a test executable with that executable's lifecycle tests.

Final source/tool snapshot (distinct from the qualified migration release):
`work/receiver-improvements-20260908/source-final/`.
Binary SHA-256 `774bb1b04cec7d1f4d5af2dca5be4162dffa8ef8bede734cea8ca485cc5831d6`;
source archive SHA-256 `f70bb9446802ced6343a85db25ce5a3964d4c3828eee66e4b70f00eec80d8000`.

## Continued regression campaign

Started at approximately **2026-09-08 15:04 UTC**, using the unchanged qualified
migration executable, on all 93 previously completed local OGG recordings:

```sh
work/rust-migration-20260908/release-v1/telemetry-yield-rs batch-audio \
  --reference-summary work/satnogs-ogg-archive-week-20260831-v1/summaries/0ff4bc3197c74436abc0e1b51ac479ec.json \
  --output work/receiver-improvements-20260908/full-93 \
  --bank full --threads 8
```

This is an expanded-bank **development/regression** run, not a confirmation
holdout. Its progress, immutable attempts and completion state live in the
output directory. At the time this section was written it was **running**, so
no full-cohort gain is claimed here. Resume requires the exact frozen binary
and settings above, with `--resume` added only after initial directory creation.
The initial invocation with `--resume` on a nonexistent directory failed before
creating outputs; it was restarted without that flag. No completed data are
overwritten.

## Next gates

1. Explain the remaining three baseline misses, separating synchronization,
   frontend filtering and frame-boundary behavior. Do not silently relax CRC.
2. Develop a signal-driven tracking/refinement candidate only after identifying
   a concrete failure mechanism. Keep the qualified legacy branch available.
3. Test calibrated repair confidence on independently known frame truth; no
   probability or false-acceptance certification is inferred from CRC success.
4. Freeze thresholds, compute budgets, sampling rules and metrics before a new
   multi-mission/time-separated confirmation cohort. Exclude these known data
   from claims of a new blind test.

## Publication direction

Primary target recommendation: IEEE Transactions on Aerospace and Electronic
Systems (telemetry and spacecraft systems are explicitly in scope):
https://ieee-aess.org/publications/taes

Applied ground-station/system paper: Acta Astronautica:
https://iaaspace.org/publications/acta-astronautica/

Stronger algorithmic synchronization/detection contribution: IEEE Transactions
on Communications:
https://www.comsoc.org/publications/journals/ieee-transactions-communications

A distinct reusable benchmark/data contribution could fit Scientific Data:
https://www.nature.com/sdata/publish/submission-guidelines

These are scope-based recommendations, not predictions of acceptance.
Publication readiness and deployment readiness remain false.

## Navigation trace

Graphify query expanded from the project's vocabulary:
`receiver, timing, soft, archive, audit, integrity, holdout`.
The older graph is navigation only; current Rust source and hash-verified
experiment artifacts determine the receiver claims. Planning-model readiness
nodes are not evidence of telemetry recovery. This report is also saved as
query feedback for subsequent work.
