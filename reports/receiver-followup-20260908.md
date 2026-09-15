# Receiver follow-up: full bank and continuous timing — 2026-09-08

## Outcome

Two separate experiments completed. Both use previously exposed development
recordings, not new holdouts. No results were uploaded to a telemetry service.

### Full fixed-bank regression: all 93 OGG recordings

The unchanged qualified Rust migration executable ran all 160 original fixed
phase/rate hypotheses. All 93 recordings completed with zero failed windows.
The independent same-audio comparison reconstructed PDU sets, rather than
subtracting counts alone:

- 477 → **502** per-observation unique PDUs: **25 additions, zero losses**.
- Improvements in **18/93** recordings; aggregate increase **5.24%**.
- Global receiver PDU union: 313 → **327**.
- Globally absent from the entire frozen archive: 23 → **29 PDUs / 7,656 bytes**.
- **Six** of those archive-absent PDUs are new relative to the old receiver
  union, also absent from the complete baseline union: **1,584 bytes**.
- Total global archive-and-baseline-absent count: **25** (previously 19).
- **Three baseline observation/PDU pairs still missing** with this fixed bank.

All 93 input WAV hashes match the original same-audio comparison. All result
payload counters agree with reconstructed sets; each frame records the existing
independent bitwise FCS check. The 93 published result SHA-256 values match their
commit records, and all 93 commit hashes match the campaign summary. This does
not authenticate the transmitter. The six newly archive-absent PDUs still need
the same follow-up provenance/secondary replay treatment as previous candidates.

Summary:
`work/receiver-improvements-20260908/full-93/summary.json`
SHA-256 `64071ec73c57f7051a9e9db8964ad06725d2f2c28de0a42015801aa59d21002d`.

The first invocation stopped after five recordings. The resumed user systemd
service completed successfully; its reported wall time is 2,249.34 seconds,
excluding the earlier completed observations and the gap between invocations.
Do not interpret this as a matched isolated decoder speed benchmark.

### Implemented DSP experiment: frontend × timing

New `rust/tracking.rs` supplies:
- a square-pulse frontend, four-boxcar long-form DC rejection over 32 symbols,
  and causal RMS AGC with a 50-symbol time constant;
- a second-order non-data-aided Gardner clock, linear interpolation, bounded
  loop feedback and ±4,000 ppm average-rate limits.

The timing bank is fixed before receiving any reference: eight initial phases
and bandwidths 0.02/0.06, 16 tracking paths. Defaults of the qualified receiver
are unchanged. This implementation is **not** a bit-exact port of GNU Radio's
PFB interpolator. The alternative frontend also changes sample rate, startup,
normalization and delay relative to the old path; the experiment isolates two
bundles, not every internal filter parameter separately.

New `examples/tracking_audio_probe.rs` runs all four combinations, separately
records their exact full frame/FCS sets, and forms their deduplicated union.
It accepts neither reference payloads nor expected frame timestamps. No bit
repair is enabled. This is a standalone development tool, not yet a resumable
production backend.

| Observation | Legacy filter + full bank | New filter + full bank | Legacy filter + Gardner | New filter + Gardner | Union |
|---|---:|---:|---:|---:|---:|
| 14936424 | 18 | 9 | 16 | 3 | **19** |
| 14936415 | 16 | 10 | 14 | 10 | **17** |
| 14936407 | 16 | 9 | 15 | 6 | **16** |
| 14936444 | 24 | 15 | 24 | 12 | **26** |

All legacy full-bank PDU sets were retained by the union. The four selected
observations now reproduce **every same-audio baseline PDU**, including the
three misses left by the full fixed bank. This is selected-case parity, not a
new 93-recording trial of the hybrid or general SatNOGS parity.

There are **four additional observation/PDU recoveries versus full bank**:
three obtainable with the alternative frontend and fixed bank, one exclusive
to the legacy frontend plus Gardner within this experiment. One of the three
filter additions is also decoded by the new frontend plus Gardner.
**All four exist in the frozen archive**: they are local recovery improvements,
not four further globally archive-absent packets. Standalone new branches often
recover fewer frames; none replaces the existing branch.

Each recording used two workers, all four jobs ran concurrently and overlapped
compilation. Overall experiment wall times per recording were 128–199 seconds.
Per-window variant costs are retained but are not isolated CPU benchmarks.

## Reproduction and verification

Frozen probe:
`work/receiver-improvements-20260908/tracking-v1-release/tracking_audio_probe`
SHA-256 `455a9622ba7c839fbba1c575b8139502a9b24c2b3f99421adb2e9ab74bb37ab3`.

Source snapshot:
`work/receiver-improvements-20260908/tracking-v1-release/source.tar.gz`
SHA-256 `9045286eb1a870eda6f5f77bcd5015b7f5847e2fb46154659bb2ff52a14a958b`.

```sh
work/receiver-improvements-20260908/tracking-v1-release/tracking_audio_probe \
  --input work/satnogs-ogg-archive-week-20260831-v1/observations/14936424/capture.ogg \
  --output work/new-tracking-replay-14936424 \
  --observation-id 14936424 --threads 2
```

Output must be a new directory. Existing experimental results are under
`work/receiver-improvements-20260908/tracking-v1/<observation>/result.json`.

- `cargo fmt` / subsequent format check: pass.
- `cargo clippy --all-targets -- -D warnings`: pass.
- `cargo test --release --all-targets -j 2 -- --test-threads=2`:
  **208 library + 13 CLI = 221 passed**, four explicitly nonordinary checks/
  subprocess helpers ignored. Six new DSP tests cover both clock-error signs,
  phase acquisition, fixed-clock zero-bandwidth control, invalid/silent inputs,
  DC rejection and amplitude-scale invariance.
- Builds were complete before tests ran. Experiment executables were copied to
  their frozen path before another build, preserving executable identity.

Machine-readable comparisons:
`reports/receiver-followup-20260908-evidence.json`.

## Remaining gates

Run the hybrid on the full development cohort with fixed settings and retained
legacy outputs, then freeze a multi-mission/time-separated unseen confirmation
cohort and a matched compute-budget comparison. Measure false acceptance using
independently known truth and negative recordings. Do not add deeper CRC repair
without its own confidence evaluation. IQ MLSE and calibrated repair confidence
remain unimplemented proposals in this follow-up. Publication and deployment
readiness remain false.

## Algorithm references

Gardner detector formula and the long-form DC blocker are established methods,
not new scientific contributions. The primary GNU Radio sources were consulted:
[Timing error detector](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/timing_error_detector.cc),
[DC blocker](https://github.com/gnuradio/gnuradio/blob/main/gr-filter/lib/dc_blocker_ff_impl.cc).
The Rust implementation uses its own bounded state and arithmetic.

Graphify vocabulary trace: receiver, timing, archive, audit, status, soft.
The older graph was used for navigation only; current code and artifacts support
these findings. Planning-model readiness is not receiver evidence.

