# Generic telemetry recovery campaign v2

Date: 2026-09-02

## Bottom line

The project has one independently audited same-IQ improvement and no evidence
yet of broad superiority. On CANVAS observation 14366383, the source-compatible
baseline recovered six trusted AX.25 UI frames carrying exact CCSDS Space
Packets. The frozen native v2 union recovered ten, including every baseline
payload. All accepted payloads passed CRC-16/X.25, AX.25 structure and inner
CCSDS validation. Because this transfer capture had been inspected before the
v2 method was frozen, the result is labelled
`transfer_holdout_not_preregistered`, not a general benchmark win.

The wider exact same-IQ G3RUH comparisons ended in parity. For 28 captures on
which the catalogue reported no frames, the compatible branch produced 11 raw
candidates and the native branch nine; strict validation rejected all of them.
For 19 positive-reference captures, the compatible branch produced seven raw
candidates and the native branch eight; strict validation again rejected all.
Trusted yield is therefore 0:0 in both cohorts. These negative results prevent
CRC collisions or malformed short packets from being reported as telemetry.

## Full RML24 result

Both public RML24 archives were downloaded and checksum-verified. The physical
dataset contains exactly 1,323,000 records in 1,323 groups and 21 classes. The
HDF5 release contains `Bit_data` and `Bit_len` fields, but they are fill-only
with zero allocated storage and all lengths equal to zero. A bounded converter
therefore extracted the real bit truth from the Pickle release without
executing Pickle opcodes. It converted all groups, and an independent audit
validated all 2,646 NPY headers plus ten full shard hashes.

The current physical receiver supports only BPSK, GMSK, OQPSK and QPSK:
252,000 records and 219,366,000 scored bits. It made 101,721,588 errors, giving
BER 0.4637071743. The scorer is optimistic because truth chooses the best final
alignment, polarity and constellation symmetry; this result is consequently a
diagnostic upper bound, not blind performance. The other 17 classes remain
explicitly unsupported.

A preregistered 72-record diagnostic found that the full-run scorer's +/-8-bit
alignment assumption was wrong. Expanding only the final scorer to +/-1024
reduced BER from 0.464851 to 0.379539, while a wrong-record control stayed at
0.463862. At +20 dB, GMSK and QPSK at 250/500 ksym/s contain strong recoverable
information, but BPSK, OQPSK and 100-ksym/s QPSK remain weak. A separate
24-record holdout tested the article-supported RRC 0.35 frontend; its IQ-only
selector was slightly worse than legacy (0.236693 versus 0.236406). No
production DSP change or improved full-run claim was made from that null result.

## Other completed work

- The live bucket now contains 290 exact IQ objects: 257 catalogued and 33 new.
  All 33 new objects were triaged; bounded G3RUH, CW, raw-binary, AX.25 and
  CCSDS probes found no trusted telemetry in the newest three captures.
- A distinct channel-conditioned FSK plugin now filters complex IQ before phase
  differencing. It is no longer an alias of the legacy phase-first path.
- A fail-closed CCSDS TM Transfer Frame parser was added. A frame is trusted
  only with a valid FECF CRC-16 or an external integrity validator; structural
  plausibility alone is never enough.
- The RML24 safe converter exposed and fixed two real streaming hazards:
  immutable NumPy dtype memo aliasing and nested raw-byte retention. A
  100-group real-data probe held peak RSS to about 74 MiB, and the full
  conversion completed without executing the Pickle program.

## Honest conclusion and next gate

The native v2 receiver can recover additional validated telemetry on at least
one real same-IQ capture, but broad superiority over the compatible baseline is
not established. The next meaningful gate is a frozen, truth-independent
carrier/timing/equalizer pipeline on unseen RML24 records and unseen real IQ,
followed by frame-level validation. More protocol plugins increase coverage,
but unsupported waveforms must remain explicit rather than being guessed.

Primary evidence:

- `reports/phase-first-v2-14366383-holdout-audit.json`
- `reports/polyitan-iq-comparison-v2.json`
- `reports/polyitan-zero-g3ruh-official-native-comparison-v1.json`
- `reports/polyitan-positive-g3ruh-official-native-comparison-v1.json`
- `reports/rml24-physical-ber-v1.json`
- `reports/rml24-physical-ber-diagnostic-v1.json`
- `reports/rml24-physical-frontend-diagnostic-v1.json`
- `reports/polyitan-live-new-three-probe-v1.json`
