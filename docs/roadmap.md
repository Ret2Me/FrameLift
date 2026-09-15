# Qualification roadmap

[Documentation](README.md) · [Release checklist](release-checklist.md)

This is an acceptance-gate roadmap, not a delivery-date or performance promise.

| Gate | Current evidence | Exit condition |
|---|---|---|
| Reproducible receiver core | Pinned Rust/Cargo; local strict checks; task and frame regression | Repeatable clean-source build and independently exercised CI/release pipeline |
| Primary field-yield claim | Completed, exposed 266-recording CANVAS study | Freeze a new policy and evaluate untouched historical groups without tuning on outcomes |
| Technical attribution | Main portfolio gain; codec arm adds no packets to its control | Matched component ablations, common budgets and retained positive/negative results |
| False acceptance | Received-FCS and artifact checks; synthetic controls | Full-policy null/wrong-profile controls plus independent trace-back of added and missed frames |
| Broader waveform coverage | Native PSK/FEC paths and synthetic vectors | Mission-specific real IQ comparisons on independently selected recordings |
| Compute performance | CPU exactness checks; optional CUDA FIR; NVRTC compilation | Actual GPU parity, failure tests and isolated repeated end-to-end measurements |
| Operational release | Explicit failures, identity-bound resume, security/operations docs | Long-run soak, recovery/failure exercises, packaging, operator procedures and maintenance ownership |
| Scientific submission | Versioned IEEE-style manuscript and count-level bundle | Human authorship review, complete evidence audit, release arrangements and venue-specific checks |

The next scientific experiment should use old recordings that have not been
inspected during receiver development; there is no need to wait for future passes.
Freeze satellite/pass/station grouping and selection before accessing waveform
outcomes. Retain all eligible successes, failures, missing inputs and timeouts.

Do not add a more complex receiver component merely to claim novelty. A simpler
system with a reproducible, independently measured benefit is a stronger result
than an unverified new branch. Multi-station combining remains separate future work.
