# Prospective API and acquisition integration — 11 September 2026

The fixed study window remains **12 September 00:00 through 19 September 00:00 UTC (exclusive)**, CANVAS/GMSK9600, at most 500 observations, fixed station–UTC-day ranks and no decoding-outcome selection. Receiver DSP and paired-analysis methods did not change.

## API correction, not a new sampling rule

The first local registration used `satellite__norad_cat_id` and `start__gte`. The current official `ObservationViewFilter` source defines `norad_cat_id` and `start` (with a greater-than-or-equal lookup), plus `start__lt`. The new registration corrects those wire names. A live read-only OPTIONS request succeeded but did not enumerate filters; it does not certify the deployed source version. Every acquired row is locally checked against the time bounds and eligibility, and cursor/filter changes fail. [Official filter source](https://gitlab.com/librespacefoundation/satnogs/satnogs-network/-/raw/master/network/api/filters.py), [official API documentation](https://docs.satnogs.org/projects/satnogs-network/en/latest/api.html).

The old registration, amendment, receiver freeze and binaries remain preserved. [Registration v3](/home/ubuntu/telemetry-yield/work/innovation-prospective-20260912-v2/analysis-registration-v3.json) binds the corrected protocol, unchanged statistical procedure, new receiver/runner freeze and exact acquisition tools. These are local records, not external timestamped preregistration.

## Implemented and actually checked

- Runner v5: the diff against archived v3 changes only API field names, registration paths and the matching fixture. 34 ordinary tests passed; one separate existing-result control passed. An actual closed-lineage preflight failed with only manifest/profile/lock created, no observation jobs or waveform reads.
- Metadata acquirer v3: 13 tests passed. Tests cover strict query keys/cursors, EOF headers, changed artifacts/failed HTTP, fixed ranking, UTC, duplicate IDs and date/eligibility boundaries. Positive HTTP evidence in these tests is an explicit offline fixture, not a real server response. The actual CLI refused execution before the window closes, before creating its output directory; zero metadata GETs and waveform fetches occurred.
- Waveform launcher v8: 22 tests passed. It supports the exact historical and new prospective plans, inherits the hash-bound selection plan into an ephemeral transport view without modifying admitted cohort bytes, and always requires runner admission before fetching. The actual closed 266-row proposal was rejected before output creation/fetching. Previous v6 fixed selection lookup but still lacked that transport-only inherited field; v7/v8 preserve and fix that additional integration gap.
- Paired yield analyzer v6: 18 tests passed and old20 reaggregation retained 139/86/44, 53 added pairs, zero lost, and 41 comparator-absent global PDUs within those complete cases.
- Cost analyzer v1: the main task independently reviewed its source, reran all 12 tests, and regenerated an exactly byte-identical old20 cost report (SHA256 `767308f347707f8d978c47c1773dd2d07e0ca325be0cb0fb975760a27ed81d38`).

The main task reviewed the final integration changes. Earlier independent-agent reviews apply to the explicitly recorded earlier snapshots; they are not represented as independent approval of all new acquisition code. Shared helper test counts overlap and must not be summed as independent evidence.

## Bounds and retained failure semantics

Metadata: at most 300 pages, 4 MiB charged transfer per page including retries, 64 MiB aggregate charged body transfer, complete immutable page/HTTP/header/body inventory and an explicit EOF. Reaching any cap is failure, never EOF or permission to select a prefix. Replay requires the original completed snapshot and verifies page continuity, identities and aggregate row equality without network access. A zero-eligible snapshot is a shortfall, not successful telemetry recovery. The completion field `network_fetches` counts page-fetch routine calls, not individual HTTP retry attempts; individual attempts are in HTTP receipts.

Audio: at most four concurrent requests, 64 MiB charged per selected file, 12 GiB total and 25 GiB disk reserve; original Ogg/Vorbis/48 kHz/mono/1–1800 s contract. Missing or failed downloads stay in the selected denominator. They do not become zero-frame decoder results and are not replaced by convenient observations.

## What remains before real execution

The future week has not happened. Neither a real complete future metadata snapshot nor prospective decoded results exist. The live full chain cannot yet be qualified on that dataset; offline tests are not a substitute. After the window closes, acquire/replay the fixed snapshot, independently check true EOF and ranking, close the then-current exposure audit, and produce a genuine reviewed release. **Do not create a passing release merely to bypass the gate.** Historical lineage still has unresolved artifacts; that is a separate unreleased cohort.

No scheduler or automatic 19 September job has been created. No new receiver DSP is running. No manuscript has been submitted or software deployed to the station.

## Existing-host invocation, not portable packaging

Run only after the window closes, with a new output path:

```sh
rtk proxy /home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/innovation_prospective_metadata_v3 --protocol /home/ubuntu/telemetry-yield/work/innovation-prospective-20260912-v2/protocol.json --amendment /home/ubuntu/telemetry-yield/work/innovation-prospective-20260912-v2/pass-dependence-amendment-v2.json --output /home/ubuntu/telemetry-yield/work/innovation-prospective-acquired-20260919-v1
```

This does not grant waveform release or run decoding. Executables currently bind this research host's paths; the [source snapshot](/home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/evaluation-tools-source-v2.tar.gz) preserves code/protocol bytes but is not a hermetic portable release. Core Rust fresh-build reproduction is documented separately.
