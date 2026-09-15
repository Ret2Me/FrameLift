# Independent anchored-control and repaired-innovation audit

Status: the single-anchor and two-anchor control suites and repaired innovation regression pass their specified artifact checks. They do not establish publication readiness or production false-alarm performance.

## Single-anchor controls

All four 600-second semi-synthetic white/pink/brown/tone composites completed the full progressive policy: 1,393 canonical task commitments per recording, seven stages and 199 physical windows per stage. Each recovered exactly one unique 264-byte PDU, byte-identical to the archived CANVAS packet SHA-256 `795faee594e744d9d67a2c2117dfe08f4ab17be31cba5f4d55629bad5babf18e`, with received FCS bytes `03ea`. An independent bitwise CRC check passes; no unexpected frame occurs.

The audit checks actual source and executable identities, source-splice receipt binding, complete stage/window membership, task commitment digests, task/trial/stage/final frame unions and a separately executed frozen frame auditor. Current integrity checks are not trusted historical execution attestation.

Actual transfer coverage matters: `early-nearest` and `nearest` each executed 1,824 timing/gain trials in 38 windows per recording. `early-multi` and `multi-anchor` executed zero trials: each had 398 explicit frontend/window skips because there were not two mutually disjoint CRC-trained source windows. A complete task bank therefore must not be described as execution of every conditional algorithm branch.

The four noise traces are reused from the earlier 40-minute controls. These composites add zero independent negative exposure and do not measure false alarms on real interference.

Receipt: `work/canvas-reference-audit-20260911/anchored-control-suite-independent-audit-v1.json`, SHA-256 `0247f0168c8a21636287d4a50fe21201ff7f133d457a3fed06ba581af0f34aa1`.

## Repaired innovation regression: public observation 14967362

The repaired executable SHA-256 `638066b9c3edee6227373aa7919c4ee8d9705ca4ad89426f30398987b6e5042a` returns the same four complete frames as the original frozen innovation-v2 result. All four received FCS values pass independent bitwise CRC verification. Each PDU is 264 bytes. There are no added or lost frames relative to that original result.

Canonical input identity is `b55880744b79706d5df8e8bc4fdded271bc6a3e747b238cabee1b2c28ee6e307`; original OGG identity is `db9c4f43f5d707847191b6a1ad9e44d99a4a104ef3234767f401c2707e61bd9a`. Independently repeating the declared FFmpeg conversion into a fresh temporary directory reproduced the exact canonical PCM16 bytes. No decoder was rerun for this audit.

Coverage is 228 physical windows, including the final partial window: 912 baseline trials, 1,824 existing supplemental trials, and 3,648 matched-white/innovation trials. All 456 window/frontend pairs are accounted for: baseline executes both clock banks for every pair; the supplemental and innovation stages execute 38 pairs and explicitly skip 418. Executed transfer pairs have their complete declared timing/gain bank.

Zero codec fits and zero pooled fits were accepted, so this case validates neither positive codec-fitting behavior nor an incremental gain from pooling. The prototype has no progressive-style per-task commit journal: consistency of its complete reported trial bank and current identities is weaker evidence than canonical task commitments. This is a known development observation, not an independent holdout.

Receipt: `work/canvas-reference-audit-20260911/repaired-innovation14967362-independent-audit-v1.json`, SHA-256 `c064cfd48124e6f5002931a8401fabe6c929bb763dbe7826f6934bc84d348f84`.

## Audit implementation and next control

The standalone Rust artifact auditor is `examples/anchored_control_audit.rs`, frozen executable `work/canvas-reference-audit-20260911/anchored_control_audit_v2`. Seven tests pass. Its first version incorrectly used floor division when checking physical-window count and rejected the valid last partial window; that failed source/binary were preserved and a boundary regression test was added. No receiver result or decoder was changed to satisfy the audit.

A separate two-anchor plan was frozen before generation: the same exact six-second packet-bearing source interval is inserted at 150–156 and 201–207 seconds, 51 seconds apart, so there can be eligible targets within the frozen 60-second transfer radius of both sources. Two packet copies still count as one unique PDU. This targets multi-anchor transfer, not codec pooling that requires distinct received packets.

Plan SHA-256: `3edd949a07cd5bbb17a1ecc21dc0907ed65618f7dfdd5d5e35d61ee1cdc3795e`. The separate Rust sample checker verified all four times 28,800,000 samples and its exact half-open boundary test passes. Sample receipt SHA-256: `c0e4f0d200897a2b2bc72eba113f8ddebb5727f5b3a9c2196b74baf3181b6a1e`.

All four two-anchor full banks finished and passed the independent audit. Each has 1,393 verified canonical tasks and exactly the expected single unique PDU with valid received FCS; there are zero unexpected frames. Both `early-multi` and `multi-anchor` actually executed 408 trials in 17 windows per recording. Each executed frontend/window bank has all eight timing hypotheses and three gains. `nearest` executed 2,832 trials in 59 windows per recording.

The expanded auditor independently checked that every multi-anchor trial refers to at least two source windows present in the correct frozen baseline generation, that those windows contain the expected native CRC-valid frame, and that source-source and source-target separation satisfies the one-second guard and 60-second distance limit. Remaining multi-anchor frontend/window combinations have explicit skips; these conditional skips are not counted as executed trials.

Two-anchor receipt: `work/canvas-reference-audit-20260911/two-anchor-control-suite-independent-audit-v1.json`, SHA-256 `65d2627f35b731a43eca6c817a49a4777309f0c097e6bf99a4cd0518b43419c9`. Frozen verifier `work/canvas-reference-audit-20260911/anchored_control_audit_v4`, SHA-256 `cb71f94ec3c98367ac1830700612fd92fa04147665181b01afb76bd92b9b941f`; nine tests pass. Earlier verifier versions and source snapshots remain preserved. Current source SHA-256: `7b2e1dff7a82b5a6c2b62f00747e069f2fe46fd7d77ed39deff418b9a78dbef6`.

This closes the tested single-/multi-anchor control coverage gap for these specified synthetic signals. It does not establish new received telemetry, statistical independence, positive codec-pooling validation, real-interference false-alarm performance, or publication/deployment readiness. Both anchor campaigns reuse the original four noise traces and add zero independent negative exposure.

Graphify was used for the required project lookup, but its available graph did not contain the new Rust control artifacts. Direct artifact and source verification supplied this report's evidence.
