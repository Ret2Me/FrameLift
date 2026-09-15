# PSK repair and qualification — 2026-09-12

Status: **development qualification in progress; not a final publication holdout**.
The prior pilot is preserved. No protected CANVAS recordings, frozen progressive
FSK study, production deployment or transmitter profile catalogue was modified.

Latest fully checked executable: **candidate13 passes392 library and17 CLI tests**.
It retains candidate10's opt-in carrier portfolio and candidate11's consumed-WAV
integrity repair. Candidate12's OGG snapshot failed its directory-privacy test
and is preserved as failed. Candidate13 corrects permissions at directory creation
and supplies verified original-OGG snapshots to both conversion tools. All288 PSK
banks exactly match10 (236480 streams/628556346 f64 values); all2499328 checked
WAV sample values match the independent formula. The earlier candidate09 passes376
library and17 CLI tests with an independent exact-output audit of its narrow
RS constant-table optimization. Its PSK source is unchanged from candidate08,
which passes the exposed repair replay and a
fresh-seed 6912-case high-SNR confirmation, plus 375 library and 17 CLI tests.
On the original nine real BPSK cases, the combined acquisition plan matches the
**same-window comparator's exact six-frame set**. The earlier 6-versus-5 result
used different window scheduling; its extra PW-Sat2 frame is a reset effect,
not an algorithm advantage. Five additional metadata-selected recordings yield
three native frames versus two comparator frames, with a one-frame Zhou Enlai
gain preserved through completed repeat, original-WAV and trailing-zero
EOF controls. A stronger fixed gr-satellites acquisition portfolio over all
14 exposed rate cases has completed. Its strict-UI union remains seven versus
nine native, but this is **not total decoded telemetry**. A separate symmetric
raw-FCS diagnostic finds twelve native PDUs versus eleven comparator PDUs,
with both gains and misses (independent audit passed). The new real-hardware
K2SAT QPSK single-pass hybrid test **fails: native 0 versus reference 88**.
The subsequent signal-ranked three-pass component test recovers **88/88**,
with all237 cells eligible and24 noise controls empty; independent audit passed.
The actual integrated candidate10 option now also passes an independently
audited matched-window test: **default1=0 versus portfolio3=88/88**,179872 unique
packet-body bytes,590 eligible cells,24 fresh noise controls empty. It costs
about2.96 times the measured native-plus-external-grading work on this capture;
the external FEC/framing dependency remains. This is an engineering-transmitter
recording, not an orbital-observation gain. The separate83520-call sensitivity study
has passed its independent audit: candidate06 9689/20736 positive cases versus
candidate09 9862/20736, **173 additional exact case recoveries, zero losses**.
These are simulated cases with 32 payload groups, not 173 new real frames.
It also exposes 99 high-SNR misses at SPS8, shared with the older receiver.
An audited, outcome-selected cause-isolation study supports complementary
timing/carrier tracking effects; it does not yet qualify a combined repair.
Broad final publication qualification remains NO-GO pending these repairs.

## Audited candidate02 checkpoint

The complete legacy fixed bank is retained. An additional signal-only joint
carrier/timing bank corrects OQPSK arm rotation order and tracks off-grid clocks.
Window power is computed once using the identical floating-point reduction.
No payload, reference bits, CRC or FEC outcomes steer synchronization.

| Test | Frozen old receiver | Repaired candidate01/02 | Qualification boundary |
|---|---:|---:|---|
| Original synthetic positives | 135/180 | 146/180 | BPSK 59→60; QPSK 44→44; OQPSK 32→42; no lost frames; two reused packet strings |
| Original negatives | 18/18 clean | 18/18 clean | Conditional controls, not a station false-alarm estimate |
| One-impairment diagnostic | 85/96 | 96/96 | Clock × drift interaction isolated; development data |
| Expanded fresh-seed positives | 1115/1440 | 1200/1440 | Direct candidate01 run; 85 gains, no losses; 240 low-SNR misses remain |
| Expanded negatives | 192/192 clean | 192/192 clean | 64 zero, 64 noise and 64 tone; full receiver searches |
| PicSat BPSK 9600 | 0 frames | Candidate01: 57 frames | Same narrow plan/IQ; candidate04 exposed a resource-admission regression described below |
| Independent GNU Radio RRC transmitter | not run | 95/96 positives; 96/96 bad-CRC negatives clean | Candidate02; one OQPSK 2-SPS stress failure under repair |

Expanded positives are 480 cases per modulation: BPSK 475→480, QPSK 358→360,
OQPSK 282→360. All 864 expanded cases at sample SNR ≥4 dB passed. SNR is
complex-sample SNR, **not Eb/N0**. These cases are clustered by payload, seed,
shape and impairment; do not treat them as independent satellite observations.

All-soft parity candidate01→02 passed on 294 original/diagnostic cases:
665,600 ordered streams and 1,225,713,880 soft values, including exact f64 bits,
labels, thresholds, scores and accepted frame bytes. This supports a lossless
cache optimization **on the admitted tested workloads**, not identical admission
of every oversized window: candidate02 also corrects frontend work accounting.
Expanded 1632-case results directly test candidate01; they were not silently
relabeled as a fresh candidate02 run.

