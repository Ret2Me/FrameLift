# Rust receiver migration — 8 September 2026

User request: speed up decoding without accuracy loss and migrate the complete
receiver application to Rust, rather than adding Rust extensions to Python.

The receiver migration covers signal input, DSP and timing, protocol adapters,
parallel window execution, CLI, reproducible result output and batch replay /
comparison. The independent observation-planning/weather/scheduling project is
not a decoder and remains outside this receiver migration. Frozen Python
sources and historical experiment artifacts remain unchanged as scientific
reference material; the new receiver must not execute Python.

## Acceptance gates

1. Same configured windows, overlap, timing hypotheses, protocol length limits,
   checksum and validation rules; no early exit after the first decoded frame.
2. Exact per-observation PDU and original-FCS sets compared against the frozen
   93-file public OGG campaign (477 recoveries, 313 globally distinct PDUs).
   Count equality alone is insufficient. Missing/additional frames, origin
   metadata differences and floating-point score differences are separate.
3. One-worker / multi-worker result equivalence, including complete coverage
   and deterministic provenance ordering. Worker failures are not zero frames.
4. Synthetic positive, malformed, boundary and null controls, plus existing
   supported protocol contracts. Unported capabilities are explicitly listed.
5. Same PCM identity for comparisons. WAV is read natively; OGG conversion may
   use the same external FFmpeg codec as the frozen experiment, not a new
   decoder that silently changes the input samples.
6. Release-build runtime, CPU/resource limits and whole-file elapsed recorded;
   historical shared-host timings are distinguished from new matched timings.

## Work partition

- DSP/timing: native f64 frontend and exact search policy, numerical fixtures.
- Protocols: AX.25/NRZI/G3RUH/HDLC/CRC and explicit CCSDS/fixed-sync adapters.
- Audit: independent immutable-reference comparison and capability inventory.
- Integration: Rust project/CLI/input/parallel execution/batch/provenance,
  resource bounds and end-to-end measurements.

No claim of completed migration, zero loss, publication readiness or speedup
is made by this plan. Each requires its corresponding completed test artifact.
