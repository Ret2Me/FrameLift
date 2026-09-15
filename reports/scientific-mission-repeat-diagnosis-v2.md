# Mission-profile failure and repeat diagnosis — 2026-09-07

Read-only audit of `work/blind-phase-confirmatory-v2/campaign-v4-scientific-run-v1`; no new cohort decoding, no rewriting retained results, no changed decoder or normalizer. This report separates a proven resource failure from a still-unresolved receiver-output reproducibility issue.

## Actionable result

- All **48 `pids_limit` failures** belong to the **Astrocast 0.1** profile: 24 runs for observation 4512 and 24 for observation 4549. Every retained receipt records `pids.max=64` and `pids_events_delta.max=1`. The cap was actually hit; this is not a speculative explanation from the status label.
- These failures occur after **1.027–1.760 seconds**, with **62,156,800–75,436,032 bytes** peak cgroup memory. All memory-event counters, including `max`, `oom` and `oom_kill`, are zero. Increasing RAM alone would not fix these failures.
- The six pairs whose two processes completed successfully differ in **actual untrusted candidate presence: zero versus one PDU**. They are not differences in KISS order, wall-clock metadata, receipt fingerprints or JSON serialization. **Do not suppress those differences in the normalizer.**
- A disclosed follow-up with **512 tasks/process tree, 2 GiB/process tree and four concurrent units** is a reasonable operational change. Keep the existing single-thread BLAS/OMP settings; these already existed in the failed mission subprocesses. Qualify the wider Astrocast profile itself using synthetic input before resuming the full schedule.

## Exact successful-pair differences

In all rows the only scientific projection changes are candidate count, candidate-byte count and the indicated untrusted PDU. Both process outcomes are return code zero, empty stdout/stderr, successful cleanup, and zero cgroup task-limit events. All trusted-native lists remain empty.

| Observation | Input | Profile | Repeat A | Repeat B | Diagnostic payload hex |
|---|---|---|---:|---:|---|
| 4509 | `phase-scramble-1376283091369227076` | BUGSAT-1 | 0 | 1 / 4 B | `8007bc8e` |
| 4493 | `phase-scramble-9775852855765700364` | NETSAT 1 | 1 / 18 B | 0 | `aa033e6e2cf1028dc6a0bb34e68149b515bc` |
| 4493 | `phase-scramble-4575003776086450455` | NETSAT 1 | 1 / 7 B | 0 | `a08bf28cd026f6` |
| 4515 | Original signal | BUGSAT-1 | 0 | 1 / 1 B | `44` |
| 4692 | Original signal | BUGSAT-1 | 1 / 35 B | 0 | `402c36eebbb26e4ca96262fae38129f656e687b0fa753a333d737a2b4b4d093cde5eab` |
| 4515 | `phase-scramble-13704097790678324204` | BUGSAT-1 | 1 / 12 B | 0 | `80f964a90236125b6d02dd14` |

These are all `mission_protocol_unvalidated`, not valid AX.25 telemetry. The frozen gr-satellites `hdlc_deframer.py` checks an HDLC FCS but allows a payload of any length above zero before stripping two FCS bytes; it does not itself enforce a valid AX.25 address/header. Therefore a one-byte diagnostic is not proof of a valid satellite frame. The campaign normalizer correctly rejects its promotion to trusted telemetry and cannot independently replay an FCS already consumed at the KISS boundary.

## Why the Astrocast graph needs more tasks

`Astrocast_0_1.yml` configures three parallel transmitters: 1200-baud FX.25 NRZ-I, 1200-baud FX.25 NRZ, and 9600-baud CCSDS Reed–Solomon with interleaving five. Cgroup task limits count threads as well as processes. The decoder's wide GNU Radio graph can therefore exhaust 64 tasks even though its numerical-library thread pools are fixed to one. The retained receipts prove exhaustion but do not record the precise maximum desired thread count; **512 is a proposed headroom setting, not a measured requirement**.

The existing all-driver zero/positive preflight uses a different single-transmitter mission profile. Passing that preflight is not sufficient evidence that Astrocast starts successfully. The follow-up should include Astrocast zero-input start/complete/cleanup and inspect `pids_events_delta.max`, memory peak and the returned raw result.

## What is and is not known about nondeterminism

The six affected pairs did **not** hit the task limit. A liberal cap fixes the proven Astrocast failures but is not yet evidence that these other pairs become deterministic.

The frozen mission wrapper sends both inner decoder stdout and stderr to `/dev/null`, and removes the original KISS file with its temporary directory after extracting PDUs. Consequently the retained evidence cannot locate the differing candidate's sample time or determine whether it was lost at end-of-file.