Candidate02 regression: 365 library tests passed, 6 deliberately ignored, zero
failures (445.97 s debug); all 17 CLI integration tests passed (126.63 s debug).
These are test-suite times, not demodulator throughput measurements.

Candidate02 immutable executable:
`work/psk-repair-20260912-v1/candidate02/telemetry-yield-rs`, SHA-256
`82bbd45bd343c2d95985baabc45c7b0c9eb0ffd879c9aaaea5cb59c9afc9805d`.
Its PSK source SHA-256 is
`c52233cc5534f846bf61cc1f32151047344a926d585784083ba160b53f1e9af7`.

## Real-data causal checks and limits

PicSat's external recovered clock is about +3198 ppm relative to nominal, outside
the original fixed ±250-ppm grid. A separate frozen-old run with a wider fixed
grid also recovers 57 frames. Thus the repair improves acquisition robustness
under the narrow declared plan; it does **not** establish 57 previously unknown
frames or an inherently novel synchronization algorithm.

GR01/DUTHSat exposes **two different issues**. Original IQ yields no raw received-
FCS-valid frame with the initial native acquisition. External post-Costas yields
one. That frame's address-extension bits are malformed for the strict AX.25 UI
validator, which rejects it even when the received FCS is correct. Consequently,
generic `unique_frame_count=0` after a working acquisition stage is not itself
evidence of a demodulation failure.

Exploratory, payload-blind sample-rate PLLs and coherence-weighted linear/quadratic
dechirping each recovered the same full-FCS GR01 frame using native downstream
demodulation. Its full-frame SHA-256 is
`8d345bd1743eeb020cfddcdd0cd602ef60ffbc4a8aa896d428045e425e8b48c1`.
It is byte-equal to the external frame and to its KISS payload after removing the
two received FCS bytes. These diagnostics do not relax production AX.25 parsing.
The weighted regression was developed after inspecting the signal-only spectrum;
it is **exploratory development**, not a predeclared successful holdout test.

Widening the symbol-rate NCO alone was an unsuccessful exploratory candidate;
its executable/source/results are retained under `candidate03-wide-negative/`.
It was removed from the live source rather than retained as unnecessary work.

## RML24: mixed bit-level result

The same 288 already-exposed records and two filters produced 576 rows. An
independent scalar audit agrees on all 1152 BER/alignment comparisons. Total
errors changed 178884→178639 (245 fewer): BPSK 26 more, QPSK 403 fewer, OQPSK
132 more. Twenty-five rows improved, 499 tied, 52 worsened. All 489 selected
legacy outputs are byte-identical; 87 outputs select the new joint lane.

The maximum-eye selector is **not monotonic in truth-bit accuracy**. These are
reference-aligned bit diagnostics, not decoded telemetry or evidence of broad
OQPSK superiority. The framed receiver visits the full portfolio rather than
selecting just this one eye-score winner.

## Candidate04/05 follow-up and candidate07

**Whole-file regression found after the checkpoint:** candidate04 rejects the
first 240000-sample PicSat window because its newly complete frontend-work
estimate exceeds the retained 200-million default cap. Only the tail runs,
recovering 5 frames; the result correctly says `failed`, not completed-empty.
The 57-frame original-plan result above belongs to candidate01, not a completed
candidate04/02 end-to-end replay. Candidate05 uses a common
500-million frontend CPU allowance (already admitted for the opt-in drift bank),
keeping memory, stream, 200-million soft-visit and one-billion aggregate limits.
No input-specific setting or shorter plan is being substituted to hide this
regression. Candidate04 artifacts remain immutable. **Candidate05 now completes
the exact original PicSat plan: 2 windows, 0 failures, 57 exact unique frames.**
Its frame-set SHA-256 is
`68bdcdf77e1ccae38e0fa9fbad73b3b19bac77806d20b8c852cbf662fd22a2ed`.
One replay took 1.0023 s; this single run is not a controlled speed comparison.

- Add a four-point Lagrange joint lane for ≤4 samples/symbol while retaining
  fixed and linear-joint lanes. Independent diagnosis isolated the 2-SPS failure
  to fractional-delay interpolation; the test mirror recovers the exact frame
  with this change alone. Candidate04's exact registered replay now passes all
  96 positives and all 96 paired bad-CRC controls, with all input hashes checked.
- Add optional BPSK `max_carrier_drift_hz_per_s`: 128-symbol local square-law
  spectra, at most 96 fixed positions, coherence-weighted bounded linear fit.
  Constant-CFO bank remains intact, and no frame feedback enters the fit.
- Preserve explicit resource admission, provenance, received-integrity semantics,
  one/four-worker equivalence and checkpoint identity binding.
- Run paired CPU/wall measurements, fresh-seed low-SPS controls, raw-FCS and
  strict-UI real-capture audits and the complete regression after the final build.

