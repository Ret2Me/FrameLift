# Verified-start gate for the scientific process supervisor

The operational v2 retest produced a startup exception for unit `87f2b8db38f02d5ec64c9a8c5194a4c8650491b6234e3de295761e3ef68e2dbe` (candidate, 9600 baud, observation 4491, repeat-a): `systemd did not materialize the required per-job cgroup`. The output directory is empty and the saved exception lacks subprocess return code and stderr. Consequently, this particular historical error cannot honestly be classified as a benign fast-exit race; an actual startup failure remains possible. All v2 results and frozen code are preserved.

## Correction

There is nevertheless a reproducible race in the old supervisor: it starts the actual decoder before verifying the cgroup. An instant decoder can finish and its transient scope can disappear before the supervisor's first scheduled check. V3 removes that race with an actual pre-execution barrier, not a startup sleep or acceptance of an unobserved scope.

1. `systemd-run --scope` starts a silent, exact-hash-pinned `/usr/bin/python3.12` gate outside bubblewrap. The gate blocks reading its standard-input pipe; the decoder has not executed.
2. The supervisor verifies that the scope exists, reads back MemoryMax, TasksMax, zero swap and 100% CPU quota, and obtains initial CPU/memory/pids accounting.
3. Only after every check succeeds does the supervisor write the release byte and close the pipe. The gate resets stdin to `/dev/null` and directly `execv`s the unchanged decoder argv.

An unreleased gate receiving EOF exits without executing the decoder. Failures kill and check the scope, close gate descriptors, and cannot publish a completed receipt. Startup exceptions now retain bounded nonblocking stdout/stderr prefixes and the subprocess return code. The gate's program SHA256 and interpreter identity are included in the attempt fingerprint and receipt. No output-stream protocol noise is added.

V3 retains the v2 natural-exit correction, hard aggregate resource enforcement, descendant cleanup, content-bound receipts, and resumability. No decoder, IQ, candidate configuration or scoring change is part of this correction.

## Focused verification

The final suite passed **33 tests in 12.11 seconds** under the actual user systemd manager/cgroup v2. It includes 40 concurrent real sudo/setpriv/bubblewrap jobs, a deliberately delayed scope-verification check proving the target cannot start early, injected scope/accounting/release-EOF failures, driver stdin and exact stdout/stderr checks, pinned-interpreter drift rejection, preservation of startup stderr/return code, reproduction of the original scheduling race, and all v2 natural-exit/resource/receipt tests.

The independent verification agent separately performs service-context stress and the actual synthetic decoder pipelines. Their outcome is not presumed by this focused test report.

## Identities

- Supervisor: `work/blind-phase-confirmatory-v2/bounded_process_revised_v3.py`, SHA256 `65e2ae5b2d81e8fc887e7f5fc3011e2e0ae7d15163cac1fb991171c25435809f`, 36,077 bytes.
- Tests: `tests/test_bounded_process_revised_v3.py`, SHA256 `7b6589382d759d5c4ecb84e148709b2e74bc9f8d27df3c49c1842b021a8d00b8`, 24,757 bytes.
- Test evidence: `reports/bounded-process-revised-v3-tests-20260907.xml`, SHA256 `442a5764eb4fb41ff80eff5d33e56cd737701299717b12c9916f08a2e243912d`.
- Gate interpreter: `/usr/bin/python3.12`, SHA256 `a92f0f95e883390c7256b2e441484aac06b1002dbe1d924141a77c8d82f96223`, 8,025,024 bytes.
- Inline gate program SHA256: `24ed6937f0752cf528236fdb70721b0f6bd865f5b12f37e1e29d4ef808846afa`.
