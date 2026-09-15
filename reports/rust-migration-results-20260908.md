# Rust migration: measured results, 8 September 2026

This report separates the measured snapshots and their exact qualification
boundaries. It is not a publication/deployment certificate.

## Complete OGG corpus equivalence: PASS

The Rust replay completed all 93 eligible observations of the immutable public
OGG reference campaign. Six observations with no OGG and one duration-rejected
observation were not relabelled successful empty decodes.

- 477/477 per-observation-deduplicated PDUs, 313 globally distinct.
- 125928 PDU bytes across observations; exact original frame/FCS bytes retained.
- 0 missing, 0 additional, 0 received-FCS/validation discrepancies.
- 15306/15306 ordered windows; 0 journal or discrete provenance differences.
- 868 provenance records checked. 522 float scores differ at the bit level;
  maximum absolute difference 2.220446049250313e-15. No discrete decision or
  decoded byte was excused by a float tolerance.
- Source WAV identities match the original FFmpeg conversions exactly.

This demonstrates zero regressions on this corpus, not universal zero loss for
every future signal. It is a migration result, **not additional telemetry yield**.

Evidence: [strict audit](../work/rust-migration-20260908/full-93-threads4-v1/audit.json),
[terminal summary](../work/rust-migration-20260908/full-93-threads4-v1/summary.json).
Audit SHA256:
`f2307d5c49d59ee1f4d3d0b88cfc55dfa713d42e824ee2bf0f37b4a0ea1950f9`.
Reference-summary SHA256:
`bda79d5ecf99b31911b324f2d556463002c51b8f277009adf46677aeb0aecec2`.

The qualified immutable executable is
`work/rust-migration-20260908/build-ogg-v1/telemetry-yield-rs`, SHA256
`334aa3618a30e31d5c9e4dedab32d137e291daf328c5b95858d8d3a865e481ed`.
It was built from that directory's isolated source snapshot, not changed in
place while the campaign ran. That audit qualifies only this older executable;
the complete delivered build has separate qualification recorded below.

Rust signal-only aggregate: 1121.33 seconds (18m 41s); whole batch: 2173.13
seconds (36m 13s), including conversion/hashing/I/O. Historical Python recorded
an aggregate 22473.31 seconds (6h 14m 33s), including two smoke controls per observation.
These historical totals have different boundaries; the isolated same-input
measurement below is the appropriate evidence for acceleration.

## Matched 90-second positive segment: PASS

Observation 14936372, first 90 seconds, 4320000 float64 samples at 48 kHz.
Both implementations used the same PCM SHA256
`7a9c141bf63994437a7490486e9bb6fd62afb31ce872ab5f3faa4fc26023ec63`.
The timed boundary is loaded PCM to complete frames and ordered window journal;
no OGG decoding, input reads, noise controls or output-file writes are timed.

| Implementation | Median elapsed, 3 repetitions |
|---|---:|
| Python, 1 worker | 40.3755 s |
| Rust, 1 worker | 6.9425 s |
| Rust, 2 workers | 3.8973 s |
| Rust, 4 workers | 2.2930 s |

The single-worker port is 5.82× faster here. Four Rust workers are 3.03× faster
than one Rust worker, for 17.61× overall versus Python. Every run yields the
same 7 full-FCS frames and 29 ordered windows. Between Rust worker counts,
complete scores/provenance/frames/journal are identical. Cross-language six
timing scores differ by at most 8.88e-16; discrete provenance and bytes match.

Evidence: [matched comparison](../work/rust-migration-20260908/matched-comparison-v1.json).
The benchmark executable SHA256 is
`9e20107055c57b49b877f16707f714280b9a0840be31580e84278d535b2d7051`.
This is one positive segment on a shared host, with sequential implementation
runs, not an isolated or randomized corpus-wide performance distribution.
Python was used only to measure the unchanged legacy oracle; the native Rust
receiver and its ordinary tests do not require it.

## Other demonstrated paths

- Native Rust WAV and CI16 end-to-end synthetic positives: exact expected
  four-frame set and 1/4-worker ordered output equality. Additional CLI tests
  recover the same four frames from big-endian SigMF with absolute sample
  origin, using an empty PATH and no Python.
- Real clipped CI16, observation 13168691, explicitly selected window starting
  at 444.75 seconds: 3/3 historical full-FCS detections reproduced, one native
  and two **repaired candidates**. Exact discrete provenance; independent
  bitwise CRC/UI validation. This is 1/4 selected windows, not full-observation parity.
  [Real-IQ audit](../work/rust-migration-20260908/clipping-real-iq-13168691-window2-v1/output/audit.json).
