# Bounded supervisor correction: natural cgroup exit

The v1 campaign's 221 `descendant_cgroup_survivor` receipts all have the same relevant pattern: root exit code 0, closed inherited stdout/stderr pipes, no recorded memory-limit or pids-limit event, and successful eventual process-tree cleanup. This is consistent with a supervisor teardown race; the old receipts do not record sufficient process identities to establish that every individual case was benign. They remain failed historical attempts and are not relabeled.

## Root cause and correction

In the frozen `bounded_process.py`, the post-root-exit grace loop waits only while output-pipe descriptors remain registered. Once both pipes reach EOF, it skips the grace entirely and probes `cgroup.events:populated` exactly once. A still-finishing namespace supervisor, last task, or short-lived descendant can therefore produce a survivor verdict immediately despite the configured five-second termination grace.

`bounded_process_revised_v2.py` preserves the frozen original and adds a separate, attempt-fingerprinted `natural_exit_grace_seconds` (default 1 second; the new campaign can explicitly choose 3 seconds). It waits for both pipe closure and actual cgroup emptiness. Memory, CPU, pids, output and wall-clock limits remain checked during this interval, and the grace never extends the wall budget. A persistent descendant remains a failure and is killed and checked for cleanup. The receipt records initial/final populated state and the actual natural-exit wait. Non-finite timing settings are rejected.

No IQ input, modulation, decoder, candidate configuration, CRC acceptance, scorer or historical result was altered in this task. This is an execution correction, not telemetry recovered or decoder-performance evidence.

## Verification

The focused suite uses the real user systemd manager and cgroup v2, not simulated process cleanup:

- The exact hash-pinned original supervisor reproduces the false-survivor condition with a short-lived child that has closed both output pipes.
- The revised supervisor waits for actual emptiness and completes that input successfully.
- A persistent child, inherited-pipe child and detached double-fork descendant still fail and are cleaned.
- Wall timeout, aggregate CPU, memory and pids limits remain enforced, including after root exit during the natural grace.
- Receipt content binding, resumability, malformed-receipt rejection and no-clobber publication remain tested.
- Forty fast jobs run at four workers through the host-required `sudo`/`setpriv`/`bwrap` path and all complete with empty cgroups.

The final focused suite contains 25 tests. Initial synthetic wrapper setup without the host-required `sudo`/`setpriv` prefix failed visibly with `Operation not permitted`; the test fixture was corrected to use the actual deployment wrapper. No supervisor failure was hidden or converted into a success. The corrected complete suite passed in 8.36 seconds before the identical evidence-producing rerun.

## Artifact identities

- Original preserved supervisor: `work/blind-phase-confirmatory-v2/bounded_process.py`, SHA256 `e4c687d9b5c721b61e59c9832b12f581057962bcf6c75c6dc544debb564feb49`, 30,726 bytes.
- Revised supervisor: `work/blind-phase-confirmatory-v2/bounded_process_revised_v2.py`, SHA256 `17547a8a0edf3585d14cc6fc36145c684a7c386da34aa99befefc0adacfd2a3a`, 33,041 bytes.
- Tests: `tests/test_bounded_process_revised_v2.py`, SHA256 `b2a273012a708172708aeb8147abc09c1c0d1ffba5b60ac688cf994592016256`, 18,960 bytes.
- Machine-readable test evidence: `reports/bounded-process-revised-v2-tests-20260907.xml`.

The existing Graphify knowledge graph routed this investigation to `tests/test_blind_phase_bounded_process.py`; current frozen code and saved execution receipts, rather than the historical graph, established the diagnosis.