Static inspection shows that `gr_satellites` calls `tb.start(); tb.wait()` at end-of-file. Its KISS output path routes asynchronous messages through `pdu_to_kiss`, a PDU-to-tagged-stream block, and a file sink. A scheduling/end-of-stream drain issue is therefore a plausible hypothesis, **not a demonstrated root cause**. Numerical chunk-boundary effects are also not ruled out. The inspected FSK demodulator does not explicitly use a random initializer; this is not proof that every underlying native block is deterministic.

KISS timestamp records are not the explanation for these six differences: upstream uses command byte `0x09`, while the frozen parser only collects command low nibble `0x00`. The wrapper also already sets `PYTHONHASHSEED=0`, `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1` and `NUMEXPR_NUM_THREADS=1` in the actual inner process environment.

Recommended follow-up without fitting the science to the results:

1. Preserve the v1 run as incomplete, and run the disclosed v2 resource/supervisor correction on the whole fixed schedule, not only positive-looking units.
2. Keep the current diagnostic-repeat mismatch test unchanged. If a pair still differs, report that repeat failure rather than treating equal trusted-zero sets as full output determinism.
3. For a separate diagnostic instrumented adapter, retain bounded raw KISS and inner stderr, and compare an independently observed PDU stream with parsed KISS on a fixed synthetic late-frame fixture. Only a demonstrated transport/drain bug justifies a new adapter version; no arbitrary sleep, altered decoder settings, or exclusion of inconvenient untrusted candidates should masquerade as a validated fix.

## Evidence locations and identities

Campaign base: `/home/ubuntu/telemetry-yield/work/blind-phase-confirmatory-v2/campaign-v4-scientific-run-v1/units/`. Each directory below contains `normalized-result.json`, `process-receipt.json` and `output/raw-result.json`.

| Pair | Repeat A unit | Repeat B unit |
|---|---|---|
| 4509 null | `f32305c158eb46dc613cd3cd6efad9b1cfcc07ca6c3949b6b990348d20b12cc0` | `eba352f468d99501fe1e1b4da470f89eeef9be450eca8032db5dc37fb5bbf961` |
| 4493 null 9775… | `bb581f8b574ed2513c554482aa7287c6062ad8a04a85ae0abe4f1599bc7cae8a` | `1f24e94afed7596a91e69b1d315a036b83fb624e442b2c3232bbe2e396e0e3d5` |
| 4493 null 4575… | `281162279b9a0fb1b471e7b300aea8d2ff5fe05476a31f1a67b9ee90a791d5ff` | `b3ed7f9ee73e0ab08b0391a3e7c144137ea9e4e38558e1e379317b78042da20c` |
| 4515 signal | `c1fbc7b474716664b0bdbafc4580900394d4857f47a3be6cea4eb29483c92976` | `e444e6d18973ca0dc459296c5205f7cd744f1a867827157fd40536a6d9481f1a` |
| 4692 signal | `9a7b82da4bfa833b25ad0d3f490ef768ac7bf9b2b20968fad6a4484a31c41678` | `c772c4b7a970025ce5737e788bb6ecb333168b01c889a714f57c971a5854760c` |
| 4515 null | `da2b7c1cdc0f67b35d879a87ad3cf696c716fccbc47d79d261f014de952c14c9` | `48e82c84f92cf5cfd4a7016bccce0687bd9d133e3c19cba0cd318fdddf089d5a` |

Representative proven pids-cap receipt: `4bcc511b2b49f8e39d4f81946232e54d27aeb92a7af07660a66f25b8c3897ab6/process-receipt.json`, payload SHA-256 `3161f4c54f9a807f9ebd044e9c9fa8b701a77749bd4844ea8d3451e1ba11f98e`.

Unchanged mission driver: `/home/ubuntu/telemetry-yield/work/blind-phase-confirmatory-v2/run_mission_satyaml_unit.py`, SHA-256 `485bc30e4ccdd57b874088c593d059d2e6689f910512a2f43bddb6bbf6a8278d`, 7863 bytes.

Unchanged normalizer: `/home/ubuntu/telemetry-yield/work/blind-phase-confirmatory-v2/normalize_result_amended_v2.py`, SHA-256 `dbe769c793bd0f4fc33d04ab55233d8e457a708ee16cf0bacc72e205bead3902`, 40473 bytes.

Graphify query expansion `[mission, repeat, normalize, pids]` located the earlier normalizer and cgroup tests, particularly `test_true_receiver_count_difference_changes_determinism_projection()` and `test_cgroup_pids_max_is_a_hard_aggregate_gate()`. The current diagnosis relies on direct retained-artifact comparison and actual frozen runtime source inspection, not on stale graph claims.
