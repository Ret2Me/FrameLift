# Data card: SatNOGS observation-planning cohort v3

## Intended use

This cohort evaluates calibrated probability estimates and receiver-conflict
scheduling for satellite observations. It is not a decoder benchmark, an orbit
truth dataset, a complete record of attempted transmissions, or evidence that a
particular station's historical antenna matches its current public metadata.

## Source and license

Rows are read from the public SatNOGS Network observations API using GET-only,
bounded requests. Raw paginated responses are retained byte-for-byte with URL,
HTTP date when available, retrieval time, byte length and SHA-256. The snapshot
manifest records the SatNOGS data license as CC BY-SA 4.0 and links the license.
Any redistributed normalized dataset and adaptations must retain attribution
and share-alike obligations. Human release review must confirm attribution and
source permission before external publication.

Recommended attribution for the derived snapshot: “SatNOGS Network observation
data and its contributing ground-station operators, accessed through
network.satnogs.org; normalized features and labels modified by the
telemetry-yield study; CC BY-SA 4.0.” The release should link both the source
manifest and license and state that Libre Space Foundation does not endorse the
study.

The API's anonymous throttle is respected. Cursor pages are fetched
sequentially; the acquisition checkpoint permits resumption without repeating
saved pages.

The public API does not expose a database transaction or snapshot token.
“Frozen” therefore means a closed historical time range plus immutable page
bytes/hashes, not an atomic database snapshot: late waterfall vetting,
demoddata upload or backfill during the multi-hour cursor traversal could make
different pages reflect slightly different source states. Page retrieval times
are retained so this limitation is auditable; conflicting duplicate IDs fail,
but an API-level omission cannot be proven absent.

Raw public API pages can contain operator usernames, client metadata and media
URLs. They are retained locally only to make the source snapshot auditable;
the normalized publication table deliberately omits usernames, free-text
descriptions, payload/media URLs and the raw client-metadata object. V4 keeps
only an allowlist of derived capture coordinates and receiver-configuration
fields; paths, device serials and arbitrary keys are discarded. Any decision to
redistribute raw pages requires a separate privacy and attribution review.

The frozen technical run records the placeholder User-Agent contact
`research@example.invalid` because no project contact channel was supplied. It
is intentionally visible rather than replaced with an invented identity. A
reachable project URL or email is a human release/courtesy gate for subsequent
API runs, but does not alter the hashes of this already acquired public data.
The automated v4 workflow now enforces that boundary: it may finish the frozen
historical SatNOGS snapshot, but post-acquisition covariate collection and
prospective planning cannot start until a non-placeholder operational contact
artifact is supplied and content-bound into campaign registration.

## Frozen population

The evaluation interval is the half-open observation-start range
`[2026-03-01T00:00:00Z, 2026-09-01T00:00:00Z)`. Ten NORAD targets were selected
before full cohort download using only active packet-transmitter metadata and
aggregate transmitter good/bad counts. Exact IDs, transmitter UUIDs, counts,
random seed and thresholds are in
`configs/observation-planning-publication-v3.json`.

The primary interval contains all transmitters for all ten targets. The
predeclared v1 and v2 count gates failed at 217 and 237 conditional-decode rows
without model fitting. The v3 successor retains those cohorts, adds the
preceding six unfiltered months for 60535, and adds the preceding six months
for the frozen selected transmitters of 40071 and 56933 restricted to
`with-signal`. That stratum is sufficient for both conditional-decode classes;
`without-signal` cannot be a valid negative for a task explicitly conditioned
on visible signal. Parent hashes and count-only selection reasons are embedded
in the successor configs.

The aggregate counts were a coverage screen captured at target-selection time;
they can include observations inside the later evaluation interval. They were
not used as row features or to rank models, but selecting well-observed targets
still makes this a coverage-enriched cohort rather than a random satellite
sample. Reported performance must therefore not be generalized to sparsely
observed satellites without a successor-cohort test.

The endpoint is queried in calendar-month chunks and followed through every
cursor page. A pass crossing a month boundary belongs to the month containing
its start, avoiding gaps and duplicates. Conflicting duplicates fail the build;
byte-compatible duplicates are counted and collapsed.

