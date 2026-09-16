# Recovery implementation and qualification, 16 September 2026

Status: engineering qualification and a bounded historical-data pilot. This is
not a completed independent publication benchmark or a universal superiority
claim. Both bounded field comparisons have completed; detailed receipts and
limitations are in [the field report](field-recovery-20260916.md).

## Delivered components

- Variable-length AX.25 UI from IQ, explicit NRZI/direct coding and optional
  G3RUH, with retained received FCS and an additive complete-observation BCJR
  lane. Channel training uses separate already validated packets. No anchors
  means no supplemental result. See `docs/recovery-hdlc.md`.
- Fixed-frame recovery retains uncoded, K7, RS, concatenated and LDPC profiles,
  coherent CPM, PSK refinement, protected repeat identity and resumable windows.
  These mechanisms are not all available in the new variable-length HDLC lane.
- Typed native-Rust paired-study receipts and summaries: additions and losses,
  bytes, failures/missing arms, mission/exposure/signal strata, negative controls
  and descriptive station/pass-component bootstrap. External decoder attestation
  remains distinct from independently checked received FCS.
- Exact CPM preparation reuse: estimate the received pilot once and construct
  final channel metrics once, rather than constructing them twice. Backward
  recursion reuses scratch storage. No heuristic search pruning was introduced.

## Synthetic results

The independently encoded integration matrix has 26 clean positive cases and
52 negative cases. Baseline and extended paths recover all 26 positives, with
zero observed negative-control accepts or execution errors.

The declared noisy grid contains FSK/GFSK/GMSK × uncoded/K7/LDPC × three noise
levels × four seeds: 108 positive cases plus 216 matched negative controls.
Baseline recovers 67 frames; coherent-CPM union recovers 72, five additional
frames (+7.46% relative), with no baseline losses and no observed accepts in
the negative controls. Both fail all 36 highest-noise cases: acquisition yields
no candidates. This comparison is **CPM disabled versus enabled in the current
receiver**, not the previous released product or an external decoder.

These counts are controlled development results, not an orbital reception
probability or an empirical real-world false-acceptance bound. Detailed manifests,
identities, timings and retained early development failures are described in
`reports/recovery-qualification-20260916.md`.

Three rotated before/after timing pairs measured 9.8% less decode wall time on
the integration matrix and 34.0% less on the noisy CPM grid (1.52× there).
All 1,206 distinct scientific reports, and all 3,618 repeated comparisons, are
byte-identical before/after optimization. These are shared-VM synthetic timings,
not a field throughput or CUDA result. The stress grid's repeated noise-only
waveforms are not independent negative observations.

## Regression and operational verification

- The previous 240-case multimode cohort was regenerated with the same recipes.
  Its 480 per-arm scientific reports are byte-for-byte identical to the prior
  frozen reports: 120 baseline versus 174 full-path frames, 54 added, none lost,
  zero false accepts. These are unchanged historical synthetic results, not
  additional gains from this change.
- HDLC module tests: 8 passed, including variable lengths, bit stuffing, line
  coding, all three PSK modes, FSK, bad CRC/truncation/noise controls, separate
  anchor/target recovery, worker parity and the inherited FSK warm-up boundary.
- Native receiver CLI: 28 tests passed; research CLI: 9; archive CLI: 5.
- Final complete library suite: **603 passed, 0 failed, 6 ignored**, 735.99 s
  with four test workers. The ignored set contains two subprocess helpers
  invoked by parent tests and four explicitly opt-in local-data/microbenchmark
  checks; it is not counted as six additional successful tests.
- CPU library/binary Clippy and CUDA-feature library/binary compilation and
  Clippy passed with warnings denied. CUDA execution was not tested.
- Library documentation builds with warnings denied; all six developer-check
  orchestration tests pass.
- The initial full library run had 586 passes, 6 ignored and one subprocess
  watchdog timeout under concurrent compilation/test load. The unchanged test
  passed in isolation. Its test-only hang guard was increased from 15 to 60
  seconds; checkpoint equality and all production deadlines remain unchanged.
  The final full-suite replay passed after that test-only adjustment.

The maintained shipping-source rustfmt check passed. `cargo fmt --all --check`
also traverses historical research examples and reports pre-existing formatting
differences; those frozen/research sources were not mechanically rewritten.

## Real-data scope

The pilot freezes 12 historical OGG observations, selected without decoder
outcomes from three mission-specific metadata prefixes, covering eleven stations.
All 12 downloads succeeded (about 94 MB). The original archive labels one as
with-signal, four as without-signal and seven as unknown. Labels are not changed
based on FrameLift results. Exposure remains unknown: downloading a new file and
finding no old matching filename do not prove non-exposure to its content.

Two existing CANVAS IQ recordings are separately classified development data and
processed across the complete source, not manually selected promising snippets.
Audio tests cannot demonstrate gains from coherent-IQ-only algorithms. See
`docs/field-recovery-pilot.md` and the field report for exact receiver settings,
input representation, attrition and limitations.

