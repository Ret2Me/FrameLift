# RML24 blind-origin v3.1 — independent audit

## Verdict: FAIL_CLOSED_PROVENANCE

The numerical replay is exact, but the provenance claim fails closed. CPython
executed ten imported local modules from timestamp-validated `.pyc` caches that
are deliberately excluded from the lock. The current cache code objects equal a
fresh compilation of the locked sources, so this is a freeze-enforcement gap,
not evidence that the reported BER is numerically wrong.

| Gate | Status |
|---|---|
| lock_and_inventory_integrity | PASS |
| transitive_executed_dependencies | FAIL |
| verification_to_use_atomicity | FAIL |
| native_runtime_dependencies | FAIL |
| environment_scope | PASS |
| split_disjointness_and_120_shards | PASS |
| truth_isolation | PASS |
| arithmetic_and_controls | PASS |
| full_replay_and_no_posthoc | PASS |
| read_only_and_immutable_flags | FAIL |
| local_freeze_chronology | PASS_LOCAL_ONLY |
| preregistration_compliance | PASS |
| narrative_report_consistency | FAIL |
| production_default | PASS |

## Exact replay

The complete transfer and 768-record holdout were regenerated at
`work/rml24/v31-independent-replay.json`. The published and replay metric cores
are exactly equal and both independently canonicalize to
`4206f4c4b2e32f5a8c2af2808b0feca0ea37a892a00e1d565f3ffa8e20b397e0`. After normalizing only `generated_at_utc`, the entire
evaluator envelope is exact; no post-hoc-only field was found.

| Holdout method | BER |
|---|---:|
| zero shift | 0.477585618897186 |
| acquisition v2 | 0.477738189259047 |
| blind origin v3 | 0.384143751196630 |
| truth-aided shift 8 | 0.456975157955198 |
| truth-aided shift 1024 | 0.166802783362052 |

All integer counts, BER divisions, success checks, wrong/random controls and
edge identities reproduce. All 120 selected IQ/truth shards (1,191,975,360
bytes) match both the manifest and freeze inventory. Development-provenance,
transfer and holdout record keys are pairwise disjoint.

## Findings

- **HIGH — UNLOCKED-EXECUTED-PYC:** Ten imported local modules execute code objects loaded from __pycache__/*.pyc. The lock records only their .py source paths and explicitly excludes .pyc files. Timestamp pyc validation checks only source mtime and size, so a same-header bytecode substitution would pass the freeze gate.
- **MEDIUM — READONLY-NOT-IMMUTABLE:** The lock, evaluations and sidecars are mode 0444 and sidecar-valid, but none has Linux FS_IMMUTABLE_FL. The owner can restore write permission; the stated read-only contract passes, while literal filesystem immutability does not.
- **HIGH — UNLOCKED-NATIVE-RUNTIME:** The runtime maps system shared libraries outside the hashed interpreter/stdlib/NumPy/SciPy trees, including BLAS/LAPACK, libc, libm, libgfortran, libstdc++ and the dynamic loader. Their contents are not frozen.
- **HIGH — VERIFY-USE-TOCTOU:** Code and data are verified once, then later imported/read through paths. Locked inputs remain writable and are neither descriptor-pinned nor rehashed after the replay, so a concurrent post-check modification would not be rejected.
- **INFO — LOCAL-CHRONOLOGY-ONLY:** Local artifact order is consistent with freeze-before-replay, but no external trusted timestamp or signed commit exists. The v3.1 artifacts disclose and appropriately bound this limitation.
- **MEDIUM — NARRATIVE-OVERCLAIMS-COMPLETENESS:** The MD accurately discloses cache exclusion and local chronology, but still labels the replay provenance-complete, says every loaded local Python file is locked, and calls mode-0444 files immutable. Those statements are too strong given the pyc and TOCTOU gaps.

## Truth isolation and scope

Static AST inspection confirms that recovery and the estimator receive no truth,
truth length, record index, SNR, filename or group identifier. The lock is
verified before the dynamic evaluator import. Truth remains in the same scorer
process, as explicitly preregistered.

The replay is correctly labelled as previously exposed, not a new holdout or an
independent scientific validation. It does not establish fully blind BER,
packet yield, a universal synchronizer or SatNOGS comparability. The production
origin estimator remains opt-in and is absent from the default route.
