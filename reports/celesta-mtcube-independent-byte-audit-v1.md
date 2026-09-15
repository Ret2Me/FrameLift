# Independent CELESTA v2 / MTCube v1 byte audit

Overall: **PASS_WITH_CELESTA_FORMAL_FAIL_PRESERVED**.

- CELESTA v2: formal preregistered raw-KISS identity endpoint **FAIL**; independently recomputed posthoc canonical strict-frame endpoint **PASS**. Counts: baseline 18, native 123, native-only 105 across 7/7 observations, strict null accepts 0.
- MTCube v1: preregistered endpoint **PASS**. Counts: baseline 4, native 24, native-only 20 across 1/2 observations, strict null accepts 0.
- All frozen-plan, runner, dependency, profile, executable, acquisition IQ, null IQ, execution manifest, input, and output hashes were recomputed from local bytes.
- Every native accepted frame passed an independent CRC-16/X-25 and strict AX.25 UI parse. Every baseline KISS payload was independently parsed and structurally validated; its FCS is removed by the gr-satellites pipeline before KISS emission.
- Every zero and complex-sample reversal null was independently checked against the identity IQ, and all baseline/native null outputs had zero strict accepts.

The CELESTA raw-container failure is not relabelled: all seven baseline KISS repeats differ because runtime timestamp/control records differ, even though their canonical strict frame sets agree. The canonical PASS remains explicitly posthoc. MTCube's frozen plan preregistered canonical sets and declared raw KISS equality diagnostic only.

Limitations are recorded verbatim in the JSON report, principally that process stdout/stderr bytes were not retained and baseline FCS bytes are absent from the KISS output.
