# Intelsat 37e real-IQ confirmation v2

**Verdict: PASS.** On exactly the same 4,773,064-byte real
Intelsat 37e recording, the pinned upstream-default receiver produced **0**
strict packets at carrier-search widths 4, 128 and 256. The selected wider
Costas-loop variant produced **26 unique packets** in each of two new
post-freeze runs. Their length-prefixed output files are byte-identical.

Every accepted record passed the modem's payload CRC path and an independent
exact-length IPv4/ICMP audit: valid IPv4 header checksum, unfragmented ICMP,
echo-request type/code and valid ICMP checksum. The 26 packets comprise 24
28-byte and 2 84-byte datagrams. Three corrected waveform-destroying controls
(zeros, 32-sample-block permutation and time reversal) exited normally and
emitted 0/0/0 records.

## Required failed-control disclosure

The preregistered 4096-sample block permutation was a bad null. Fourteen real
packets survived because a block was long enough to contain a complete packet;
these are not false decodes. The unguarded version also hit the 300-second EOF
timeout. Both failures remain in the machine report. Confirmation v2 was frozen
after that diagnosis, then reran two identity inputs and three corrected nulls.

## Claim boundary

This is a repeatable real-satellite-path positive result and a same-IQ repair of
the pinned upstream default. It is post-development confirmation on one capture,
not an independent generalization result, not SatNOGS superiority, and not an
AX.25/CCSDS telemetry-yield claim.

Machine-readable evidence: `reports/intelsat37e-gr4-confirmation-v2.json`.
