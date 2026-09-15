# Independent review: startup-gated operational retest v3

Date: 2026-09-07 UTC. **Status: GO; 53 tests passed in the real service context.**
This review addresses the startup race found during the actual v2
retest, not a change of decoder or scientific hypothesis.

## Why another version was required

The v2 preflight passed 51 tests, including 11 real synthetic driver runs, but
the subsequent real campaign still revealed a distinct startup race. Its
interrupted record set contains 43 normalized results: 42 completed and one
`supervisor_failure`. The exact failing unit is
`87f2b8db38f02d5ec64c9a8c5194a4c8650491b6234e3de295761e3ef68e2dbe`,
candidate / 9600 baud / observation 4491 / repeat-a. The recorded exception is
`systemd did not materialize the required per-job cgroup`.

That failure remains a failure; the earlier successful synthetic preflight
does not erase it. The complete v1 outputs and interrupted v2 outputs are
preserved, and v3 rejects both previous output directories.

## Independently reviewed code

- Runner `scientific_campaign_runner_v3.py`:
  `182b4105616c69746ba02ac1383476ad3811ece724d43b82cc7eaac17c77f1b8`.
- Supervisor `bounded_process_revised_v3.py`, 36,077 bytes:
  `65e2ae5b2d81e8fc887e7f5fc3011e2e0ae7d15163cac1fb991171c25435809f`.
- Independent tests `tests/test_scientific_campaign_runner_v3.py`:
  `1328ce5696090b4311596ab9c843b4a981a64eb94ccfb291e5962ef8696c6b99`.

The new supervisor inserts a silent, hash-bound Python startup gate ahead of
the unchanged direct driver argv. The gate cannot execute the driver until
the supervisor verifies the actual cgroup, reads back RAM/task/swap/CPU limits,
and samples initial accounting. A one-byte pipe release then replaces the
gate with the original driver, restoring stdin to `/dev/null`. EOF or loss of
the parent fails closed. Gate program/interpreter identities enter both the
attempt fingerprint and terminal receipt. Failed startup retains bounded,
nonblocking diagnostic output. All natural-exit, resource enforcement and
cleanup behavior from v2 remains in force.

The independent code comparison found no changes to decoder argv,
configuration, original IQ identities, four rates, repeats, mission routes,
CRC verification, trusted/untrusted separation, normalizer, null transformations
or numerical scoring calls. Limits remain four workers, 2 GiB cgroup RAM and
512 tasks per unit, 32 GiB address space, 15-second termination grace and
3-second monitored natural-exit grace. All 6,168 units will be fresh executions
in a separate v3 directory. The already-exposed cohort remains explicitly a
post-outcome operational retest, not a pristine confirmatory study.

## Strengthened verification

The entire independent suite is launched inside the real transient user
systemd service `telemetry-yield-v3-independent-m0X4r1.service`, invocation
`a10d3e214d9e4abea3618e3ba1587015`, matching the service context of campaign
execution rather than relying only on a direct shell.

In addition to all earlier positive/negative/CRC/resume/Astrocast checks, an
independent stress fixture runs 64 extremely short actual sudo/setpriv/
bubblewrap jobs in four threads. Each deliberately delays cgroup-limit
verification and asserts that the target output does not yet exist. Every
receipt must prove completed execution, observed baseline accounting, exact
startup-gate contract, zero task-limit events and an empty scope. The stress
test also verifies subsequent asynchronous removal of all 64 empty scope
directories within a bounded two-second wait. An
injected pre-release validation failure must execute no target and leave no
populated cgroup. This directly tests the previously uncovered timing boundary.

Persistent evidence root:
`work/blind-phase-confirmatory-v2/scientific-v3-independent-tests-m0X4r1/`.
The final JUnit file and synthetic receipts remain there. The service exited
successfully with code 0: **53 passed in 135.92 seconds, none skipped**.
JUnit SHA-256:
`7c03114f013ae1028b300f00827362fb0b8185f9fbc98b6937c6c4b4b0bf8379`.

All 11 actual receiver executions passed: the known positive fixture through
candidate/component/mission, three drivers twice on generated zeros, and
Astrocast 0.1 twice on generated zeros. The positive candidate/component
payloads passed strict CRC; mission output remained untrusted diagnostic data.
Corrupted CRC rejection and resume without target re-execution also passed.

All 64 fast target executions completed, with baseline accounting observed,
verified startup gates, zero task-limit events and confirmed cleanup. Nineteen
receipts honestly recorded deferred empty-directory removal; all 64 directories
were absent at the final bounded check. The injected pre-release failure
executed zero targets and left no populated cgroup. Its summary is retained at
`pytest-temp/test_real_startup_barrier_para0/service-startup/independent-startup-summary.json`,
SHA-256 `6c5fe14a8187079a4ab910079f69711e7de7cca845ad42371afb735f1a5632f5`.

The earlier test attempt in `scientific-v3-independent-tests-oCZcRm/` remains
preserved: 52 passed and the new stress test failed after eight successful
target executions because it incorrectly required synchronous removal of the
empty cgroup directory before receipt publication. Two receipts correctly
recorded `scope_removed_before_receipt=false` while also proving
`empty_after_run=true` and successful process cleanup; the directories were
subsequently absent. This was an over-strong test assertion, not an execution
failure. Only the test was corrected to require actual absence of live tasks
and separately check bounded asynchronous directory removal. No runtime code
or previous receipt was changed. The corrected final suite is rerun in full,
including positive, zero, Astrocast and the 64-job startup stress.

## Decision

GO for all 6,168 new executions with this exact v3 runner/supervisor, a fresh
output directory and its new timestamped operational amendment. Final
runner/supervisor/test hashes remained unchanged after the complete suite.
The v1 evaluation and interrupted-v2 summary also retained their exact pinned
hashes. No real cohort IQ was decoded by this reviewer, and no full campaign
was launched here. This executor GO does not establish decoder superiority,
guarantee every real-capture execution, or grant publication/deployment
readiness. The frozen sensitivity-29 null exposure remains below 30 hours and
is not repaired by execution changes.
