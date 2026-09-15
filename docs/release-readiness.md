# Publication and deployment readiness

The project does not equate a lower diagnostic BER or one successful capture
with a releasable receiver. `telemetry_yield.release_readiness` evaluates three
independent evidence groups and fails closed when a required field is missing
or malformed.

## Research evidence

A release claim requires an independent, position-blind and oracle-free
holdout; an executable baseline on exactly the same IQ; at least 30 independent
observations from three missions and 30 strictly validated frames; an
appropriate paired confidence interval; enough negative-control exposure for
the one-sided 95% false-accept upper bound to be at most 0.1/hour; and complete
source, configuration, environment and repeatability provenance.

Superiority and incremental-union claims require the lower confidence bound of
the gain to exceed zero. A noninferiority claim may use an explicitly configured
margin. CRC alone is not strict validation: accepted frames require both link
integrity and legal protocol structure.

## Deployment evidence

The production path must stream an input of at least 4 GiB within 512 MiB peak
RSS, never load benchmark truth, resume idempotently, reject malformed inputs
and unknown profiles, expose versioned API/output contracts, isolate external
decoder processes, and pass the full, golden-protocol and deterministic replay
test suites.

## Publication package

Methods and limitations must match executable artifacts. A data card,
immutable artifact manifest and independent reproduction are mandatory. Source
permission attestation, attribution and a secret scan remain explicit human
release checks; they do not block internal technical research.

The current evidence and computed blockers live in
`reports/release-evidence-current-v1.json` and
`reports/release-readiness-current-v1.json`. They must be updated from measured
artifacts rather than by changing thresholds to fit a result.
