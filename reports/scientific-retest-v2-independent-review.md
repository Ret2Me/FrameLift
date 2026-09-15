# Independent review: operational retest v2

Review date: 2026-09-07 UTC. Scope: new execution of the same 6,168 scientific
units after the user requested more liberal limits, correction of execution
errors, and continuation. **Status: GO for the versioned operational retest,
subject to its new timestamped amendment; all independent tests passed.**

## Reviewed identities

- Runner: `work/blind-phase-confirmatory-v2/scientific_campaign_runner_v2.py`,
  SHA-256 `a4f9aab2819dc2c7cb182e09395b27ae6be8661bdda9d3d9314b1734afb4e0d8`.
- Revised supervisor: `work/blind-phase-confirmatory-v2/bounded_process_revised_v2.py`,
  SHA-256 `17547a8a0edf3585d14cc6fc36145c684a7c386da34aa99befefc0adacfd2a3a`,
  33,041 bytes.
- Independent tests: `tests/test_scientific_campaign_runner_v2.py`,
  SHA-256 `fc424be44959ed4e4df8d3a2a5994b3d4c22e400a8bb74a4db794279d3c674af`.

## Scientific and operational boundaries

The execution plan, candidate configuration, original 30 IQ identities,
four generic rates, two repeats, mission routes and driver arguments, null
control kinds/seeds/chunks, exact CRC verification, amended-v2 normalizer,
trusted/untrusted distinction, and frozen statistical calls are unchanged.
AST-level comparisons cover the integrity and evidence paths and the numerical
scoring body through both the full-30 and sensitivity-29 scorer calls. The
entire observation 4491 is still excluded from the sensitivity analysis;
all-zero controls still require the degenerate-input contract and do not count
toward the false-acceptance denominator.

Authorized operational changes are four concurrent workers, 2 GiB cgroup RAM
and 512 tasks per unit, 32 GiB address space per process, 15 seconds termination
grace, and a new 3-second resource-monitored natural-exit interval. Cgroup RAM
is therefore bounded to 8 GiB for four simultaneous units, not 128 GiB despite
the much larger virtual-address-space ceilings. Original CPU/wall/log/file
limits remain in force.

The natural-exit correction waits for cgroup emptiness even when stdout/stderr
are already closed. It does not turn persistent descendants or resource
violations into completed results. The current supervisor test suite includes
persistent-descendant cleanup, resource enforcement during natural exit, and
actual concurrent bubblewrap jobs.

All 6,168 units are new executions: 514 signal and 5,654 null. The original two
pre-campaign exposures remain documented in historical metadata but are no
longer imported into this retest. The old full-v1 run has already exposed all
30 observations. Accordingly the new run is explicitly
`post_outcome_operational_retest_of_exposed_cohort`, not a fresh blind holdout.
The wrapper retains numerical scores and lowers publication/deployment claims
to false, adds this classification, and recomputes evaluation self-hashes.
Six previously observed mission repeat mismatches involving untrusted noise
PDUs are not flattened or hidden by a revised normalizer.

## Preservation checks

No original runner, decoder, normalizer, plan, IQ, or v1 result was edited.
The following original hashes were independently checked before integration:

- v1 runner: `da0241f9b9d75c81632262467f6f17fe91eac13944daf8a5789e6e6feed880b8`.
- v1 evaluation: `c7adc5169a11fa4cea4c17f807ff1782ab0e42f507800d0d578ea4b4c5e450f4`.
- v1 binding: `63a738a58a93314f9f2a473e0e798b7b57637be0379e58c3ad69e4750d405d14`.
- v1 source ledger: `05cd4bc67280bf581896e9160f38542dab5622b0672bb8d3966a8e23a733c505`.

New output must use a separate directory. The runner explicitly rejects the
v1 execution directory and retains no-clobber publication, operation binding,
resume evidence validation, and interrupted-without-receipt no-relaunch rules.
It does not claim success of the failed historical persistent-namespace guard.

## Independent tests

The first metadata/regression pass completed with 47 passed and 3 explicit
integration skips. The final suite added an unchanged-argv comparison and ran
with `TY_SCIENTIFIC_INTEGRATION=1`: **51 passed in 130.37 seconds, none skipped**.

Persistent evidence root:
`work/blind-phase-confirmatory-v2/scientific-v2-independent-tests-1T56j5/`.
Its `junit.xml` and `pytest-temp/` contain the final test result and actual
synthetic process receipts, raw outputs, and normalized outputs.
JUnit SHA-256:
`9558dc62ae7a193aecd3a010e8bda7795bd2f4c465c61973482d95cca529211a`.

Real integrations cover a known synthetic G3RUH positive through all three
drivers, CRC corruption rejection, resume without re-execution, all three
drivers twice on generated zeros, and the previously task-limited Astrocast
0.1 multi-chain profile twice on generated zeros under the 512-task limit.
These fixtures are not recovered station telemetry and are not counted as new
scientific gain. No cohort IQ is decoded by this independent preflight.

All 11 actual synthetic executions completed with confirmed process cleanup:

- Positive fixture: candidate and component each retained the exact one
  expected unique CRC-verified payload. Mission retained it only as one
  untrusted diagnostic payload, correctly with zero trusted frames. Candidate
  elapsed 60.39 seconds with peak cgroup memory 239,157,248 bytes; component
  1.23 seconds / 63,680,512 bytes; mission 0.91 seconds / 62,459,904 bytes.
- Generated-zero fixture: three drivers, two repeats each; all six completed
  with zero trusted and zero diagnostic payloads and matching repeat
  projections for each driver.
- Astrocast 0.1: both generated-zero repetitions completed under the explicit
  512-task limit, with zero `pids.events:max`, zero accepted/diagnostic payloads,
  matching repeat projections, empty cgroups and removed scopes. Elapsed times
  were 0.81 and 0.85 seconds, with peak memory 63,467,520 and 62,283,776 bytes.

Actual integration directories below the evidence root are:
`pytest-temp/test_real_positive_all_three_d0/positive/`,
`pytest-temp/test_real_zero_all_three_drive0/zero/`, and
`pytest-temp/test_real_astrocast_profile_pr0/astrocast/`.
The Astrocast integration also reran the complete content-closure runtime
preflight after execution. Final runner/test identities and the four original
v1 artifact hashes above remained unchanged after the full integration suite.

## Decision

GO: the independently tested code can execute the separately timestamped
operational amendment, using a fresh output directory and all 6,168 new units.
No real-IQ campaign was launched by this review. This GO establishes executor
qualification for the stated retest, not superiority, guaranteed error-free
operation on every real capture, or publication/deployment readiness. In
particular, the existing 29-observation sensitivity null exposure is below the
frozen 30-hour threshold and is not improved by changing execution limits.

Graphify was used only for initial code/test navigation; its older graph did
not contain the September 7 runners. Current file identities, AST comparisons,
independent tests and actual runtime receipts are the decisive evidence here.