Fresh low-SPS transmitter confirmation (1728 cases, frozen plan) finds
**848/864 positives and 864/864 clean bad-CRC controls** in candidate04. All 16
misses are stressed OQPSK with rolloff 0.2. Its strict all-positive criterion
therefore **fails**. A post-result split-loop diagnosis rescues all 16 by doubling
only timing-loop bandwidth, versus only 2/16 by doubling carrier bandwidth.
Candidate07 adds a separate faster-timing cubic lane for all low-SPS OQPSK,
preserving every earlier lane. Its complete registered v2 replay now passes:
**864/864 positives, 864/864 clean bad-CRC controls, zero decoder errors**.
The original 192-case replay also passes 96/96 positives and 96/96 controls.
All reconstructed input hashes match the original cases. On a balanced subset
of 216 cases, all retained older outputs match candidate06 bit-for-bit:
100224 ordered streams and 268663168 soft values, including scores/thresholds.
These are post-repair replays of exposed development data, not a new publication
holdout. The subsequent independently reconstructed fresh-seed v3 confirmation
finds **3450/3456 positive recoveries and 3456/3456 clean bad-CRC controls**.
All 6912 IQ/packet identities reproduce exactly; there are no unexpected frames
or decoder errors. Six QPSK cases with rolloff 0.2, negative 9000-ppm clock,
CFO -0.17 baud and +140-Hz/s drift fail at 2–2.25 samples/symbol. All BPSK/OQPSK
cases pass. Thus **candidate07 fails v3's strict all-cases criterion** despite
the earlier complete replay successes. This negative result is preserved while
the QPSK timing-acquisition cause is investigated.

Candidate07 also reran the entire earlier 1632-case low-SNR development suite:
the same **1200/1440 positives, 192/192 clean controls and 240 misses**, with
exact previous IQ/truth/frame sets, no gains/losses/errors/unexpected frames.

A fixed-profile expansion over six previously archived public BPSK recordings
(nine file/rate cases) has completed all old/candidate07/drift/gr-satellites arms
on the identical derived IQ. Preliminary strict-UI counts are five frames for
candidate07 and five for gr-satellites, but the sets differ: candidate07 finds
one additional PW-Sat2 frame and misses one MYSAT-1 frame. This is **not blanket
baseline parity**. Independent reconciliation and repeatability checks were
pending at that checkpoint and subsequently completed below. All arms completed successfully; raw externally CRC-attested packets
with malformed UI headers are reported separately, not silently promoted.

The opt-in drift branch was tested in a pre-execution frozen development grid:
**81→192/192 positives**, 111 gains, no losses, and **64/64 clean controls**.
These are BPSK rectangular/RRC signals with signed drift, noise and off-grid
clocks; two fresh packet/noise seeds are reused across the grid. They are not
192 independent satellite observations. On real GR01, candidate05 with a
600-Hz CFO bound alone recovers no raw CRC frame; adding the bounded 200-Hz/s
drift lane recovers the exact one external raw-FCS packet. Strict AX.25 UI
acceptance remains zero because the received header is malformed.

Candidate04's exposed RML24 replay is mixed: 179082 errors versus candidate01's
178639 and the old receiver's 178884, all over 557120 truth bits. Relative to
candidate01: BPSK 35 fewer, QPSK 635 fewer, OQPSK 1113 more. All 1152 independent
scalar alignment/BER checks agree. This is the signal-only maximum-eye-selected
bit diagnostic; the full framed portfolio retains the earlier outputs.

Candidate05's frozen full library suite passes **370 tests, 0 failures, 6 ignored**
(438.28 s). An earlier run invalidated by rebuilding its own executable is
preserved and explained in the test-orchestration amendment, not counted as a
passing result. Candidate07 full library regression now passes **375 tests,
0 failures, 6 ignored**, with unchanged executable hashes before/after (612.95 s
debug, concurrent research load). All 18 targeted PSK tests passed (90.46 s
debug), and all 17 CLI tests passed
against the immutable release executable (6.82 s). That test runner's compiled
CLI path points to the frozen candidate, not Cargo's mutable build output.

Candidate06 isolates a rolling 32–128-bit sync matcher optimization from PSK
changes. Nine coded-sync unit tests pass; the independent 108-case full-output/
error oracle is byte-identical (41004 bytes) for None/RS/LDPC, 32/64/128-bit sync,
0–2 mismatches, threshold ties and candidate/error-order boundaries. Balanced
full coded-sync decoder benchmarks show 1.118–3.612x scenario-specific median
speedups, with all result signatures unchanged. These include framing/FEC but
**exclude IQ demodulation**, so they are not whole-radio pipeline multipliers.
One broader paired timing now measures **114.474→105.967 ms** for a complete
BPSK positive/noise pair: **7.43% less elapsed time (1.0803x throughput)**, with
exact outputs (one expected CRC-valid positive, zero noise frames; all 16+16
streams visited). Six balanced old/new samples were pinned to one CPU. This
small generated workload does not establish a universal or real-station speed
gain. Previous power-cache measurements
did **not** establish a reliable overall speed gain: combined paired CPU saving
was -1.15%, with mixed workload results.

