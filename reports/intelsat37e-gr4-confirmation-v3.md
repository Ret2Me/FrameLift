# Intelsat 37e real-IQ confirmation v3

**Verdict: PASS.** The fail-clean v3 executable used exactly the same
physical IQ snapshot and exactly the same executable bytes for the upstream
Costas baseline and the selected Costas settings. The baseline emitted
**0** strict packets. Each
of two selected identity runs emitted **26**
unique strict IPv4/ICMP echo requests, with byte-identical output SHA-256
`6cfe0876452e46b275a173025ea36a6b2a0770f98d4b1aa097c646a2b2d2d2ef`.

All six waveform processes returned rc=0 and container exit status 0. The three
frozen waveform-destroying controls (zeros, 32-sample block permutation and
time reversal) emitted empty outputs and zero framed records. Every run has a
separate immutable-content manifest binding its exact commands, physical input
snapshot, executable, full source inventory, native closure, container digest,
stdout/stderr, output and exit state.

V3 replaces only the crashing upstream file transport with exact whole-file
preload into its existing finite `VectorSource`; modem DSP, CRC path, packet
validation and IQ bytes are unchanged. The Costas bandwidths are explicit
runtime arguments to one shared executable.

The prior v2 remains preserved as **FAIL** because its empty null outputs came
from rc=139 processes and its historical runs lacked these manifests.

This is post-development confirmation on one already exposed capture. It is
not an independent holdout, SatNOGS comparison, AX.25/CCSDS yield claim or
production deployment recommendation. A separate independent audit is still
required.

Machine-readable evidence: `reports/intelsat37e-gr4-confirmation-v3.json`.
