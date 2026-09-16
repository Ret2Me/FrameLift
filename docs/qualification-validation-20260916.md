# September qualification: engineering checks and run status

This record concerns the September 16 native archival workflow and opt-in
`marginal-yield` scheduler. It is not a new field-yield result and does not
replace the frozen 266-recording paper evidence.

## Completed engineering checks

- `cargo xtask check` passed: shipping-source formatting, strict CPU lint,
  library/binary/integration tests, independent receiver-auditor tests, strict
  documentation and CUDA host checks. GPU execution tests remain explicitly
  ignored; no GPU hardware qualification is claimed.
- The final source tree then passed the full quality gate again: 501 CPU library
  tests, 21 receiver CLI tests, five archival CLI tests, nine research CLI tests,
  the binary/tool suites, 34 independent-auditor tests and 10 CUDA host checks.
  Six optional library checks, two optional auditor checks and two GPU-runtime
  checks remained explicitly ignored for their documented prerequisites.
- After the final cohort/release and WAV preparation hardening, the targeted
  library filter passed 25 tests, the archival CLI passed all five tests, and
  `cargo xtask lint` passed again. The receiver/scheduler code did not change
  after the full quality pass.
- `cargo xtask research-check` compiled the retained research targets.
  Historical example warnings remain visible; those frozen sources were not
  reformatted or silently modernized.
- Positive-fixture regression compares exact received-FCS frame sets and task
  details between fixed and adaptive complete runs. Resume tests cover a partial
  last batch, policy mismatch and modified scheduling journals. These are
  engineering controls, not evidence of improved field reception.
- Archival tests cover outcome-independent ranking, complete pagination,
  whole-pass exposure exclusion, declared sampling amendments, strict external
  decoder output parsing, missing/failed observations, paired gains and losses,
  and station/pass-component uncertainty calculations.

## Real-recording engineering regression

The first 90 seconds of previously exposed CANVAS observation 14936372 were
used only as a development control. Both complete schedulers recovered exactly
the same **nine** received-FCS-valid frames and completed all **203** tasks.
Their task details also match after removing only session identity and nested
`elapsed_seconds` fields. The SHA-256 of the normalized, stage/window-sorted,
compact sorted-key JSON task array (including its final newline) is
`d8eb1d89c0beb7bb0097e38601507e8c6ef794ccc252d5690b151da20272faa0`
for both runs.

The single observed full-run wall times were 15.65 seconds (fixed) and 18.20
seconds (adaptive). This does **not** demonstrate a speedup; adaptive batch
barriers have overhead. The new hypothesis concerns limited-budget yield, not
universal lower completion time. This exposed control is not part of the new
cohort or a new publication effect estimate.

The external-decoder smoke test caught an integration defect: `atest` rejected
FFmpeg's default WAV `LIST` metadata chunk before decoding. Preparation now
uses `-map_metadata -1 -fflags +bitexact` while keeping PCM16 and the original
sample geometry. A repeated `atest` smoke run then succeeded. The old and
metadata-free WAVs both have raw-PCM SHA-256
`774601c91efd8351f84660375e824482f05c0e9f86d9c2f1f52536cfb9e61eed`.
The container-only change therefore does not alter receiver input samples.
The pre-fix development executables and receipts are retained separately from
the final campaign registration.

The corrected optimized build was also tested on the metadata-free WAV:
again **9 identical frames, 203 tasks each**, and the same normalized task-array
hash. Its single-run wall times were 15.22 seconds fixed and 18.42 seconds
adaptive. An existing development noise fixture completed all seven tasks with
zero accepted frames. One short noise control is not a false-acceptance bound.

Candidate frozen executable identities:

| Executable | SHA-256 |
|---|---|
| `telemetry-yield-rs` | `85656c5eb0c340634b6f87c220ff39918029a3ebdb0ad5d23f7a39c380ce1b18` |
| `framelift-campaign` | `a8b6d9befa5f6fa94450a030ba8d545c421ee5acd8331c8d885d28b6425b07b1` |

## Field experiment preparation

The native inventory examined 905,006 local file names and bounded metadata
content, identifying 11,474 public observation IDs and 954 mission-days to
exclude. There were no read issues. This inventory is local and conservative;
it does not prove absence of renamed recordings or external exposures.
`complete_inventory_attested` therefore remains false.

The protocol covers July 8–22, 2026, with four documented FSK9600 mission
profiles. Target size is 96 recordings, with matched fixed/adaptive runs at
3, 10 and 60 seconds. This is a multi-mission qualification increment, not a
claim of population representativeness or a sufficiently powered final study.

Metadata acquisition encountered a public-API HTTP 429 at 13:19:19 UTC with
`Retry-After: 2598`. The acquisition process respected that interval. No partial
catalogue was substituted. The sampling-cap adjustment is documented separately
in [the pre-outcome amendment](archive-sampling-amendment-20260916.md).

Metadata acquisition completed with **2,352 eligible recordings** and no
waveform downloads. The original cap failed explicitly at 88/96 recordings;
the declared 40-per-mission amendment passed. The frozen selection contains
**96 recordings, 52 stations and 96 connected pass groups**, totaling 38,419
scheduled recording seconds (10.67 hours). The largest station contributes 11
recordings. Mission counts are CANVAS 40, SONATE-2 40, NORBI 9 and UNISAT-7 7.
No selected record matched the local exposure exclusions; this does not remove
the inventory's global-nonexposure limitation.

The [metadata-only selection reference](../config/archive-qualification-20260916-selection.json)
records every selected observation ID and the frozen artifact identities:

- Cohort: `fad5fc9d5158cbac60b146bbd834c1ffc295df0ef83307d717e7a51365407fdc`.
- Amended catalogue: `64611aae553b12ce63c662d27c82fb214b009517b5ae60c7ea80659a51ef8adf`.
- Runtime registration: `982e8e31f472a5e8f7065af346be2e3fdf96accb94c07bf3a0904d773cc719cf`.

The registration binds the optimized receiver/campaign, reference executables
and 17 supplied runtime/profile files. It is not a hermetic OS image or an
externally timestamped preregistration. Audio acquisition started only after
these artifacts were fixed. Completed field results are still pending; there
is no new measured adaptive-versus-fixed improvement to quote.

## Preserved publication artifacts

The existing paper and its checksum manifest remain unchanged:

| Artifact | SHA-256 |
|---|---|
| `publication/decoder-paper-v2/main.pdf` | `a441168fa1e88734c7e4e1f3125a2821b6775309803247dddd8434057fda25fe` |
| `publication/decoder-paper-v2/evidence/checksums.sha256` | `576108cca982a4f08c5ccefcc10e8a6958ea60789579a07c50192ce9aeccfcae` |

The remaining repository-wide Python migration, hermetic baseline packaging,
global exposure audit, negative-control false-acceptance study and broader
modulation/IQ field qualification are separate unfinished gates.