Do not use an intermediate candidate for a supposedly final scientific benchmark
while its checks are pending. Real QPSK/OQPSK telemetry and an untouched,
prespecified population evaluation remain separate qualification requirements.

### Candidate08 follow-up and real-cohort reconciliation

The six candidate07 QPSK misses have a reproducible timing-capture cause.
An independent baseline mirror matches all 2304 streams / 6139664 soft values
over the six positives and their six bad-CRC siblings. Baseline and carrier-only
bandwidth change each recover 0/6; timing-only 0.02 with carrier 0.01 recovers
6/6, including the cubic lane itself. All control variants remain rejected.
Candidate08 appends this faster cubic timing lane for low-SPS BPSK/QPSK too,
retaining the complete candidate07 bank and charging the extra work. The
resume identity is `rust-psk-soft-v7`. Its full v3 replay passes 3456/3456
positives and 3456/3456 controls, recovering all six known misses. A balanced
432-case audit preserves 255744 old streams and 685791618 soft values exactly,
including the existing OQPSK fast lane. Fresh v4 qualification also passes all
3456 positives and 3456 paired bad-CRC controls, with no extra frames or errors.
An independent auditor reconstructs every IQ hash and checks 32 new packet
contents and all 3456 noise seeds are disjoint from v3. This is fresh-seed
development confirmation under the same high-SNR envelope, not an untouched
operational benchmark or 3456 unique telemetry packets.
The immutable full library regression passes 375 tests (zero failures, six
ignored, 565.04 s). All 17 release CLI tests pass;
PicSat again has all 57 exact frame objects, and GR01 retains raw FCS 1 / strict
UI 0 with the opt-in drift branch.

The initial real BPSK nine-case comparison is now independently reconciled,
with every one of its 36 arms eligible. Candidate07 and the comparator each
yield five strict UI frames, but the sets differ: native adds one PW-Sat2 frame
and misses one MYSAT-1 frame. The PW extra carries a valid received FCS and
strict UI structure; another unchanged-input run repeats the native 3 versus
comparator 2 unique-PDU sets. IL01 and DUCHIFAT-3 each contribute one exact
shared frame. KR01's extra raw comparator packet is not a strict UI frame.

On MYSAT-1, all eight post-result loop-bank arms complete without errors:
loop bandwidths 0.01, 0.02, 0.05 and 0.1 at CFO bound 600 Hz, each under whole
capture and five-second nonoverlapping schedules. Only 0.05 recovers the exact
one external PDU under both schedules. Its full received-FCS 60-byte frame
has SHA256 `643745e2` (prefix), independently valid X.25 residue and UI header.
This is a **configuration improvement of unchanged candidate07**, not an
algorithm change. The original 240-Hz bound and 0.01 bandwidth both differed;
a full-cohort factorial follow-up tests that distinction. All four 1200-baud
cases have distinct CFO levels 240/600 Hz; the five 9600-baud cases have the
same 1920-Hz bound under both labels and their duplicate executions are
repeatability controls, not independent factor levels. None of these already
exposed recordings becomes a pristine test set through retesting.

The configuration follow-up completed all 54 executions with no ineligible
arms. Candidate07's union recovers six strict frames versus the frozen
continuous gr-satellites five, with one additional PW-Sat2 frame and none
missing. All old-receiver arms remain zero. MYSAT-1 succeeds only with **both**
the wider 600-Hz bound and loop bandwidth 0.05; neither one-factor change
succeeds. Conversely, replacing every plan with bandwidth 0.05 loses the IL01
and DUCHIFAT-3 frames, so this result requires retaining the original slow
branch. These counts are not a population estimate of a 20% improvement.

The real combined-plan integration also passes on candidate08: all nine
cases complete with exact equality to that six-frame union. Each plan includes
original-slow, wide-slow and wide-fast hypotheses, removing exact duplicate
configurations before changing labels. There are three distinct hypotheses at
1200 baud and two at 9600 baud. This confirms the normal single-process
receiver, including aggregate admission, rather than merely an offline union
of independent commands. Matched-schedule controls remain separate, because
the original native five-second schedule and continuous comparator differ.

### Matched scheduling removes the apparent PW-Sat2 algorithm gain

The independent matched-schedule audit completes all nine five-second cases.
Native candidate07 at its original 0.01 bandwidth and gr-satellites resetting
on exactly the same five-second sample ranges give equal per-window and union
sets in eight cases. MYSAT-1 remains a native miss. For PW-Sat2 specifically,
**both receivers recover three unique frames when reset by window, and both
recover two on the whole recording**. The previously reported extra native
frame therefore measures a scheduling/reset benefit, not a superior
demodulation algorithm.

Whole-record comparison has eight eligible cases: seven equal sets and the
MYSAT-1 miss. PW-Sat2 at 9600 baud exceeds the native 500-million frontend-work
cap and is retained as **ineligible with a null comparison**, not decoded-zero.
The audit runner initially mishandled that expected failure twice; original
failed runs and corrected append-only continuation remain in the artifacts.
No completed valid work was rerun to conceal them.