Both complete IQ pilot runs recover 9 observation-local distinct PDUs with
the old receiver, 9 with the new baseline and 9 with the supplemental sequence
lane; gr-satellites recovers 8. Native receivers add 3 versus gr-satellites but
miss 2, so this is not a superset. **No incremental field gain from the new
sequence lane is demonstrated.** The initial timing comparison used different
worker counts and does not isolate algorithm cost. The versioned replay uses
four workers in both new native arms and retains the same frame sets. It also
holds and verifies the external executables and profile, not just the input and
native runner. The final CLI independently re-summarized its study receipts;
yield and attrition match, with explicit development-exposure strata added.

The replay's summed wall times are 6.03 s for old generic FrameLift, 6.43 s for
gr-satellites, 14.16 s for new HDLC baseline and 15.90 s with sequence recovery.
These are shared-host measurements with different process-startup boundaries,
not an isolated receiver efficiency experiment. They do **not** show a speed
advantage for the new HDLC implementation; the CPM optimization above is a
different signal path and benchmark.

The first OGG comparison exposed an input-container incompatibility: Dire Wolf's
`atest` rejected the FFmpeg-produced WAV `LIST` chunk before decoding. This is
an infrastructure failure, not zero telemetry. That run is retained as
interrupted; a versioned amendment uses a canonical WAV container and verifies
unchanged PCM before rerunning the complete selected cohort with all decoders.

The amended OGG pilot completed **48/48 receiver runs on all 12 inputs**, with
no failed/missing arms or unavailable recordings. Observation-local unique PDU
counts are 18 for old FrameLift, 18 for new FrameLift, 15 for Dire Wolf and 5
for gr-satellites. All packets come from **one CANVAS observation, 14197262**;
all four decoders return zero on the other eleven recordings. New and old
FrameLift have exactly the same packet sets.

Against Dire Wolf (also the union of the two external frame sets), FrameLift
adds four PDUs and misses one: net +3, or +20% of this small external denominator.
Against gr-satellites alone it adds thirteen and misses none. This is evidence
for this recording/profile combination, not a 20% population improvement or a
gain attributable to the new IQ algorithms. The one archive-confirmed signal
recording, 14211100, yields zero for every arm: its percentage gain is undefined,
not positive. The requested signal-labelled metric is retained separately.

Summed OGG wall times are 143.17 s old, 146.39 s new, 48.35 s Dire Wolf and
76.55 s gr-satellites. Native audio uses more compute here; there is no new
audio speedup. All twelve canonical WAV container hashes and decoded PCM sample
hashes were independently rechecked using SHA-256 and FFmpeg. The common WAVs
are temporary RAM-backed files; original OGG and reproduction receipts are
durable. That storage distinction remains a publication-replication limitation.

A comparator-rebased report initially omitted an arm name from its candidate
declaration while retaining that arm's receipt. The strict summarizer rejected
it. Corrected derived studies change only baseline/candidate declarations,
preserve every observation/receipt, and pass validation; original rejected
exports are retained. No receiver was rerun or result excluded for that repair.

## Remaining publication and hardware gates

No NVIDIA device or driver library is exposed in this VM (the PCI display device
is virtual). CUDA builds remain optional and compilable, but full GPU execution,
end-to-end parity and speedup require actual supported hardware. No new GPU
kernel is claimed on the basis of CPU-only measurements.

The historical-data pilot is not representative population sampling, and its
runtime dependencies are not a sealed execution closure. A fully audited,
unexposed test cohort, predeclared compute-matched comparisons, broader real
protocol coverage and field-level ablations remain necessary before extending
publication claims to these new mechanisms. The existing frozen publication
campaign executables were not changed.

## Local evidence locations

- Closure/regression: `/home/ubuntu/framelift-closure-20260916.EKKlOG`.
- Synthetic qualification: `/home/ubuntu/framelift-recovery-qualification-20260916.uuMlua`.
- Historical pilot: `/home/ubuntu/framelift-field-20260916.QAl5x5`.

The final CPU CLI is
`/home/ubuntu/framelift-closure-20260916.EKKlOG/telemetry-yield-rs-final`, SHA-256
`6ec7448d5e58157386919d7f20a6039dc6c6550f694082491f4673a9254a010b`.
The audio comparison intentionally retains its earlier frozen binary, SHA-256
`f329e9aefb5ac63369351448ef8188dea870b166a4a5e3b1f6af2cf7573c5d8f`;
it was not replaced midway through the experiment.

Only this task's initial generated test executable was compressed to `.zst`,
saving about 106 MiB; it remains recoverable. No raw recordings were removed.

Graphify was consulted with vocabulary `benchmark baseline validation recovery
frame`; its September 4 graph refers mainly to the older Python implementation.
Current Rust source and run artifacts, not graph edges, support this report.
No new semantic extraction was run (0 extraction input/output tokens).
