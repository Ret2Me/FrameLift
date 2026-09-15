# Validated Incremental Telemetry Recovery from SatNOGS Archives Using Complementary Demodulation Paths

Working manuscript and prospective analysis specification, version 1, 2026-09-08.
Not submitted, not externally preregistered, and not a completed evaluation.
Authors, affiliations, funding, and intended venue: to be supplied by the researchers.

## Evidence status and scope

The completed measurements below are development results. The independent
cohort is a target, not a claim that recordings have already been collected or
decoded. Its analysis protocol was locally frozen at 2026-09-08T19:24:51Z in
`reports/satnogs-holdout-protocol-20260908-v1.json`, SHA-256
`ddfe8c45f2c0adf86571eda3603c0796a4c7937db6902f1b076e35cc09607c28`.
The immutable selected-ID manifest remains a separate acquisition artifact.
Replace all `NOT YET MEASURED` fields only from committed, audited results.
Do not silently amend the algorithm or selection rule after seeing holdout yields.
An implementation change after exposure creates a new method version and makes
the exposed observations development data for that version.

This study concerns offline recovery of 9,600-symbol/s FSK/G3RUH AX.25 telemetry
from real archived, already FM-demodulated audio. It does not establish performance
on every satellite, modulation, CCSDS framing mode, or raw-IQ representation. A
CCSDS Space Packet inside an AX.25 payload is not a separately validated native
CCSDS radio-link decoder. No machine-learning model or CRC-guided bit repair is
part of the evaluated four-path receiver.

## Abstract — incomplete until independent evaluation

Public satellite observation archives can preserve waveforms from which additional
telemetry may be recoverable after the original reception. We investigate whether
complementary fixed-clock and continuously tracked demodulation paths increase
validated packet recovery from identical archived audio. The receiver combines two
signal-conditioning frontends with two timing-recovery methods and retains exact
received bytes, frame-check sequences, and waveform provenance. We distinguish
additional observation-level recoveries from content absent across a frozen
archive snapshot, and compare historical archive output separately from a
versioned same-audio reference decoder. In a development cohort of 93 CANVAS
observations, expanding the previous fixed bank increased observation-deduplicated
recoveries from 477 to 502; this is a 5.24% gain against our own previous receiver,
not a measured 5.24% advantage over the historical SatNOGS pipeline. Independent
cohort size, validated archive increment, reference-decoder differences,
uncertainty, false-acceptance results, and compute cost: **NOT YET MEASURED**.
The independent experiment will determine the supported claims; neither
revolutionary novelty nor publication readiness is assumed.

Keywords: satellite telemetry; archived radio observations; packet recovery;
software-defined radio; receiver diversity; reproducible evaluation.

## 1. Introduction and research questions

