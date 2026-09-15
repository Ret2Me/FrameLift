# Reproducibility checklist: RML24 beacon holdout v1.1

This checklist reproduces and audits only the RML24 physical-layer BER claim.
Passing it does not make the complete receiver publication-ready or
deployment-ready.

## 1. Obtain the exact package and data snapshot

- [ ] Work from the repository root.
- [ ] Provide the RML24 shards referenced by
  `work/nature-dataset/shards/manifest.json`; the large arrays are not embedded
  in this package.
- [ ] Confirm the local dataset manifest SHA-256 is
  `dba5fd0b840c9835967963179ba2065baefad9a32985ac33a2f6b2b63e05a2ef`.
- [ ] Retain the original file modes for frozen evidence (`0444` where recorded).

The dataset source is Zhang et al., [Scientific Data 13, article 860
(2026)](https://doi.org/10.1038/s41597-026-07182-7). It is HIL plus modeled
space-channel data, not real satellite-pass I/Q.

## 2. Verify the publication allow-list and claims

Run the fail-closed verifier without writing anything:

```bash
rtk .venv/bin/python work/rml24/build_beacon_holdout_publication_manifest_v1.py --check
```

- [ ] It exits zero and prints `status=PASS`.
- [ ] It verifies every pinned source hash and every `.sha256` sidecar.
- [ ] It reports 252 groups, 504 transfer records, and 1,008 holdout records.
- [ ] It reports W/T/L `329/221/458`.
- [ ] It reports both `system_publication_ready=false` and
  `system_deployment_ready=false`.

To reconstruct the checked-in manifest in a clean tree, write to a new path and
compare canonical bytes:

```bash
rtk .venv/bin/python work/rml24/build_beacon_holdout_publication_manifest_v1.py \
  --output /tmp/rml24-publication-manifest.json
rtk cmp /tmp/rml24-publication-manifest.json \
  reports/rml24-beacon-holdout-publication-manifest-v1.json
```

The writer uses exclusive creation. It accepts an existing destination only
when its bytes are already identical; it never overwrites a different file.

## 3. Verify the frozen public-randomness chronology

- [ ] Cohort lock SHA-256:
  `dd7d02b573a8b6fba5a1a79296709e306ebc2be8ef3c2b53eefc347aa64ecffe`.
- [ ] RFC 3161 timestamp: `2026-09-03T12:32:18Z`.
- [ ] Target NIST pulse: `2026-09-03T12:45:00.000Z`.
- [ ] Lead time: 762 seconds.
- [ ] Pulse JSON SHA-256:
  `28b780588bc034eae2b54310a59dbbe5b5873e525c127b3ca974be4fb7852fef`.
- [ ] Selection SHA-256:
  `3cba8d5cb62582984313ba1356eda08a2d3f7a7e9ecb2e48bc3825b932e643e6`.

The RFC 3161 response can be checked with the system trust store:

```bash
rtk openssl ts -verify \
  -data reports/rml24-beacon-holdout-v1-freeze-lock.json \
  -in reports/rml24-beacon-holdout-v1-freeze-lock.tsr \
  -CApath /etc/ssl/certs
```

The independent audit additionally reimplements the deployed NIST chain-2
serialization and validates the certificate, hostname, RSA/SHA-512 signature,
and output-value relation. It records that the deployed encoding is not the
exact draft NISTIR 8213 byte serialization.

## 4. Recompute the score-only audit

This step does not execute DSP or the frozen evaluator. It reconstructs the
selection from metadata and recomputes scores and statistics from the immutable
score rows:

```bash
rtk .venv/bin/python work/rml24/independent_audit_beacon_holdout_v1_1.py \
  --output /tmp/rml24-independent-audit.json
rtk cmp /tmp/rml24-independent-audit.json \
  reports/rml24-beacon-holdout-v1.1-independent-audit.json
```

- [ ] The recomputed audit status is `PASS`.
- [ ] Its SHA-256 is
  `02f9ca88341ba8193acd8f31b1e7345831299b733d6c4539bcdd3556fe9a4b72`.
- [ ] Run 1 and run 2 are byte-identical, each with SHA-256
  `1b2c78602a5b885c6a9a775186e357a664ea654ac7bc1f12fbba0d710532497c`.
- [ ] Baseline/candidate errors are 420,529 / 384,563 over 877,464
  holdout truth bits.
- [ ] BER is 0.4792549893784816 / 0.43826641320897497.
- [ ] Candidate-minus-baseline is -0.04098857616950663.
- [ ] 95% group-bootstrap CI is
  `[-0.05644367855817216, -0.026643939647130008]`.
- [ ] Paired group sign-flip p-value is `0.00000999990000099999`.
- [ ] Record W/T/L is `329/221/458`.

The audit may retrieve a public certificate issuer over HTTPS. Preserve the
stored pulse and certificate files; the checked-in audit remains the immutable
evidence if the remote issuer later becomes unavailable.

## 5. Optional full evaluator replay

A full replay is materially more expensive and requires the complete selected
RML24 shard data. Perform it only from the exact evaluator lock and write to a
fresh destination. Never overwrite either checked-in run.

```bash
rtk .venv/bin/python work/rml24/evaluate_beacon_holdout_v1_1.py evaluate \
  --selection reports/rml24-beacon-holdout-v1-selection.json \
  --lock reports/rml24-beacon-holdout-v1.1-evaluator-lock.json \
  --output /tmp/rml24-evaluation-replay.json
rtk cmp /tmp/rml24-evaluation-replay.json \
  reports/rml24-beacon-holdout-v1.1-evaluation-run1.json
```

- [ ] The runtime matches the recorded Python 3.12.3, NumPy 2.5.2, and SciPy
  1.18.1 environment if claiming exact environment reproduction.
- [ ] A byte-identical same-host result is described as deterministic replay,
  not proof of cross-runtime reproducibility.

## 6. Reporting checklist

- [ ] State “RML24 HIL plus synthetic-space-channel,” not “satellite pass.”
- [ ] State that only BPSK, GMSK, OQPSK, and QPSK were tested.
- [ ] Name micro-BER as the primary endpoint and disclose edge handling.
- [ ] Report W/T/L alongside micro-BER and explain their different weighting.
- [ ] Disclose prior dataset-wide aggregate exposure.
- [ ] Disclose the transfer-only pre-score adapter failure.
- [ ] Describe the audit as same-host agent-role separation, not external
  independence.
- [ ] Explicitly deny fully blind BER, packet yield, SatNOGS superiority,
  real-IQ generalization, and system deployment readiness.
