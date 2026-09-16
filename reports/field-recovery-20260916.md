# Field recovery coverage pilot — 2026-09-16

This is a completed bounded engineering pilot, not an independent publication
benchmark. Both IQ comparisons and all 48 amended OGG receiver executions are
complete. No algorithms were tuned against these outcomes. The new mechanisms
show **no additional field packets over the previous native receiver** here.

## Design and provenance

- IQ: both complete pre-existing CANVAS recordings, observations 14115025 and
  14366383, station 106, CI16 little-endian at 57,600 samples/s. These are
  previously exposed development data, not a holdout. Every fixed 16-second
  window with a 15-second hop is processed, 17 windows per recording. Windows
  are not independent observations.
- OGG: four observations each from ISS APRS, SONATE-2 telemetry and CANVAS,
  selected by a fixed ID-hash ranking from saved first-page metadata for
  2026-06-01. Selection was frozen before audio download/decoding. This bounded
  page-prefix design is not representative of the whole archive. All twelve
  downloads succeeded, totalling 93,553,555 original bytes. Filename searches
  found no prior local selected-ID recordings, but renamed derivatives/content
  were not exhaustively audited: exposure remains **unknown**, not held out.
- The signal subgroup uses pre-existing archive waterfall labels, not our
  decoder outcomes: one with-signal, four without-signal, seven unknown.
- The endpoint is unique strict AX.25 UI payloads per recording, excluding FCS.
  Native packets preserve and recheck received FCS. External decoder outputs
  lack those FCS bytes and remain decoder-attested. No claim of transmitted
  ground truth or field false-alarm qualification follows from CRC acceptance.

The artifact root is `/home/ubuntu/framelift-field-20260916.QAl5x5`.
The frozen OGG cohort SHA-256 is
`5cdaddce0d1f2061a3317bc3388fbdac7040785f006c4151598778c6cd631e8d`.
Mission/profile evidence and exact limitations are in
[the pilot protocol](../docs/field-recovery-pilot.md).

## Complete historical OGG results, amended v2

All twelve inputs from three missions and eleven station IDs were downloaded,
converted and processed by all four arms: **48/48 completed, zero failures or
timeouts, zero unavailable inputs**. There are no silently omitted recordings.

| Arm | Observation/PDU pairs | Recordings with packets | Summed wall time (s) | Summed CPU time (s) |
|---|---:|---:|---:|---:|
| Previous native audio receiver | 18 | 1/12 | 143.170 | 508.670 |
| New native audio receiver | 18 | 1/12 | 146.387 | 516.300 |
| Dire Wolf 1.7 | 15 | 1/12 | 48.345 | 45.170 |
| gr-satellites 5.5.0 | 5 | 1/12 | 76.552 | 133.240 |
| External decoder union | 15 | 1/12 | — | — |

Old and new native frame sets are identical for every observation: zero added
and zero lost pairs. This is expected because the new IQ mechanisms are not
part of the native audio arm. This also does not benchmark the larger progressive
audio portfolio, which is a separate command.

Compared with the external union, native audio adds four pairs (1,056 bytes),
misses one (264 bytes), and yields three net extra pairs: 18 versus 15, or +20%.
Compared specifically with gr-satellites it adds thirteen and misses none.
**Every decoded packet and every difference comes from one recording,
CANVAS 14197262.** The other eleven recordings produce no accepted packets in
any arm. These results must not be presented as representative three-mission
superiority, and their apparent percentage precision is misleading. Only one
component carries positive outcomes; degenerate descriptive bootstrap intervals
are not evidence of a precise population effect.

The independently labelled with-signal subgroup contains only CANVAS 14211100.
All four arms recover zero packets there. Its absolute added-packet count is
zero and its relative gain is undefined, not 0% recovery of transmitted data.
The archive label does not explain whether the signal had the expected payload
format or sufficient quality.

All twelve canonical WAV container hashes and signed-16 sample hashes were
independently rechecked by the parent audit. The new audio run is not faster:
its concurrent-host wall total is slightly higher than the old run. These are
single-run descriptive process measurements, not an isolated performance test.

Final evidence lives under `audio-run-v2/`: `study.json`, `denominator.json`,
per-observation source/PCM receipts, all 48 raw arm receipts, and
`metrics-final.json`. The external-baseline views are
`study-vs-direwolf-complete-arms.json` and
`study-vs-gr_satellites-complete-arms.json`, with their corresponding
`metrics-final-vs-*.json` summaries. These views change only baseline/candidate
labels, retaining every original observation, arm and frame unchanged.

## Complete IQ results, amended v2

| Arm | 14115025 | 14366383 | Observation/PDU pairs | Summed wall time (s) | Summed CPU time (s) |
|---|---:|---:|---:|---:|---:|
| Previous generic receiver | 8 | 1 | 9 | 6.026 | 13.140 |
| New HDLC baseline | 8 | 1 | 9 | 14.160 | 14.015 |
| New HDLC plus sequence detector | 8 | 1 | 9 | 15.900 | 14.994 |
| gr-satellites 5.5.0 | 6 | 2 | 8 | 6.428 | 15.960 |

All eight arms completed without failed windows. The new sequence detector
adds **zero packets** and loses zero compared with both the new baseline and
the previous generic receiver. This pilot therefore demonstrates compatibility,
not increased yield from the new sequence algorithm.