- FSK and AFSK null smoke: 90 cases per mode (30 silence, 30 Gaussian, 30 bursty
  tones), zero frames and zero failures. Repeated silence is not independent
  exposure; no population false-alarm rate is inferred.
- Protocol fixtures cover 515 ordered AX.25 comparisons and 704 CCSDS
  structural/integrity cases; soft repair 196 result/error cases plus 50
  compensated-cost fixtures/1630 ordered combinations; physical recovery 31
  fixtures and 58 separately truth-based scorer cases.
- Metadata/KISS/SatYAML, external-process ownership, durable OGG campaigns,
  clipping selection, generic plugin contracts and IQ file checkpointing have
  separate unit/integration gates; consult the scope inventory and final build
  records rather than treating OGG parity as proof for those components.

## Additional integration gates

The intermediate pre-CW integration test executable
`work/rust-migration-20260908/pre-cw-lib-tests-v1/library-tests`, SHA256
`ebcd20248d289fb615d351f44c744ed2aa9cd4e9ee1696c8ff6535e1779d6621`,
passed 183 library tests (0 failures, 4 deliberately ignored entries) in
166.38 seconds. Two ignored entries are subprocess fixtures exercised by
lifecycle tests; two require the separately retained research dataset.
The real-IQ selected-window test was then invoked explicitly and passed again,
including the new exact bounded soft-region selection:
[post-optimization IQ audit](../work/rust-migration-20260908/pre-cw-real-iq-v2/audit.json).

Separately, all 13 black-box CLI test groups passed without Python or codec
programs on PATH, including positive WAV/SigMF, completed resume, malformed
inputs, candidate trust labels and rejected unbounded/ignored soft settings.

The soft-region optimization passed the 196 unchanged Python oracle cases
plus 7940 ordering/boundary comparisons. On a 1,000,000-sample dense-flag
regression, it reports all 150745530 eligible pairs but retains only the same
four selected regions. A debug test measured 0.55 seconds and 24044 KiB peak
RSS. The former all-pairs raw tuple payload would be about 3.6 GB; that figure
is calculated, not a measurement of an old process. No search candidate is
discarded beyond the original configured selection policy.

The explicit legacy AFSK path subsequently passed six groups: four same-IQ
full-result comparisons, 20 timing-bank cases and 72 HDLC cases. Accepted and
rejected frames, original FCS, counters, ranks, polarity and used bits match.

CW passed four groups: seven waveform cases, 140 NumPy-partition cases,
36 Morse-run cases and 2048 interval-padding comparisons. Classification,
selected counts, candidate text/order, WPM and repetitions match. One noiseless
constant-tone fixture exposes a numerical degeneracy: approximately 3e-12
envelope-center separation changes Rust duty/transitions/separation diagnostics
to 0.27/47/2.728 versus Python 0/0/0. Both classify it non-keyed, assign zero
evidence score and emit no text candidates. These diagnostics are **not**
claimed bit-identical. Morse text is never promoted to validated telemetry.

## Delivered release build and final integration gates

The complete native receiver was built from the isolated
`work/rust-migration-20260908/release-v1/source/` snapshot with locked Rust
1.98.1, LLVM 22.1.8, release optimization, thin LTO and no fast-math.
The executable is [telemetry-yield-rs](../work/rust-migration-20260908/release-v1/telemetry-yield-rs),
SHA256 `9ec64fc7bec9fe54d9730320d0d8c9fb29e7c381494bbf64b3a75e6dcf08b7eb`.
Its [source archive](../work/rust-migration-20260908/release-v1/source.tar) SHA256
is `ccf5c98a41038064fbd31c587652c638534cf6f2d0067aa31c77a2ae9e3ae467`.
The eight historical Python source files included there are hash-checked test
oracle data, never executed by the native receiver or ordinary tests.

- 193 library tests and 13 black-box CLI test groups pass; zero failures.
  Four library entries are intentionally ignored in the ordinary suite:
  two subprocess helpers exercised by other tests, and two local-data gates.
  The real-IQ gate was then additionally invoked explicitly.
- `cargo fmt --check` and all-target Clippy with warnings denied pass.
- Final real-IQ selected-window audit passes again: 3/3 original full-FCS
  detections, one native and two repaired candidates, exact discrete provenance
  and 729598 search attempts. Decoder time 5.4914 s in this optimized build;
  the whole test took 7.45 s while other tests ran. This is not a matched
  Python speed benchmark.
- CLI tests execute children with an empty PATH: native WAV/IQ, metadata,
  AFSK, CW and candidate contracts do not rely on Python or installed codecs.
  Real OGG use still needs FFmpeg/ffprobe.