The scientific target is usable telemetry recovered from actual observations,
not attractive waterfall features or a larger count of unchecked candidates.
The same packet can be decoded in overlapping windows, by several timing
hypotheses, or at several stations. Those events provide different kinds of
evidence and must not be conflated with distinct information. SatNOGS explicitly
supports observations by multiple ground stations, making this distinction
material to archive analysis. [SatNOGS Network documentation](https://wiki.satnogs.org/Network)

Our candidate contribution is a reproducible, provenance-preserving study of
incremental archive recovery and its computational cost using complementary
receiver paths. Whether this contribution is sufficiently novel requires a
broader related-work review and completed measurements. The receiver is an
offline tool usable alongside an existing ground-station system, not a claim
that SatNOGS as a network has been replaced or comprehensively outperformed.

The prespecified research questions are:

1. How much independently checked packet content is absent from the frozen
   comparison archive but recoverable from its selected audio recordings?
2. On identical samples, what packet sets are recovered or missed relative to
   a versioned reference decoder and to the previous receiver?
3. Which frontend/timing combinations contribute complementary valid packets,
   and what is the recovery-versus-compute tradeoff?
4. Are the apparent improvements stable across dates, passes, and stations,
   and what false acceptance is observed under independent negative controls?

## 2. Related work and attribution

### 2.1 Existing satellite decoding pipelines

gr-satellites already separates demodulators, deframers, transports, and sinks.
Its demodulators perform filtering and synchronization; its AX.25 deframer
includes NRZI decoding, bit unstuffing, CRC checking, and optional G3RUH
descrambling. These are existing capabilities, not inventions of this work.
The current documentation is background; the actual reference executable and
configuration must be independently version-pinned for the experiment.
[gr-satellites components](https://gr-satellites.readthedocs.io/en/latest/components.html)

### 2.2 Established synchronization and conditioning

GNU Radio contains a Gardner timing-error detector and a long-form DC blocker.
The alternative Rust path uses these established ideas with separately bounded
state, arithmetic, initialization, and linear interpolation; it is not a
bit-exact reproduction of GNU Radio's full symbol-synchronization chain. A Rust
port or an extra timing loop alone is not evidence of new synchronization theory.
[GNU Radio timing-error detectors](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/timing_error_detector.cc),
[GNU Radio DC blocker](https://github.com/gnuradio/gnuradio/blob/main/gr-filter/lib/dc_blocker_ff_impl.cc)

### 2.3 Multiple-demodulator precedent

Dire Wolf is an existing software AX.25 modem with documented 9,600-bps
GMSK/G3RUH support. Its official change history already records multiple decoders
per channel to tolerate frequency offsets. Consequently, receiver portfolios
and trying several demodulation hypotheses cannot be claimed as wholly new.
An offline, version-pinned Dire Wolf comparison, with repair disabled, would
strengthen the practical evaluation; if it is omitted, disclose the omission
and do not claim superiority over all available modems.
[Dire Wolf project](https://github.com/wb2osz/direwolf),
[Dire Wolf revision history](https://github.com/wb2osz/direwolf/blob/master/CHANGES.md)

This short section establishes concrete prior-art constraints, not an exhaustive
literature review. Before submission, review research on receiver diversity,
multi-hypothesis synchronization, burst acquisition, and archival SDR recovery;
identify the closest published systems and state exactly what remains new.

## 3. Receiver methods

### 3.1 Input and shared decoding boundary

Archived mono OGG is decompressed using the frozen FFmpeg/ffprobe environment.
The reference and experimental decoders receive the identical decoded PCM
waveform, with recorded sample rate, sample count, and SHA-256. Post-FM real audio
is not RF IQ and is not passed through another FM discriminator. The native
runtime is Rust; external codec and reference-decoder dependencies are explicitly
identified rather than described as a fully pure-Rust stack.

The qualified audio path uses six-second windows with a three-second stride and
retains complete window coverage. Failed windows are failures, not valid empty
observations. DSP does not receive archive payloads or expected packet timestamps.
The downstream auditor alone reads those references. Exact framing and integrity
rules are frozen along with the executable and configuration.

### 3.2 Prespecified 2 × 2 receiver portfolio

| Variant | Conditioning | Timing |
|---|---|---|
| LF | Legacy FIR/DC path | Full bank of 160 fixed phase/rate hypotheses |
| NF | Square-pulse, long-form DC, RMS AGC path | Same full fixed bank |
| LG | Legacy FIR/DC path | Bounded Gardner tracking bank |
| NG | Square-pulse, long-form DC, RMS AGC path | Bounded Gardner tracking bank |

The alternative frontend applies square-pulse conditioning, a four-boxcar
long-form DC rejection scale of 32 symbols, and causal RMS AGC with a 50-symbol
time constant. The timing bank contains eight initial phases at each of two
normalized bandwidth settings, 0.02 and 0.06. The second-order non-data-aided loop
uses linear interpolation and an average-rate bound of ±4,000 ppm.

For each observation, the tested portfolio output is the bytewise deduplicated
union LF ∪ NF ∪ LG ∪ NG. Each constituent set is retained for ablation. A union
does not make its individual paths statistically independent confirmations.
It retains LF by construction only if the LF path and acceptance policy are
actually unchanged; this property is checked using exact payload sets.

The frontend comparison changes a bundle of choices, including sample rate,
startup, normalization, and delay. It must not be interpreted as identifying
the causal effect of AGC or one filter parameter in isolation. The implementation
does not include bit repair, IQ MLSE, or an AI transmission detector.

## 4. Dataset and frozen independent evaluation

### 4.1 Development exposure

The original fixed-bank comparison used 100 selected CANVAS observations,
of which 93 completed, six lacked OGG, and one was rejected by a duration limit.
Despite the legacy directory name containing `week`, the selected observations
cover one UTC day, 2026-09-06. These observations and every other previously
inspected receiver recording are excluded from confirmatory selection.
The four observations used to choose the hybrid paths were deliberately selected
problem cases and are not a random subset or an independent test.

### 4.2 New cohort target and freeze requirements

The first confirmation cohort targets CANVAS, NORAD 68635, transmitter UUID
`GCmN6RULea8dAT7Qoat8z2`, catalogued GMSK at 9,600 symbols/s and decoded through
the FSK/AX.25-G3RUH audio profile. Its fixed date interval is
2026-08-23T00:00:00Z inclusive through 2026-09-06T00:00:00Z exclusive.
After complete metadata pagination and development-ID exclusions, divide each
UTC date into twelve two-hour observation-start strata. Select at most two rows
per stratum with the lowest SHA-256 of `satnogs-holdout-v1:` followed by the
decimal observation ID, breaking ties by numeric ID. This yields at most 336
selected observations across 14 calendar dates. These numbers define a
resource-bounded target, not a publication threshold or a power calculation.
The actual available dates, full exclusion ledger, query responses, and selected
IDs must be written into an immutable metadata-only manifest before selected
audio is decoded or evaluated.

The final manifest is authoritative. Until it exists, these fields remain:

| Required frozen field | Status |
|---|---|
| Date interval | 2026-08-23 inclusive through 2026-09-06 exclusive |
| Available-date census | PENDING ACQUISITION MANIFEST |
| Eligible observation universe and archived query responses | NOT YET FROZEN |
| Deterministic selection | Two lowest prefixed-ID SHA-256 values per UTC two-hour stratum |
| Complete development-exposure ID/hash exclusion list | NOT YET FROZEN |
| Included IDs, transmitter identities, station IDs, start/end times | NOT YET FROZEN |
| Input limits | Mono 48-kHz float32 PCM; OGG ≤64 MiB; audio ≤1,800 s |
| Missing-data rule | Include missing-OGG selections in attrition; no replacement |
| Treatment executable and settings | Frozen September 8 hybrid identity below; two workers × two threads |
| Stopping rule | Attempt entire frozen selection; no yield-triggered stopping/expansion |
| Archive comparison scope | Listed packet objects for selected observations only |
| Primary endpoint and control exposure | Frozen protocol; details in Section 5 |
| Runner/analysis implementation identity | To freeze before first holdout decode |

Selection must not depend on waterfall appearance, archived packet count,
human success labels, or our decode yield. Include observations with and without
historical frames under the same selection rule. Metadata availability and
download failures can induce selection bias and must be reported. Do not replace
an observation because it is unsuccessful. The frozen protocol does not replace
missing-audio selections. Metadata can expose historical outcomes, but selection
must not use those fields to rank, tune, or terminate the cohort.

All receptions on a UTC date are retained together in the primary statistical
resampling unit, conservatively preserving dependence within a day. No claim is
made that distinct stations or overlapping observations are independent passes.
Any later pass-proxy grouping is exploratory and must use timestamps and mission
identity without reference to decode yield. A timestamp group is not proof of an
orbital pass. Content deduplication and two-day block sensitivity provide
additional protection against overstated evidence from repeated transmissions.

Single-mission confirmation supports a claim about that mission/profile and
sampled archive, not every protocol. Wider claims require a separately frozen
extension to additional missions and protocols without reusing it for tuning.

### 4.3 Comparison arms

Keep the following references separate:

- **Historical archive:** packet contents actually available in the frozen
  comparison snapshot. Its station pipeline and input representation may differ
  from replay, and it may be incomplete or subsequently updated.
- **Same-audio reference:** version-pinned gr-satellites FSK9600/AX.25-G3RUH
  replay using version 5.9.0 on the same whole float32 mono 48-kHz PCM. This is a component-level
  reference, not a verified reconstruction of each historical station pipeline.
- **Previous receiver and ablations:** unchanged development-qualified decoder,
  LF, NF, LG, NG, and their prespecified union.
- **Additional modem control:** offline Dire Wolf if configured and frozen before
  comparison; otherwise record it as not evaluated.

No arm transmits packets or submits recovered data to a service. Runtime
comparisons record host, sample representation, measured boundaries, and resource
allocation. The frozen study measures full-cost yield and owned-child CPU/wall/RSS;
it does not establish equal-compute superiority. A matched compute-budget curve
remains an additional publication-strengthening experiment. Extra searches that
cost more are a valid recovery tradeoff, but not automatically superior compute
efficiency. The primary reference uses frozen defaults, not holdout tuning.

Qualification of the optional installed Dire Wolf 1.7 `atest` baseline is separate:
its WAV reader requires integer PCM, whereas the primary experiment uses float32
PCM. A quantized-input comparison must be labelled accordingly, or all arms must
be rerun on the same explicitly identified quantized waveform. It cannot silently
inherit the primary same-sample claim.

## 5. Endpoints, integrity, and statistical analysis

### 5.1 Exact packet-set endpoints

Let U_i, B_i, and A_i denote accepted byte-identical PDU sets for portfolio,
same-audio reference, and historical archive at observation i. The canonical PDU
representation excludes transport wrappers and received FCS but retains the
complete link-layer PDU; normalization must not erase meaningful payload bytes.
Original received FCS and normalization provenance remain in the evidence record.

Report additions |U_i \ B_i| and losses |B_i \ U_i| separately, not merely their
net difference. The primary inferential endpoint is the paired difference
|U_i| − |B_i| per completed same-input observation, with strict AX.25 UI acceptance;
report mean difference and the ratio of summed counts when its denominator is
nonzero. Decoder-accepted non-UI reference candidates are retained separately.
The secondary finite-cohort archive endpoint is
|U \ A_snapshot| and its total PDU bytes, where U is the union of all U_i and
A_snapshot contains every packet in the explicitly frozen comparison scope.
Also report |U \ (A_snapshot ∪ B)|, and separately exclude available development
archive/native/reference unions. The former distinguishes recovery absent
from the archive from recovery also available through an existing replay modem.

Archive absence means absence from that snapshot, not proof that no station
worldwide ever received the packet. If comparison files are missing or scope is
limited to selected observations, label the endpoint accordingly; do not call
it absence from the entire SatNOGS database. A SHA-256 identifies evidence but
cannot establish completeness of the underlying archive query.

Secondary endpoints include total per-observation unique PDUs, globally unique
PDUs, fraction of observations improved, zero-archive-frame observations with
validated recovery, recovered bytes, baseline omissions, and exclusive variant
contributions. Full PDU byte counts are not automatically spacecraft application
data byte counts; any application-payload count requires separate parsing.

### 5.2 Validation and false acceptance

For every archive-incremental PDU, preserve input hash, observation identity,
window/time origin, receiver variant, received bytes/FCS, independently recomputed
FCS, and protocol-structure checks. A clean replay verifies reproducibility,
not independent radio reception. Address fields, plausible telemetry, or CRC
alone do not authenticate the transmitter. Record independent-station or
mission-side corroboration when available, without inventing it when absent.

The frozen supplemental controls are 32 deterministic white-noise and 32
AR(1)-colored-noise recordings, each 60 seconds, mono 48-kHz float WAV, seed
20260908, AR coefficient 0.85. Apply the complete four-path search with repair
disabled. Report accepted false PDUs and exposure separately by control family.
These 64 minutes constitute synthetic pipeline smoke testing, not a calibrated
false-positive rate for operational stations. Real observations with zero archive
frames are not known-negative recordings.

Independent known-packet truth under controlled degradation and genuine
no-transmission recordings remain additional validation work. Any added controls
need explicit selection/exposure rules and a separately identified analysis.
Repeated silence and reused random seeds are not independent exposure. Audio
transformations that preserve real packets are not automatically valid
no-transmission controls.

### 5.3 Uncertainty and resource accounting

The observed finite-corpus unique-content counts are exact set counts, not a
sample of independent frame trials. For the primary paired comparison, resample
UTC dates with replacement, preserving all completed rows within each sampled
date, using 10,000 replicates and seed 20260908. Report percentile 95% intervals
for mean paired difference and aggregate yield ratio; handle zero-denominator
replicates explicitly. Repeat with adjacent two-day calendar blocks as the
prespecified sensitivity analysis. With at most 14 dates, acknowledge limited
time coverage, dependence across day boundaries, and unstable few-cluster intervals.

Global unique counts are descriptive finite-corpus results, not binomial estimates
or bootstrap general-population claims in this protocol. Any exploratory
resampling of globally deduplicated metrics must rebuild unions rather than sum
precomputed per-observation unique counts. Do not treat many
CRC checks, windows, or observations of the same burst as independent trials.
Report the number of dates, calendar blocks, stations, and unique inputs alongside
every inferential result. Confidence intervals and a positive effect are not
proof of novelty, universal benefit, or production readiness.

Report wall time, process CPU time, peak resident memory, decoding-time/audio-time
ratio, and incremental validated bytes per CPU-hour. Separate codec acquisition,
DSP, and audit time. Summed parallel stage elapsed times are not process CPU
time. Matched timing trials use an isolated or explicitly characterized host,
fixed thread counts, repeat order, and a frozen measurement boundary.

## 6. Results

### 6.1 Completed development measurements — not confirmation

The full fixed bank retained every previously decoded PDU on the 93 completed
development recordings. Per-observation unique recoveries increased from 477 to
502, with 25 additions and no losses; 18/93 recordings improved. The global
receiver union increased from 313 to 327. Against the entire frozen development
archive snapshot, 29 unique PDUs totaling 7,656 bytes were absent, and 25 of those
were also absent from the baseline union. Six PDUs totaling 1,584 bytes were new
relative to the previous receiver and require follow-up provenance/secondary
replay treatment. Three baseline observation/PDU pairs remained missing with LF.
[Development report](../reports/receiver-followup-20260908.md)

| Selected development observation | LF | NF | LG | NG | Union |
|---|---:|---:|---:|---:|---:|
| 14936424 | 18 | 9 | 16 | 3 | 19 |
| 14936415 | 16 | 10 | 14 | 10 | 17 |
| 14936407 | 16 | 9 | 15 | 6 | 16 |
| 14936444 | 24 | 15 | 24 | 12 | 26 |

On these four deliberately selected cases the union recovered every reference
PDU, including the three remaining LF omissions. Its four additional
observation/PDU recoveries versus LF were all already present in the frozen
global archive. They must not be added to the 29 archive-absent contents.
Standalone alternative branches often recovered fewer PDUs than LF. These
results motivate complementarity testing, not unconditional replacement of LF.

### 6.2 Independent cohort — no measurements yet

| Endpoint | Independent result |
|---|---|
| Selected / downloaded / completed / failed observations | NOT YET MEASURED |
| Dates / calendar blocks / stations / audio hours | NOT YET MEASURED |
| Per-observation portfolio / reference PDUs | NOT YET MEASURED |
| Global portfolio / reference PDUs | NOT YET MEASURED |
| Archive-incremental validated PDUs / bytes | NOT YET MEASURED |
| Archive-and-reference-absent validated PDUs / bytes | NOT YET MEASURED |
| Reference additions / losses and cluster-aware interval | NOT YET MEASURED |
| Zero-archive-frame observations with validated recovery | NOT YET MEASURED |
| Exclusive LF / NF / LG / NG contributions | NOT YET MEASURED |
| False accepted PDUs and independently known control exposure | NOT YET MEASURED |
| Wall / CPU / peak RSS | NOT YET MEASURED |
| Matched compute-budget curves | ADDITIONAL EXPERIMENT NOT YET PERFORMED |

Required figures: selection/attrition flow; packet-set overlap and archive
increment; date-level paired differences; measured yield and computation cost.
Matched yield-versus-compute curves require their additional qualified experiment.
Generate tables and figures from audited machine-readable artifacts, including
failures and zero-gain cases, rather than manually selecting favorable examples.

## 7. Discussion — interpretation rules

Complete this section after the frozen test. A positive finding should state
its actual scope: for example, validated incremental packet content from a
specified mission/profile and date cohort at a reported computational cost.
Discuss missions or reception conditions not tested, archive incompleteness,
correlated receptions, loss of information in stored audio, and lack of direct
transmitter authentication. A historical archive comparison is operationally
useful but cannot isolate receiver algorithm quality because its original
processing environment is not controlled.

A zero or negative holdout effect remains a reportable outcome and must not be
hidden by repeated cohort extension or retuning. Any follow-on algorithm changes
require a new independent test. The word *revolutionary* is not an experimental
endpoint; claims should be proportional to validated gains and established
distinction from the closest prior work.

## 8. Reproducibility and availability — to complete before release

Retain frozen source, Cargo.lock/toolchain, native executable hashes, external
dependency versions, exact commands, resource configuration, metadata responses,
input recordings or a durable reproducible acquisition route, and complete
frame/provenance ledgers. Preserve normalized and original archived data together.
Include all exclusions, download failures, interrupted attempts, negative controls,
and the script or Rust command that regenerates each reported number.

Release repository, dataset accession/DOI, archive snapshot scope, and independent
clean-environment reproduction: **NOT YET AVAILABLE**. Data and software sources
must be attributed accurately. No external submission, publication, telemetry
upload, or third-party contact is implied by creation of this working draft.

## Internal evidence map

- Receiver implementation and boundaries: `docs/rust-receiver.md`.
- Completed September 8 DSP experiments: `reports/receiver-followup-20260908.md`.
- Machine-readable development comparison:
  `reports/receiver-followup-20260908-evidence.json`.
- Frozen development hybrid executable SHA-256:
  `455a9622ba7c839fbba1c575b8139502a9b24c2b3f99421adb2e9ab74bb37ab3`.
- Frozen independent analysis protocol:
  `reports/satnogs-holdout-protocol-20260908-v1.json`.
- Independent selected-ID manifest and result ledger: **PENDING**.

Graphify navigation trace: `receiver archive publication holdout audit integrity
evaluation`. The September 4 graph returned older receiver/planning navigation
nodes and cannot substantiate the current receiver results. Planning-model
publication-readiness outputs are excluded from this manuscript's evidence.
