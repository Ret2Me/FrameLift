# Full RML24 carrier/timing v2 BER replay

Date: 2026-09-02

## Result

The versioned `carrier_timing_v2` path completed the full RML24 shard manifest
with the historical alignment policy of ±8 bits.  It saw all 1,323,000 records,
scored all 252,000 records from the four implemented modulation families, and
reported the remaining 1,071,000 records as unsupported.

| Full ±8 metric | Frozen legacy | Carrier/timing v2 | Delta v2 - legacy |
|---|---:|---:|---:|
| Supported records | 252,000 | 252,000 | 0 |
| Truth bits | 219,366,000 | 219,366,000 | 0 |
| Bit errors | 101,721,588 | 101,493,256 | -228,332 |
| BER | 0.4637071743 | 0.4626663020 | -0.0010408723 |

This is a small reduction under the historical scorer, not the main evidence
for the v2 recovery gain.  The earlier preregistered wide-alignment holdout is
the evidence that v2 recovers real high-SNR sequences; its claim remains
separate and is not mixed into this full-run number.

## Critical interpretation

The full ±8 result is dominated by the scorer's known synchronization failure.
The prior preregistered diagnostic found correct offsets as large as 218 bits,
and 38/72 records needed more than eight bits.  Thus the full replay generally
cannot see the strong BPSK 250/500-ksym/s correlations recovered by v2.

OQPSK also changes from two legacy stagger hypotheses to six pre-truth
branch/boundary-pairing hypotheses.  The final oracle alignment scorer chooses
among them.  At -20 dB, where all methods are at the null floor, OQPSK alone
improves by 0.002824.  That is direct evidence that much of the full-run OQPSK
reduction is multiple-hypothesis null-floor bias, rather than recovered signal.
At +20 dB the OQPSK reduction is larger, 0.004259, so a signal-dependent
component may coexist with that bias; the full ±8 report cannot separate them.

Accordingly:

- valid claim: the versioned v2 path completed the same 1.323M-record dataset
  and produced 228,332 fewer errors under the same ±8 edge/error policy;
- invalid claim: the BER delta of 0.001041 by itself proves a receiver gain;
- stronger but separate claim: on the frozen +20 dB holdout with ±1024
  alignment, v2 improved target BER from 0.235669 to 0.149167, with paired
  bootstrap 95% interval [-0.136965, -0.042036] for v2-minus-legacy.

## Result by modulation

| Modulation | Bits | Legacy errors | v2 errors | Error delta | Legacy BER | v2 BER |
|---|---:|---:|---:|---:|---:|---:|
| BPSK | 36,561,000 | 16,893,802 | 16,888,356 | -5,446 | 0.4620716611 | 0.4619227045 |
| GMSK | 36,561,000 | 16,551,323 | 16,551,323 | 0 | 0.4527043298 | 0.4527043298 |
| OQPSK | 73,122,000 | 34,018,702 | 33,797,415 | -221,287 | 0.4652321052 | 0.4622058341 |
| QPSK | 73,122,000 | 34,257,761 | 34,256,162 | -1,599 | 0.4685014223 | 0.4684795547 |

GMSK is bit-for-bit identical, as required by the control.  OQPSK accounts for
96.9% of the total error-count reduction.  Across the 252
modulation/SNR/rate cells, 125 improve, 63 regress and 64 tie; all 63 GMSK
cells are among the ties.  The largest cell improvement is OQPSK +20 dB / 500
ksym/s (-9,693 errors); the largest regression is BPSK -2 dB / 500 ksym/s
(+857 errors).

## Selected SNR slices

| SNR | Legacy BER | v2 BER | Delta |
|---:|---:|---:|---:|
| -20 dB | 0.4655328355 | 0.4645026805 | -0.0010301551 |
| 0 dB | 0.4648454911 | 0.4637981045 | -0.0010473866 |
| +20 dB | 0.4606635076 | 0.4591703044 | -0.0014932031 |

The similar reduction at -20 and 0 dB is another warning that the aggregate is
affected by final-scorer hypothesis multiplicity.  Only the separately frozen
wrong-record controls and wide-alignment holdout distinguish recovered signal
from that floor.

## +20 dB by rate