Evidence: [build/test manifest](../work/rust-migration-20260908/release-v1/build.json),
[test log](../work/rust-migration-20260908/release-v1/tests.log),
[final selected-IQ audit](../work/rust-migration-20260908/release-v1/real-iq/audit.json).

### Final executable thread benchmark

The same 90-second PCM segment was tested again with 1/2/4/8 workers, three
repetitions with rotated worker order. All twelve complete outputs, including
floating scores and journal order, are identical. The saved reference output
is also byte-identical to the earlier qualified Rust benchmark artifact.
The PCM SHA256 and all seven full-FCS frames remain unchanged.

| Workers | Median elapsed | Median process CPU |
|---|---:|---:|
| 1 | 7.0599 s | 7.0594 s |
| 2 | 3.7909 s | 7.1845 s |
| 4 | 2.1481 s | 8.1388 s |
| 8 | 1.6753 s | 12.3516 s |

Eight workers reduce wall time but consume more aggregate CPU than four;
parallelism is not free computation. Relative to the earlier same-boundary
Python median of 40.3755 s, the final four/eight-worker figures are about
18.80×/24.10× faster. Those Python and final Rust runs were not simultaneous
or randomized across implementations; shared-host and single-segment limits
still apply. This is not a blanket throughput guarantee.

Evidence: [final benchmark](../work/rust-migration-20260908/release-v1/benchmark-90s/result.json),
[bound input and executable](../work/rust-migration-20260908/release-v1/benchmark-90s/plan.json).

### Final executable full 93-observation replay: PASS

The delivered executable above was then replayed over the entire eligible
corpus with eight workers and the final durable campaign coordinator. It
completed 93/93 observations with zero failures, not merely a canary subset.
The strict independent audit again finds exactly 477 per-observation PDUs,
313 globally distinct and 125928 PDU bytes across observations. There are
zero missing/additional PDUs, zero original-FCS/validation differences and
zero ordered-window/discrete-provenance differences across all 15306 windows.
The same 522 of 868 floating scores have the disclosed maximum 2.22e-15
cross-language difference; no discrete comparison uses that tolerance.

Full batch wall time is 1873.8300 s (31m 13.83s); recorded signal-processing
wall-time sum is 806.3362 s (13m 26.34s). This batch includes OGG conversion,
hashing, persistence and qualification-related integrity work, so the matched
90-second measurement remains the acceleration benchmark. Native null smoke
also ran briefly alongside this batch; these are not isolated-host timings.
The owned service recorded 7187.105 s aggregate CPU, 1144836096 bytes peak
memory (1.066 GiB) and zero peak swap, below its explicit 6 GiB outer limit.

An explicit completed resume verified and reused all 93 observations in
45.6777 s, with zero failures or new decodes. The original terminal summary
remains byte-identical; its immutable SHA256 is
`4a35fa4db295253851cc40b382ab9511ac2ba0ba87c152d979f726a28bdb770e`.

Evidence: [final full-corpus audit](../work/rust-migration-20260908/release-v1/full-93-threads8/audit.json),
[terminal summary](../work/rust-migration-20260908/release-v1/full-93-threads8/summary.json),
[completed resume](../work/rust-migration-20260908/release-v1/full-93-threads8/summaries/summary-000002.json),
[service resource record](../work/rust-migration-20260908/release-v1/service-usage.json).
Final audit SHA256:
`f0a17ec578cb056b42171014aa66f1c1fb211ad04fdfb42102fec8078fc743f6`.
The post-resume audit is byte-identical. All 93 attempt directories remain
first attempts. A compact [delivery qualification record](../work/rust-migration-20260908/release-v1/qualification.json)
binds the final gates and limitations to the executable identity.

The final executable separately passed 90 FSK and 90 AFSK synthetic null
cases, zero frames and zero failed windows. As before, repeated silence is
not independent exposure and no population false-alarm claim follows.

## Limits and handoff status

No new FEC, AOS/USLP support or universal modulation support is implied. Physical
BPSK/QPSK/OQPSK bits are not automatically validated telemetry. CRC-constrained
repairs remain candidates; a ledger does not authenticate source or integrity.

The native receiver source is Rust. Third-party FFmpeg is deliberately retained
for OGG codec equivalence; optional external baselines are comparison tools.
Historical Python experiment/report/download scripts and the separate planning
project are not silently deleted or passed off as migrated receiver runtime.

Receiver migration and the finite regression/performance delivery gates above
are complete. Further held-out scientific validation, new protocol/FEC work,
archive-tool migration and operational deployment are separate work, not
silently claimed by these results. See the [scope inventory](rust-migration-inventory-v1.md).