An independent relational audit compares candidate08's combined plans with
the window-matched gr outputs and finds **exact set equality in all nine
cases: six strict frames on each side**. The MYSAT configuration repair closes
the remaining miss; it does not create a seventh frame. This is useful local
parity qualification, not proof of superiority or station-wide sensitivity.

### Five additional metadata-selected real BPSK recordings

The separate five-case plan was frozen before inspecting signal/decoder outcomes
for that cohort. All 20 arms complete and pass an independent result audit.
Native candidate08 recovers one strict UI PDU each from EntrySat, FMN-1 and Zhou
Enlai; gr-satellites recovers the exact EntrySat and FMN-1 PDUs but no Zhou PDU.
Both receivers return zero on Shaonian Xing and 3CAT-2. The frozen old native
receiver returns zero throughout. The combined 0.01/0.05 plan adds no frames
over candidate08's 0.01 plan here.

Totals are three native PDUs / 121 bytes versus two comparator PDUs / 84 bytes.
Those byte counts include AX.25 headers and exclude FCS; they are not counts
of application information bytes. Zhou's extra 37-byte PDU has an independently
valid received FCS and legible beacon message. Its entire 1.476-second source
fits one native window, so multiple-window cutting does not explain this gain.
All nine predeclared post-result controls completed: two exact-IQ repeat pairs
each retain native 1 / gr 0; gr's original-WAV route gives 0; and native/gr
pairs with 0.25-second and 1-second trailing-zero padding each retain 1 / 0.
These controls do not support a converter-only or simple EOF-flush explanation.
They do not establish physical sensitivity or scientific novelty: acquisition
settings remain a possible cause and are tested separately over all 14 cases.

### Candidate09: immutable RS basis tables

The only production change relative to candidate08 replaces per-call construction
of the two RS basis-conversion tables with one compile-time immutable table.
All 256 forward/inverse entries match the legacy construction exactly; repeated
calls share the same static object. The PSK and coded-sync sources are unchanged.
The independent complete-output oracle remains byte-identical over 54 full
cases, 54 budget/error-order cases and three edge checks: 41004 bytes, SHA-256
`becb3bb1144e69e041bfb50eed0ab01c1992f182e881fce6208b8268ba5dacb1`.

Full immutable regression passes **376 library tests (6 deliberately ignored)**
and **17 release-CLI integration tests**, zero failures. Sixteen balanced timing
rounds show median elapsed changes of -3.57% in an RS-dense workload and -6.32%
in one deterministic IQ→BPSK→RS→CSP positive/noise workload. No-RS and single-tail-RS
controls instead regress by 0.82% and 1.17%. Thus the optimization preserves
tested outputs and avoids repeated work, but does not support a universal
whole-pipeline speedup claim. Previous candidate08 waveform experiments retain
their original binary attribution rather than being relabeled as candidate09 runs.

### Comparator acquisition portfolio and symmetric raw-FCS endpoint

All 42 predeclared additional gr runs over the same 14 exposed rate cases
complete: default/clock/FLL/both strict counts are 7/6/4/4; their union is the
same seven default PDUs. Neither wider clock tolerance nor wider FLL recovers
Zhou in these fixed contracts. However, the raw gr union has eleven PDUs:
lowercase Shaonian Xing addresses and other malformed-header packets are
excluded by the strict endpoint. They cannot be dismissed as random junk or
silently treated as undecoded data.

The new, separately frozen lower-level native diagnostic keeps every original
compound plan and five-second window, but observes the existing raw received-
FCS API before UI filtering. All 14 real and 14 matched-length AWGN arms
complete; all earlier strict native subsets reproduce exactly, and all noise
controls are empty. Independently audited exact counts are **native 12 PDUs / 1311 bytes**
versus **gr union 11 PDUs / 953 bytes**, nine shared. Native-only: PW-Sat2
(already a known scheduling effect), Zhou, and one 137-byte ITASAT-1 PDU.
gr-only: one 15-byte nonconformant IL01 PDU and the 47-byte malformed-header
KR01 PDU. Native also recovers both lowercase Shaonian Xing packets exactly.
ITASAT-1 has actual received FCS `beb0` and 288 supporting hypothesis origins,
but fails the strict address-bit contract. The independent auditor passed
the complete raw results (audit SHA256
`8eb18e26483723dda17b1cb4869ad1ad3eda1799b553a0573abe21b67849efe2`).
Those 288 origins are correlated decoder hypotheses, not separate received
transmissions. Byte totals include link headers, exclude FCS,
and do not establish useful application-telemetry bytes or broad superiority.

### K2SAT engineering-transmitter QPSK hybrid: complete negative result

Public source: 216035328-byte CF32LE, 27004416 samples at 2 Msps, QPSK
500 kbaud, RRC0.35; capture SHA-256
`54ba4f7c8c835e7cae4c0d51fe63f2be1bae5a52b478c5f07a7537c9988a089c`.
This is a hardware engineering-model transmission, not an orbital recording.
The external front end follows the published GNU Radio topology; external
Viterbi/differential/V.35 output is checked by an independent Rust CRC-16
implementation. The unmodified installed gr-satellites component independently
matches all **88 unique CRC-valid reference payloads**. Its emitted-PDU length
bug is handled by checking actual marker boundaries and received CRC scopes.

