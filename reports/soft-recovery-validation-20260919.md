# Soft-recovery implementation validation — 2026-09-19

## What was implemented

- bounded reliability-ordered lists for LDPC, Reed–Solomon, K=7
  convolutional and concatenated convolutional/Reed–Solomon profiles;
- addition of aligned coded-bit LLRs across independently identified recording
  fragments or stations, followed by the ordinary FEC and integrity validator;
- an opt-in `unresolved-only` scheduler that runs the quick sweep first and
  records expensive tasks skipped after a quick received-FCS success;
- an explicit Amdahl scenario calculator for GPU planning.

Defaults are unchanged. The new recovery paths are opt-in and still require the
configured received CRC/FECF. FEC convergence or agreement between copies is not
by itself accepted as telemetry.

## Controlled positive cases

These are deterministic implementation tests, not orbital-yield measurements.

| Case | Ordinary result | New path | Result |
|---|---|---|---|
| Shortened interleaved RS with nine damaged symbols | Outside the ordinary eight-symbol correction radius | Reliability list tests bounded low-confidence bit hypotheses | Original CRC-valid AX.25 frame recovered |
| K=7 convolutional frame with an ambiguous information decision | Maximum-posterior frame fails received CRC | BCJR information LLR list | Original CRC-valid AX.25 frame recovered and re-encoded |
| Small controlled LDPC trapping-set case | Hard maximum-posterior output fails | Posterior/channel reliability list | Correct CSP frame, valid CRC and LDPC parity |
| Two independently identified soft copies with different weak bit errors | Each copy separately fails AX.25 FCS | Coded-bit LLR addition | One valid original AX.25 frame recovered |

The list tests also retain invalid-CRC controls: valid FEC alone cannot promote a
frame. Duplicate identities, overlapping same-source samples, corrupt protected
repeat headers, non-finite LLRs and false station diversity are rejected.

## Scheduler result

In the deterministic five-window scheduler fixture, quick decoding resolves
three windows. `unresolved-only` executes 11 of 20 tasks and records the other
9 as skipped. Replay reconstructs the exact skipped set. A separate end-to-end
CLI test runs two independent sessions, resumes one, and passes the exact
compute-session audit.

This is evidence that gating works, not a general 45% runtime claim. Savings on
real recordings depend on quick-stage success and the cost distribution of the
skipped stages. The policy can miss an additional weak transmission in a window
that already contains an easy frame, so exhaustive publication comparisons must
continue to use `fixed` or `marginal-yield`.

## Verification receipt

Commands were run from the repository root on 2026-09-19:

```text
cargo test --locked --lib
645 passed; 0 failed; 6 ignored

cargo test --locked --test cli_integration
29 passed; 0 failed

cargo clippy --locked --no-default-features --lib --bins -- -D warnings
passed

cargo clippy --locked --features cuda --lib --bins -- -D warnings
passed (host compilation only; no physical GPU execution)

RUSTDOCFLAGS=-Dwarnings cargo doc --locked --no-default-features --no-deps --lib
passed
```

The ignored library tests declare external local-data or manual benchmark
requirements. They are not failures and were not counted as executed evidence.

## Evidence boundary and next experiment

The controlled cases prove implementation behavior and fail-closed acceptance;
they do not establish an incremental gain on real satellite recordings. The next
publication experiment must freeze repeated real bursts, derive aligned LLRs
without using payload truth, compare single-copy, combining and list arms on the
same cohort, and report additions, losses, compute cost and negative controls.
Cross-station matching additionally needs clock/frequency alignment and a
mission-specific immutable-frame identity before combining is allowed.

GPU numbers remain scenarios rather than measurements. See
[`gpu-speedup-estimate-20260919.md`](gpu-speedup-estimate-20260919.md).