Relative to gr-satellites, each native set adds three observation/PDU pairs
(598 bytes) and misses two (126 bytes). The net difference is one pair, or
12.5% of the eight-pair external denominator. Native output does **not** contain
all externally recovered packets; this tiny, exposed, single-station result
must not be promoted as a general percentage improvement. Shared station 106
joins the recordings into one statistical component, so no bootstrap interval
is reported.

The sequence lane had a validated training anchor in only 13 of 272 stream
receipts. The other 259 explicitly report `no_validated_training_frames`.
Only three of the 34 windows contained baseline packets. This explains a
practical coverage limitation: the guarded lane cannot operate without an
already validated anchor. It does not show that the lane would necessarily
recover missing packets if more anchors were available.

These times do not show a speed improvement. Both new native arms use four
candidate workers in v2, but old generic parallelizes windows, and external
timings include subprocess startup whereas native IQ timings are in-process.
The host was concurrently building and testing. CPU/wall boundaries and worker
placement differ, so the table is descriptive, not an equal-compute ranking.

Every arm reads the same original CI16 file. gr-satellites internally converts
to float32 divided by 32767; native processing reads integer-valued float64.
No identical intermediate floating-point-array claim is made. The external
GMSK profile explicitly uses 2400 Hz deviation rather than its 5000 Hz generic
default. No source crop, resampling, payload oracle or per-result tuning is used.

The HDLC sequence test does not exercise coherent CPM, repetition combining
or SIC for arbitrary HDLC traffic. Those features must not be credited for
these field results.

## Retained failures and amendments

The initial IQ v1 used one baseline worker and four sequence workers. Its
packet sets are retained separately and match v2. Before v2, the protocol was
amended to use four workers for both native lanes, a shared window-boundary
implementation and held-descriptor guards for external executables/profiles.
No DSP parameter or input was changed. Both v1 and v2 remain available.

The initial audio run exposed a container incompatibility: Dire Wolf 1.7's
`atest` rejected FFmpeg's WAV `LIST` metadata chunk before reading samples.
Those are recorded execution failures, not successful zero-packet decodes.
The owned campaign was stopped; original inputs, partial results and logs were
retained under `audio-run/`, with `interrupted.json` explaining the reason.

Audio v2 repeats all twelve selected observations and all four arms, using a
metadata-free, bitexact PCM16 WAV container. The numerical PCM digest is checked
against every available v1 WAV; the common canonical file is identical across
all four arms. No waveform samples, decoder binaries, modulation parameters or
frame acceptance criteria are intentionally altered by this amendment.

Because the disk has less than 1 GiB above the 4 GiB safety reserve, v2 common
WAVs are in `/dev/shm/framelift-field-pcm-v2.1eQhid/pcm`. These are volatile.
Original OGGs, source/container/sample hashes, exact conversion recipes,
executable hashes, logs and results are durable on disk. This does not yet
constitute a fully durable publication replication bundle. Original IQ/OGG and
v1 WAV files were not deleted or overwritten.

The first auxiliary external-baseline export listed only the two native
candidates while retaining a fourth arm in each observation. The strict metrics
validator correctly rejected those inconsistent views. They were preserved;
new `*-complete-arms.json` views register all three non-baseline arms. No receiver
output, observation or failed-arm denominator was changed. A regression test
now verifies that rebasing preserves all original observations and arm records.

## Runtime and checks

| Artifact | SHA-256 |
|---|---|
| Previous native receiver | `9a04404d7e170417d9d3e39d8a8d6c3a87969025dfe2186959e712b244373ae2` |
| Frozen new audio receiver | `f329e9aefb5ac63369351448ef8188dea870b166a4a5e3b1f6af2cf7573c5d8f` |
| IQ v2 benchmark executable | `bffa4144a8eebd754013a247d7c936007e96741ea38bcee8f06f91f1369c55f7` |
| Audio v2 benchmark executable | `80ac05e0eb96f767a1d92b104f29420e46b10bcf254ff4fe79266ea60e8357da` |

Installed versions: gr-satellites 5.5.0, Dire Wolf 1.7, GNU Radio 3.10.9.2,
FFmpeg 6.1.1, libsndfile 1.2.2. SatDump 1.2.2 is installed but was not selected
as the comparator for these explicitly profiled amateur AX.25 links. Dynamic
dependency closure is not hermetically sealed.

Both new benchmark examples pass strict Clippy. Example tests pass: eight for
audio and four for IQ, including shared transport tests. Four new tests verify
LIST-bearing versus clean WAV sample identity, detection of a one-sample
change, and preservation of a conversion failure without fake frame/input
evidence, plus complete arm registration when changing the comparison baseline.
Completed-receiver results are verified by the native paired-metrics
CLI; publication readiness stays false.

Current repository harness source includes subsequent reporting/error-retention
cleanup and tests. The executed benchmark executables above remain immutable;
their original receipts are never rewritten to pretend a later binary ran.

## Remaining publication gates

This pilot is too small, lacks independent payload truth, includes exposed or
unreviewed inputs, and does not demonstrate a new field-yield improvement over
the previous receiver. A confirmatory study still needs audited unseen source
lineages, more missions/modulation families and independent station/pass groups,
calibrated negative controls, externally reviewed receiver profiles, a sealed
runtime and durable waveform evidence. Publication claims should describe the
actual measured conditions, including losses and null improvements.