The recorded selection transmitter UUID establishes satellite eligibility and
is not a row filter in the primary interval. The two v3 supplemental windows
are explicitly transmitter-filtered as disclosed above. Each row retains its
actual transmitter UUID and mode, and the conditional-decode task independently
excludes non-packet modes. This prevents selected-transmitter metadata from
being silently assigned to observations of other radios.

## Labels

`signal_present=1` only for `waterfall_status=with-signal` and `0` only for
`without-signal`. Unknown waterfall labels are excluded rather than treated as
negative.

`decode_success_given_signal` is restricted to transmitter modes other than CW,
FM, AM, USB or LSB. A non-empty demodulated-data list is class 1 because the
artifact itself establishes detectable signal, even if the waterfall was not
manually vetted. Class 0 requires both `with-signal` and an explicit empty
list. Unknown waterfall plus an empty/missing list remains unknown; a
`without-signal` label combined with a non-empty artifact is excluded as
contradictory before mode-specific decode eligibility is evaluated. This
endpoint measures the presence of an uploaded demodulated
artifact, not strict frame validity or payload correctness. A missing or
non-list `demoddata` value on a `with-signal` packet row is a counted
malformed-row exclusion, never a decode failure. `status=good` is never used as an independent label
because Network can derive it from waterfall vetting or an upload.

## Features and leakage controls

The builder rejects a mismatched, malformed or post-observation embedded TLE
and never replaces it with a current CelesTrak TLE. Network source inspection
showed that the observations serializer returns the station's current
coordinates at retrieval, not the coordinate columns cached on the observation.
V4 additionally established that many public rows carry capture-time latitude,
longitude and elevation inside `client_metadata`.
The probability models therefore use observation-cached schedule elevation and
rise/set azimuth, duration and embedded-TLE age. Capture-time client coordinates
are preferred for diagnostic SGP4 range/Doppler and are the only coordinates
eligible for the historical terrestrial-weather join. Current-coordinate
fallbacks are explicitly flagged; all coordinate-derived quantities remain
excluded from probability features. A moved station can still limit rows
without captured coordinates and is stated as such.

Transmitter frequency, mode, baud and status come from the observation record.
Trailing satellite, station, transmitter and satellite-station outcomes are
constructed separately in every evaluation fold using eligible training
observations whose end is strictly earlier than the prediction start. Held-out
outcomes are not fed back, and observations crossing a split boundary are
excluded. Numeric imputation and standardization use the training fold only;
unseen categorical levels map to an all-zero vector.
Temporal and forward-validation cuts keep identical start timestamps atomic;
the independent audit reconstructs the holdout suffix, counts and time bounds.

## Known missing dimensions

The observations endpoint does not expose its model's cached historical station
coordinates or a versioned physical-antenna profile. V4 recovers capture
coordinates and receiver settings only where public client metadata carries
them. Receiver RF gain and receiver port are not physical antenna gain/type.
Versioned physical antenna gain, receiver noise temperature, local atmospheric
attenuation and ionospheric scintillation therefore remain absent unless a
time-stamped local declaration with evidence is supplied. The production
planner accepts optional providers and widens uncertainty when inputs are
missing; the prospective campaign freezes current public antenna type/frequency
ranges before each decision.

## Selection effects and non-claims

SatNOGS jobs are scheduled rather than randomly sampled. Stations, satellites,
operators, transmitter declarations and human waterfall vetting are uneven.
Demoddata uploads depend on client and decoder availability. The cohort can
therefore support a scheduling result within observed Network behavior, not a
causal RF propagation claim or universal station-transfer claim.

The embedded newer TLE is a planning-state reference, not ephemeris truth. TLE
epoch is only a proxy for availability because exact Network ingestion time is
not exposed. The scheduling replay treats one SatNOGS observation as one sample;
it cannot infer unique payload records or science value from baud and duration.

## Promotion gates

Publication promotion requires at least 1,000 known signal labels across five
satellites and five stations, plus 300 conditional-decode labels with both
classes across at least five satellites and five stations. Probability
usefulness requires at least 5% relative Brier reduction
over `global_rate` and a paired cluster-bootstrap 95% interval whose upper bound
is below zero. A negative result is retained and reported; targets, dates,
models and thresholds are not changed after inspecting performance.
