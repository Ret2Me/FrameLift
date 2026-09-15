# Main-30 sample-rate independent audit

Verdict: **FAIL_PHYSICAL_SAMPLE_RATE_CONTRACT**. The run followed its frozen 48 kS/s plan, but that plan conflicts with hashed SatNOGS job metadata and official flowgraph source for at least 23/30 observations.

- Definite mismatches: 23/30 (all exact-metadata 9k6 jobs; official rate 57.6 kS/s).
- Additional inferred mismatches with incomplete job metadata: 3/30.
- Correctly specified 48 kS/s records: 4/30 (6365642, 5409715, 9388631, 9389746).
- Both baseline and native used 48 kS/s on every record, so same-byte pairing is intact but most physical demodulation conditions are wrong.
- The only native uplift was CELESTA 6365642, whose official rate is 48 kS/s; that narrow record-level result survives this issue. The 30-observation detection rates, transfer statistics, and null exposure do not.
- R5 must not run under the v1 rate contract. Freeze a corrected per-observation-rate plan and replay identity plus controls.

The JSON report includes every observation, hashed source provenance, formula checks across official SatNOGS flowgraph tags, exact job metadata classification, and corrected duration accounting.
