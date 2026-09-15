# Operational retest v2 — 7 September 2026

The user explicitly requested more liberal resource limits, correction of execution errors, and continued tests. This is a new execution on the **already exposed** 30-observation cohort. It is not a new blind holdout. All v1 files, receipts, failures and evaluations remain unchanged.

## Execution changes

- Per-unit memory ceiling: 512 MiB → 2 GiB.
- Per-unit process/thread ceiling: 64 → 512.
- Per-process virtual address-space ceiling: 8 GiB → 32 GiB; this is not a physical-memory allocation.
- Concurrent units: 8 → 4, giving a sum of at most 8 GiB in decoder cgroup memory ceilings. The VM remains capped independently; unrelated station/planning services are outside this change.
- Termination grace: 5 → 15 seconds; separate natural-exit grace: 3 seconds. Wall and CPU limits remain four hours per unit, with bounded logs and files.
- The revised supervisor waits for both closed output pipes and an actually empty cgroup after the root exits. Exceeding a resource/time bound or leaving a persistent descendant is still failure, followed by cleanup. Old survivor receipts are never relabelled.
- All 6,168 units execute freshly in a separate v2 directory, including the two units originally imported into v1. This ensures uniform execution limits. All prior exposure remains disclosed.

## Scientific invariants and interpretation

The decoder implementations, candidate configuration, four generic symbol rates, mission profiles, 30 source IQ recordings, two repeats, eleven control transformations/seeds, strict frame integrity checks and numerical scoring remain unchanged. Full-cohort and whole-observation #4491 exclusion analyses are both retained. A new output root changes file/attempt provenance, not input samples.

The previous six both-successful mission-repeat mismatches contain genuinely different unvalidated diagnostic PDU sets, not merely bookkeeping hashes. They are not erased, canonicalized away, or counted as trusted telemetry. More liberal limits do not by themselves prove that the mismatch is repaired. The new run will report the same strict repeat checks.

The first run exhausted its schedule with 269 execution errors and no trusted telemetry. Its zero yield neither proves parity nor validates the receiver. A successful operational retest must still be distinguished from positive evidence of recovery gain. Publication and deployment claims remain false pending independent scientific and deployment qualification.

Before new cohort decoding: focused regression tests, real synthetic positive and zero tests, an Astrocast 0.1 wide-profile startup test, independent review, and an externally timestamped amendment binding final code and limits are required. This note is rationale, not a claim that those checks have already passed.
