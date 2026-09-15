# Independent audit: Intelsat 37e confirmation v2

**Verdict: FAIL (fail-closed).** The packet-content result is real and reproducible: the two
frozen 944-byte outputs are byte-identical, an independent length-framed parser consumes them
exactly, and all 26 unique datagrams pass IPv4 length/header-checksum, unfragmented ICMP echo-request
structure and ICMP-checksum validation. A fresh replay of the pinned selected binary on the pinned
IQ and container image reproduces the exact output SHA-256 `6cfe0876452e46b275a173025ea36a6b2a0770f98d4b1aa097c646a2b2d2d2ef`.

The confirmation gate nevertheless fails. The frozen plan requires each corrected-null process to
exit zero. The machine report has no exit-code field, and its builder equates an empty PDU file with
success. In an exact independent replay, zeros, the 32-sample permutation and time reversal each
created a 0-byte PDU file but returned **139**, i.e. `[139, 139, 139]`.
Therefore the claimed “exited normally” 0/0/0 control result is not supported.

A second HIGH gap is historical run provenance. No per-run record binds command, input SHA-256,
executable path/hash and exit status for baseline, selected or null runs. The current selected bytes
replay exactly, but the historical same-IQ baseline cannot be independently bound to its claimed
input/binary, and the frozen baseline audit executable is not retained at a declared path. Current
selected header and binary bytes match the frozen SHA-256 values, although their filesystem mtimes
postdate the confirmation outputs.

The failed coarse null is disclosed correctly: the guarded 4096-sample permutation contains 14
strict valid packets, all a subset of the 26 identity packets. The report also keeps the correct narrow
boundary: one post-development capture, not holdout generalization, SatNOGS superiority, or AX.25/
CCSDS telemetry gain.

Machine-readable evidence: `reports/intelsat37e-gr4-confirmation-v2-independent-audit.json`.
