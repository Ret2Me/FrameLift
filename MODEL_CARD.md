# Model card: no model released

No machine-learning model has been trained or released. This is an intentional
gate result, not missing documentation.

The only permitted future role is the decision layer: ranking classical
decoder configurations or deciding when to stop. A model must never generate,
repair, or accept payload bits. The classical CRC-validated signal path remains
authoritative.

The observation planner contains an explicitly versioned, untrained
hierarchical-Beta/link-budget baseline for cold-start reception probability.
It is a scheduling estimator, not a released machine-learning model; it reports
missing features and uncertainty and never participates in frame acceptance.

Training, evaluation, calibration, leakage analysis,
`frames_per_cpu_second`, `false_accepts_per_hour`, and baseline comparisons are
all **not applicable** until A0/A0.5 correctness gates pass and a reviewed,
event-split dataset exists. Current negative results are documented in
`docs/gates.md`.