| Modulation | Rate | Legacy BER | v2 BER | Delta |
|---|---:|---:|---:|---:|
| BPSK | 100k | 0.437995 | 0.437937 | -0.000059 |
| BPSK | 250k | 0.457703 | 0.458045 | +0.000342 |
| BPSK | 500k | 0.469429 | 0.468503 | -0.000926 |
| OQPSK | 100k | 0.442651 | 0.438676 | -0.003976 |
| OQPSK | 250k | 0.461861 | 0.458437 | -0.003425 |
| OQPSK | 500k | 0.468628 | 0.463896 | -0.004733 |
| QPSK | 100k | 0.446920 | 0.446900 | -0.000020 |
| QPSK | 250k | 0.464668 | 0.464678 | +0.000010 |
| QPSK | 500k | 0.474671 | 0.474678 | +0.000007 |
| GMSK | 100k | 0.398995 | 0.398995 | 0.000000 |
| GMSK | 250k | 0.427863 | 0.427863 | 0.000000 |
| GMSK | 500k | 0.450230 | 0.450230 | 0.000000 |

## Alignment policies kept separate

The JSON in this report uses only:

- `max_shift_bits = 8`;
- global BPSK/FSK polarity ambiguity;
- QPSK/OQPSK D4 ambiguity;
- missing aligned edges counted as errors.

A full ±1024 replay was not run.  It would test 2,049 shifts instead of 17 for
BPSK/GMSK and 1,025 symbol shifts instead of nine for QPSK/OQPSK—approximately
114-121 times more alignment positions per candidate.  The completed ±8 replay
already used 23:21 wall time, so the wide scorer was judged inappropriate for
an unplanned full run.  Existing ±1024 results remain explicitly labelled as
bounded record-level diagnostics/holdout, never as a full-dataset BER.

## Versioned invocation and default

The CLI now exposes an explicit choice:

```bash
rtk .venv/bin/telemetry-yield benchmark-rml24-physical \
  work/nature-dataset/shards/manifest.json \
  --output reports/rml24-physical-ber-carrier-timing-v2-v1.json \
  --batch-size 32 \
  --sample-rate 1000000 \
  --max-shift-bits 8 \
  --recovery-version carrier_timing_v2
```

The report records `recovery_version=carrier_timing_v2` and adapter name
`rml24-carrier-timing-v2-v1`.  Omitting the option still selects `legacy`, so
existing invocations and frozen reports do not silently change meaning.  The
same option is available for the HDF5 physical benchmark command.

## Coverage, resources and integrity

- Status: `complete_supported_subset`.
- Records seen: 1,323,000 / 1,323,000.
- Supported: 252,000; unsupported: 1,071,000.
- Supported cell count: 252.
- Cell sums exactly equal the reported 101,493,256 errors and 219,366,000 bits.
- Legacy and v2 have identical record, bit, supported and per-class unsupported
  coverage.
- Source uses strict paired NPY memory maps; Pickle execution is false.
- Wall time: 23:21.06; user CPU: 1,187.71 s; system CPU: 213.15 s.
- Peak RSS: 70,064 KiB; swaps: 0; major page faults: 0.
- No AMR or validated telemetry-frame yield is claimed by this dataset report.

Focused verification:

```bash
rtk .venv/bin/pytest -q \
  tests/test_cli.py \
  tests/test_rml24_physical_plugin.py \
  tests/test_rml24_physical_diagnostic.py
```

Result: `28 passed in 0.85s` after the full run.  The versioned CLI test uses a
real one-record BPSK group shard and verifies the recorded recovery version,
adapter name and alignment policy.

## Provenance

- Full v2 JSON SHA-256:
  `e0805a0994b914559939d0017917c42cad7c69be6dbb3c01a51b24e4f8b0f227`
- Frozen legacy JSON SHA-256:
  `91a21e0b1f79be4c845cc09b13d060f43d22ea062d15606418ce621a113d093e`
- Evaluated physical plugin SHA-256:
  `c2ffb75a8ff03098f08e604c1170434dffa931f0a262c9e7d473c2c051f3327d`
- Versioned CLI SHA-256:
  `22600501e16c8de6f54abe7183d226b6bde71553ba5e47d3c82b322966716d6c`
- Source manifest SHA-256:
  `dba5fd0b840c9835967963179ba2065baefad9a32985ac33a2f6b2b63e05a2ef`
- Source archive SHA-256:
  `daac87c1ce1b10c986c0a2bd6eaf5666316e629f45d53592fc76444bf3614c1d`
- Frozen v2 configuration SHA-256:
  `8ab1e87411ecb11e21911257e36ecef750d02f1e3ef56be67459796085333acc`
- Preregistered holdout JSON SHA-256:
  `7974fddd6ee4d14afe95aa864f2ff27270d3fb5efd2f883a18d1bdca1f4b047a`

The existing `reports/rml24-physical-ber-v1.json` was read for comparison and
was not overwritten.
