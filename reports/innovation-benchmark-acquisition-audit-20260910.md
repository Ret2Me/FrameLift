# Acquisition and benchmark audit, 2026-09-10

## Handoff snapshot at 2026-09-10 21:23 UTC

No fresh cohort has yet been frozen or decoded. Original metadata collection has 59 completed pages / 1,475 rows and is honoring HTTP 429 `Retry-After: 2863`, received at 20:59:47 UTC. Its monotonic retry deadline corresponds approximately to 21:47:31 UTC. Further quota delays may occur; no early retry or alternate host/IP is used to bypass them.

Original metadata process: PID 127210, Linux start ticks 3334061, exec session 91559. It continues the original full-interval snapshot. Partial-prefix availability is not a frozen cohort and is not used as a substitute for interval EOF.

Corrected automatic continuation: PID 140344, exec session 2901. It binds the exact metadata PID/starttime/executable, waits at most six hours for local atomic cohort publication, verifies full metadata provenance, then runs the corrected selector and original OGG acquisition. It makes no metadata API requests, never bypasses Retry-After, and never launches DSP. It writes immutable waiting, started and terminal receipts.

Corrected cohort/waveform destination: `work/innovation-benchmark-20260910-v2/acquisition-corrected-v3`. The original `acquisition` directory and its old selection remain immutable provenance only. The corrected cohort will retain the structural v2 cohort schema but freeze a v3 selection plan and explicit station-field amendment.

## Disclosed station-schema correction before waveform access

The actual public API field is `ground_station` (for example 1616), not `station_id`. The first selector would have grouped missing station IDs as null and implemented day-only rather than station/day sampling. A read-only metadata audit caught this before any new waveform download or decoder outcome. The original automatic downloader continuation (PID 136372) was stopped; the metadata collector was not interrupted.

The v3 selector requires positive numeric `ground_station` and rejects missing station fields rather than collapsing them. It replays only the complete original metadata snapshot into a new directory. Date limits, 500-observation target, SHA256 rank prefixes, round-robin sampling intent, prior exclusions, outcome-blind policy and transport limits are unchanged. Original files are preserved, and the old cohort will be explicitly marked not used. See the separate v3 selection amendment for exact scope.

## Verification evidence

- Initial acquirer: 7/7 standalone tests passed.
- Corrected replay/acquirer: 9/9 standalone tests passed, including the actual `ground_station` shape, distinct station strata, missing-field rejection, full EOF replay, partial-prefix refusal, immutable original cohort preservation and outcome-independent ordering.
- Continuation: 1/1 PID starttime parser test passed. The active process successfully bound the real metadata PID/starttime/executable at launch.
- Prior-exposure scan: 365,988 artifact entries; 256 MiB bounded metadata identifier reads; 8,877 conservative excluded IDs. All IDs from the previous 20-observation set, old holdout and old week cohort were independently confirmed included. Limitations are explicit: 7,828 symlinks not followed, 18,939 JSON files beyond reading bounds (path IDs still excluded), one known unreadable environment guard. This is not proof of global nonexposure.

Build identities, source archives, program versions, process ownership and test summaries are retained in `work/innovation-benchmark-20260910-v2/acquisition-v3-build.json`, `acquisition-continuation-v3-waiting.json`, and the associated immutable artifacts. Standalone rustc builds did not overwrite shared Cargo test/release outputs. Replay-only v3 retains inert copied helper functions and emits dead-code warnings; no strict-clippy-clean claim is made.

## Independent runner review (source only, no decoder results)

The review identified and the runner author corrected:

1. A final observation-level validation failure could previously be counted as complete when all five arm commits existed. Primary comparisons now require a complete observation state while retaining per-arm statuses/costs.
2. Reanalysis/resume now explicitly verifies common PCM identity across every arm and the observation record.
3. Relative bootstrap intervals no longer silently discard zero-baseline resamples; undefined-draw counts are reported and the relative interval is null when any draw is undefined.
4. The runner checks the supported profile and freeze/end chronology and accepts consistent `plan.norad_cat_id` / `plan.norad` aliases, rejecting conflicts.

These corrections were rechecked directly in runner source SHA256 `23b3b92e484e5a15d15e88841fe91a1d901f1c84ebeb993ba6e4d918ed758653`. No benchmark performance result or fresh decoder output was viewed in this audit.

The native received FCS is independently rechecked; external stripped FCS is correctly described as decoder-attested, not fabricated. Exact PDU sets distinguish observation-level additions from globally absent telemetry. Failure denominators and shared-host timing limitations remain explicit. None of this establishes receiver novelty, false-alarm qualification or publication readiness.

## Root-owned recovery, snapshot at 2026-09-10 22:03:53 UTC

This section supersedes the process-liveness statements in the 21:23 snapshot.
Later checks found both original process IDs absent, the original exec session
unavailable, and no terminal receipt or full-interval EOF. The definitive cause
was not established; this is not evidence of a metadata or waveform completion.
All 59 previously completed metadata pages remain preserved.

The root agent added a bounded, metadata-only recovery executable. It accepts
only the pinned previous selection plan and reuses a cached page only when its
successful HTTP receipt, byte count, body SHA256 and exact requested cursor URL
match. It does not rescan or change the frozen prior-exposure exclusions.
Eight standalone tests passed, including modified-body and wrong-cursor refusal.
The previously recorded Retry-After deadline was honored before recovery began.

Acquisition now runs under the user service
`telemetry-innovation-acquire-20260910-v4.service`, independently of an agent exec
session. The invocation ID is `a8645ca6b805478bac03dff844970fcd`; its observed
MainPID was 147657. The service is a bounded oneshot with a six-hour startup
timeout, 2 GiB memory ceiling, one-core CPU quota, and control-group termination.
It is not a reboot-persistent schedule or an unlimited retry promise.

At the snapshot, the service was active in its metadata step and had persisted
119 pages / 2,975 rows in tier 0, without interval EOF. These are metadata rows,
not acquired OGGs or completed decoder tests. Its recovery output is
`work/innovation-benchmark-20260910-v2/acquisition-resumed-v4`. Only successful
completion of that step permits ExecStartPost to run the frozen v3 selector into
`acquisition-corrected-v3`, with the corrected `ground_station` strata and original
transport limits. A partial metadata prefix cannot trigger selection. The
service acquires data only and does not launch a fresh-cohort DSP benchmark.

A later receipt check explains the unchanged 119-page snapshot: page 119 received
another HTTP 429 at server time 22:03:46 UTC, with `Retry-After: 3226`. The
recovered service is honoring that delay (approximately until 22:57:33 UTC), not
continuously downloading new pages. No alternate endpoint or IP is used to evade
the quota. This prevents a completed fresh-500 result in the current snapshot.

Frozen recovery binary SHA256:
`5efe09ab267ffe8dcfe84b400c212f15d95e217949e3ddb2f055fa49f16fc462`.
Recovery source archive SHA256:
`d27f7dd45c7d98e0b8d4730858502cfd694885f2df217f03fc90f23246c4af38`.
Corrected selector binary SHA256:
`a0b07bd26c1003a35565168ac4540d84b3f2e681564dd70e3beb3c06db7128fb`.
The auxiliary recovery compilation ran at lower priority on the shared benchmark
host; receiver timings must not be presented as isolated measurements.
