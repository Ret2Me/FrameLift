# Phase-first calibrated replay parity

## Outcome

For SatNOGS observation 12511021, the phase-first soft-list path recovers all
four retained SatNOGS payloads byte-for-byte. This is calibrated P-POS replay
parity, not blind validation and not a claim that the receiver already matches
SatNOGS across satellites or stations.

| Event | Payload | Boundary | Raw-level flips | Result |
|---|---:|---|---:|---|
| 09-09-36 | 200 B | exact left and right flags | 1 in the separated audit | CRC valid, exact payload |
| 09-09-37-1 | 250 B | left delimiter Hamming 3, exact right flag | 2 | CRC valid, exact payload |
| 09-09-37 | 101 B | exact left and right flags | 0 | CRC valid, exact payload |
| 09-13-35 | 101 B | exact left and right flags | 4 | CRC valid, exact payload |

Two decode-only runs are byte-identical. A separate post-decode process then
matches 4/4 candidates to the references. Two off-burst windows, each repeated
twice through the same candidate path, produce zero CRC-valid frames.

On a second observation, 12512778, the same front-end with independently
calibrated event timing recovers two of four payloads exactly: 200 and 101
bytes. The candidate path also finds one CRC-valid 101-byte frame that is not
the SatNOGS reference. The separate byte audit rejects it. This second result
is therefore 2/4, not 3/4, and remains P-POS rather than blind.

## What changed relative to the historical path

The retained IQ is severely clipped. Reconstructing the historically
compatible `gr-satnogs` chain exactly therefore produces zero frames even
though that decoder worked on the live, unclipped branch.

The new path changes the information used before hard slicing:

1. It projects clipped samples onto a calibrated constant-radius model.
2. It takes phase differences before the linear low-pass filter, preserving
   transition evidence that amplitude clipping largely destroys.
3. It keeps soft symbol distances for a bounded timing/threshold bank instead
   of committing immediately to one hard stream.
4. It searches low-reliability raw-level changes while accounting for the
   G3RUH descrambler's multi-bit error impulse.
5. For the hardest event, it adds positions where lag-1/lag-2 hard decisions
   disagree with lag 3. Complete HDLC unstuffing, legal length, and full
   CRC-16/X.25 are the in-decoder acceptance rules.

## Reference separation and limitations

The decode-only runner can open only frozen soft/level artifacts. Its plan has
no reference path, bytes, expected length, payload hash, or expected FCS. It
serializes CRC-valid candidates before the separate audit opens references.

This separation prevents direct payload leakage, but it does not remove the
calibration leakage: event times, timing hypotheses, lag choices, body span,
threshold order, and search budgets were tuned on the positive benchmark. The
separated campaign evaluates 68,924 structured candidate bodies; under a crude
uniform 16-bit-CRC model that is about 1.05 expected accidental CRC hits. Exact
4/4 payload equality is much stronger than CRC alone, but it is still a
post-calibration result.

## Publication and deployment gate

The result is suitable as a methods/proof-of-concept finding. A defensible
general telemetry-yield claim still requires a frozen, preregistered receiver
run over untouched observations without opening their demoddata, followed by a
separate audit. The blind runner must discover event/body boundaries rather
than receive calibrated positions and must report every CRC-valid candidate,
candidate multiplicity, false accepts per hour, and SatNOGS-relative recall.

Primary evidence:

- `reports/phase-first-soft-list.json`
- `reports/phase-first-acceptance-audit.json`
- `reports/holdout-12512778-phase-first-transfer.json`
- `work/parity-audit/decode-run1.json`
- `work/parity-audit/decode-run2.json`
- `work/parity-audit/reference-audit.json`
- `reports/tag15-faithful-front-end.json`
- `reports/iq-dump-placement-forensics.json`