The frozen native09 front end uses the same original wideband source, known
center +500 kHz, residual CFO bound100kHz, four phases, clock0ppm, RRC0.35/span6,
loop0.01, and every old/new timing and ambiguity lane. All 55 half-second
windows at quarter-second stride and 12 full-bank AWGN controls complete:
**0/88 reference payloads, zero noise payloads, zero window failures**.
The 607.61-second experiment includes thousands of external downstream calls;
it is not a native-versus-gr speed benchmark. Frozen result SHA-256:
`db77b2d72456b3c45117ad7b811d8b3d0c1547241b934f29283de34dbbcd994e`.

This negative result rejects candidate09's single-pass real-QPSK qualification;
the later integrated carrier repair is tested separately below. Signal-only
inspection observes a dominant fourth-power DC line during transmission.
A post-failure diagnostic completed all five selected windows × original,
translation-only and published channel-low-pass arms: all15 recover zero.
Filtering alone therefore does not fix the tested failures. Instrumenting
the external reference Costas frequency gives about +38kHz during transmission,
while native acquisition chooses 0Hz and its subsequent carrier loop can
correct only ±15.9kHz at this baud. Native/reference coded-soft alignment is
near chance. A separately frozen, signal-ranked alternative-carrier diagnostic
retains the legacy pass and tries two separated local spectral peaks. All24
cells are independently reconciled: three selected real transmission windows
recover6/10/10 payloads,26 unique exact reference payloads total; no native-only
payloads and all3 fresh AWGN controls empty across allthree passes. The unchanged
legacy and negative-frequency alternative recover0. A full55-window plus24-noise
confirmation was frozen for237 cells but both shards stopped during their
first legacy cell: native soft/score parity matched, while the unchanged
external streaming FEC output differed at EOF by80 decoded bits. Partial
journals and the failed gate are preserved. A separate96-call test and192-
branch independent bit-file/CRC audit qualify a deterministic complete-block
deployment of the same external FEC object: all direct repeats are bit-exact,
all scheduled/direct common prefixes and packet sets agree, and all eight
full-reference repeats recover88. Amended full-capture v2 completes all237
cells and30336 streams: per-arm real unique counts0/88/0, all24 noise inputs
empty on every arm, no native-only payloads. All8576 historical native soft
streams match exactly;268 historical external tail differences change no packet
set. Independent received-CRC/coverage/parity audit passes (SHA256
`4d68a4f0eb1755371a8b25243fee31a9dddfe8335607764517b0064aa842c2d3`).
This whole-capture three-pass hybrid success is distinct from the preserved
single-pass failure and still is not native Rust Viterbi/V.35/K2SAT support.
The optional in-program carrier portfolio subsequently passed scoped regression
and matched-window testing below. No old result is relabeled as that test.

### Complete fresh-seed sensitivity study

All83520 receiver executions on41760 paired inputs completed without execution
errors. Candidate06 recovers9689/20736 positive cases, candidate09 9862/20736:
+173 exact positive recoveries, no losses or unrelated packets; mode gains
are BPSK10/QPSK66/OQPSK97. The deliberately broad complex-sample-SNR grid gives
46.726%→47.560%, not a station success-rate estimate. This is development
qualification with32 fresh payload groups, not a blind population holdout.
All666 old-stream parity cases pass:274688 streams/803370608 f64 values.
Both receivers accept0/288 pure-noise inputs. The one-bit-invalid-CRC controls
produce2→3 restorations of the original valid packet, all at2dB; these were
predeclared separately and are not counted among positive gains or as unrelated
fabricated packets.

At24dB all1728 SPS2/4 positive cases pass, but candidate09 still misses99 SPS8
cases (7QPSK,92OQPSK), all identically missed by06. All clean-profile SPS8
cases pass; the high-SNR misses involve clock/CFO/drift stress. This broadens
the earlier v4 SPS2..4 test and exposes an unresolved synchronization limit;
the failure location alone does not prove timing is its cause. All240 strata,
all99 miss IDs, per-content-group descriptive summaries and raw journals are
retained. Shared-host summed elapsed time rises about11.2%; this is not a
controlled crossover speed measurement. A separate SPS8 cause-isolation study
uses all99 failures and paired controls, a full clock/CFO/drift factorial,
and isolated timing/carrier/interpolation/carrier-peak interventions. Its
protocol and complete source passed preflight; all8134 component passes have
now completed and its independent audit passed. On exactly unchanged99 failed
IQs, timing-only .02 recovers69, carrier-gain-only .02 recovers54, a wider carrier
clamp recovers0, added cubic recovers9, added cubic+fast timing recovers70, and
three coarse carrier candidates recover0. Every arm retains179 passing guards;
all99 bad-CRC and278 fresh noise controls are empty in every arm. Native/mirror
and retained banks match across616064 ordered streams/1645297296 f64 values.
The full clock/CFO/drift factorial independently reproduces all input hashes
and supports a combined timing/carrier limitation. A post-hoc union of stored
arm outputs covers83/99, but no such combined receiver has been tested;16 remain
unresolved. This outcome-selected diagnosis does not change the frozen
sensitivity results or establish an independent holdout success.

### Integrated carrier option and input-integrity qualification

Candidate10 adds `carrier_candidates:1..3`, default1, without dropping or
relabeling old streams. All configured banks and signal-only peak selection
are charged to unchanged500M frontend/200M soft-visit limits. Its independent
integration study completes432 calls over144 configurations/108 IQs: all
three arms recover46/48 positives with0/48 bad-CRC and0/48 noise acceptances.
The two retained misses are OQPSK SPS8. Pinned09→10 default parity covers59008
ordered streams/157192364 f64 values; all default streams remain in portfolio3.
There is no packet gain on that small synthetic integration cohort.

The separately frozen actual in-program K2SAT test uses all271 matched0.1s
windows at0.05s stride, including the4416-sample final tail, plus24 fresh noise
inputs. Both default1 and portfolio3 run the same IQ, all declared native banks
and the same qualified external downstream grader. **0→88 exact target packets**,
179872 unique packet-body bytes, no native-only payloads and no noise packets.
All590 cells are eligible; all37760 ordered default-prefix records are exact.
The independent no-receiver auditor reconstructs295 input hashes and checks
received CRC scopes, work accounting, coverage and journals. The6640 successful
stream origins collapse to118 window-payload instances and88 unique packets
across86 real windows; correlated hypotheses are not independent receptions.
The two shards took1431.39/1415.96s. Summed arm times707.61→2097.01s include
external grading and are not a speed comparison with gr-satellites. Audit:
`e3dc7bf1227008c5782bf48da16ceb4e25af00f3c70d068125e4ef680b6d218e`.

Candidate11's input-integrity repair follows a preserved full-regression
failure: metadata-only audio checks allowed same-size mutations in52/100 old09
and14/100 new10 standalone trials when file timestamps did not change.
WAV reads now use SHA256-authenticated64KiB blocks bound to the frozen full
hash; the actual checked byte copy reaches the sample decoder. All389 library
and17 CLI tests pass, including deterministic metadata-collision/cache/seek
tests. All288 candidate10 PSK integration banks replay identically on11:
236480 streams/628556346 f64 values. A balanced12-WAV reader benchmark compares
39989248 f64 values without one differing bit. The security check costs time:
median summed setup31.63→91.78ms and reads61.35→117.21ms. This is not a speedup.
An independent review finds no P1/P2 in that WAV path, but identifies a
pre-existing original-OGG conversion race. The separate OGG-snapshot candidate12
failed a new privacy assertion: its temporary directory was0775, not0700.
The source-only review's mistaken privacy assumption is explicitly corrected
in a preserved addendum. Candidate13 requests0700 at directory creation, then
copies and SHA256-verifies at most64MiB of original OGG into a read-only snapshot
used by both ffprobe and ffmpeg. All392 library tests pass, including actual
conversion after the original OGG is corrupted and preservation of old resume
metadata. All17 frozen CLI tests also pass. The same144-configuration transfer
to13 again preserves all288 banks/236480 streams/628556346 f64 values exactly;
a separate12-WAV pass preserves2499328 sample values. Candidate11 and the failed
candidate12 remain immutable; these new gates do not relabel earlier real-data
studies or constitute a new publication holdout.

## Evidence

- [Four-arm gr acquisition portfolio](../work/psk-repair-20260912-v1/gr-acquisition-portfolio14/RESULTS.md)
- [Symmetric raw-FCS diagnostic protocol](../work/psk-repair-20260912-v1/raw-fcs-symmetry14-v1/PROTOCOL.md)
- [K2SAT failed native hybrid result](../work/psk-repair-20260912-v1/k2sat-hybrid-v1/native-run-v1/results.json)
- [K2SAT reference cross-check](../work/psk-repair-20260912-v1/k2sat-hybrid-v1/reference-crosscheck-v1/result.json)
- [K2SAT diagnosis and qualification limits](../work/psk-repair-20260912-v1/k2sat-hybrid-v1/RESULTS.md)
- [Frozen low-SNR study](../work/psk-repair-20260912-v1/sensitivity-v1-candidate06-09/plan.json)
- [Complete sensitivity result and curves](../work/psk-repair-20260912-v1/sensitivity-v1-candidate06-09/RESULTS.md)
- [SPS8 cause-isolation result and audit](../work/psk-repair-20260912-v1/sps8-causal-ablation-v1/RESULTS.md)
- [Carrier integration preservation qualification](../work/psk-repair-20260912-v1/carrier-integration-qualification-v1/RESULTS.md)
- [Integrated K2SAT same-window independent audit](../work/psk-repair-20260912-v1/k2sat-hybrid-v1/integrated-carrier-v1/INDEPENDENT_AUDIT.md)
- [Candidate11 build, complete regression and reader overhead](../work/psk-repair-20260912-v1/candidate11/BUILD.md)
- [Candidate13 final checkpoint and complete verification](../work/psk-repair-20260912-v1/candidate13/BUILD.md)
- [Preserved candidate12 privacy failure](../work/psk-repair-20260912-v1/candidate12/BUILD.md)
- [Corrected independent OGG snapshot review](../work/psk-repair-20260912-v1/audio-integrity-qualification-v1/OGG_SNAPSHOT_REVIEW_ADDENDUM.md)
- [Post-hoc K2SAT carrier-rank coverage](../work/psk-repair-20260912-v1/k2sat-hybrid-v1/integrated-carrier-v1/POSTHOC_RANK_ABLATION.md)
- [Final publication-test readiness gates](psk-publication-readiness-20260912.md)
- [Symmetric raw-FCS independent audit](../work/psk-repair-20260912-v1/raw-fcs-symmetry14-v1/INDEPENDENT_AUDIT.md)
- [Candidate09 build and regression](../work/psk-repair-20260912-v1/candidate09/BUILD.md)
- [Candidate09 independent RS audit](../work/psk-repair-20260912-v1/rs-const-audit-v1/README.md)
- [Zhou repeat/input/EOF controls](../work/psk-repair-20260912-v1/real-bpsk-five-candidate08/zhou-repeat-contract/RESULTS.md)
- [Real QPSK/OQPSK source feasibility](../work/psk-repair-20260912-v1/real-psk-source-audit/README.md)
- [Expanded tests](../work/psk-repair-20260912-v1/expanded-synthetic/RESULTS.md)
- [All-soft parity](../work/psk-repair-20260912-v1/synthetic/soft-parity/RESULTS.md)
- [OQPSK source audit](../work/psk-repair-20260912-v1/review/OQPSK_SOURCE_REVIEW.md)
- [RML24 audit](../work/psk-repair-20260912-v1/rml24/README.md)
- [BPSK localization](../work/psk-repair-20260912-v1/bpsk-diagnosis/REPORT.md)
- [Drift qualification](../work/psk-repair-20260912-v1/synthetic/drift-suite/RESULTS.md)
- [Independent transmitter audit](../work/psk-repair-20260912-v1/review/interop-diagnosis/INTEROP_AUDIT.md)
- [Candidate04 RML24 audit](../work/psk-repair-20260912-v1/rml24-candidate04/README.md)
- [Paired cache timings](../work/psk-repair-20260912-v1/synthetic/perf-pair/RESULTS.md)
- [Test-runner correction](../work/psk-repair-20260912-v1/regression-runner-amendment.md)
- [Candidate07 transmitter/parity audit](../work/psk-repair-20260912-v1/candidate07-interop/audit.json)
- [Candidate07 real-capture audit](../work/psk-repair-20260912-v1/candidate07/real-capture-confirmation.json)
- [Candidate08 real-capture audit](../work/psk-repair-20260912-v1/candidate08/real-capture-confirmation.json)
- [Candidate08 source/build checkpoint](../work/psk-repair-20260912-v1/candidate08/BUILD.md)
- [Candidate08 full replay/preservation audit](../work/psk-repair-20260912-v1/candidate08-interop/RESULTS.md)
- [Fresh v4 confirmation](../work/psk-repair-20260912-v1/interop-confirmatory-v4-candidate08/RESULTS.md)
- [Full-cohort configuration follow-up](../work/psk-repair-20260912-v1/real-bpsk-plan-portfolio/results.json)
- [Combined-plan integration](../work/psk-repair-20260912-v1/candidate08-bpsk-bundle/results.json)
- [Matched-schedule independent audit](../work/psk-repair-20260912-v1/matched-schedule-control-v3/audit.json)
- [Five additional BPSK recordings](../work/psk-repair-20260912-v1/real-bpsk-five-candidate08/RESULTS.md)
- [Six-case QPSK causal diagnosis](../work/psk-repair-20260912-v1/v3-qpsk-diagnosis/RESULTS.md)
- [Nine-case independent reconciliation](../work/psk-repair-20260912-v1/real-bpsk-expansion-audit/reconciliation.json)
- [MYSAT loop-bank configuration ablation](../work/psk-repair-20260912-v1/mysat-diagnosis/loop-bank-results.json)
- [Signal-only selector study](../work/psk-repair-20260912-v1/rml24-selector-study/RESULTS.md)
- [Preserved failed pilot](psk-demodulator-pilot-20260911.md)

The staggered-loop rationale was checked against the
[JPL receiver report, equations 6–7](https://www.systems.caltech.edu/andre/journal/180D.pdf).
GNU Radio provides an independent transmitter and established synchronization
reference, not a runtime dependency of the production native Rust paths. The
explicitly hybrid K2SAT experiment does depend on its external FEC decoder; see
[Symbol Sync](https://wiki.gnuradio.org/index.php/Symbol_Sync).
The graphify search located older Python relationships, not this new Rust
integration, so source inspection and independent numerical tests determine
the implementation claims above.
