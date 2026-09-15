# FSK-family AX.25 file API v1

The stable Python entry point is
`telemetry_yield.fsk_file_orchestration.run_fsk_ax25_file`. Its contract ID is
`telemetry-yield-fsk-ax25-file-api-v1`; any other value fails before DSP. The
CLI command is `telemetry-yield decode-fsk-ax25`.

The caller must explicitly provide the IQ sample rate, symbol rate, integer
decimation and whether the G3RUH `x^17+x^12+1` descrambler is enabled. This is
a physical/protocol route, not a satellite-name shortcut. The built-in branch
covers binary FSK, GFSK and GMSK signals whose demodulated symbols follow the
same two-level link contract. Other waveform families require another plugin.

Input is a regular interleaved little-endian complex-int16 file. Optional size
and SHA-256 guards bind the attempt to exact bytes. Segment boundaries,
finite timing-rate/phase banks, overlapping window size, candidate count and
checkpoint identity all fail closed. Processing materializes one bounded IQ
window at a time and atomically checkpoints every completed window, so an
interrupted job can resume without duplicating accepted payloads.
An exactly constant complex-IQ window is an expected degenerate no-signal
state: it emits no candidate and is counted as degenerate, but is not reported
as an execution failure. Other DSP failures retain their explicit reason.

Acceptance has three layers: a complete HDLC region with valid unstuffing, a
valid CRC-16/X.25 over the complete frame, and a legal AX.25 UI address/control
structure after FCS removal. CRC-valid candidates that fail the final layer
remain in `candidates.rejected`; they are never labelled telemetry.

An optional external baseline must use `candidate-ledger-origins-v1`, be
content-bounded, refer to the same capture SHA-256 and event key, and contain
only explicitly validated baseline origins. The returned candidate ledger
then forms a deterministic union and retains all origins. A weaker native
branch therefore cannot erase a valid baseline frame.

The output contract is `fsk-ax25-file-result-v1`, formalized in
`schemas/fsk-ax25-file-result-v1.schema.json`. Checkpoints use
`fsk-ax25-file-checkpoint-v1`. Incompatible fields or acceptance changes
require a new API and schema identifier.

The frozen packaged-equivalence replay reproduced the research decoder's exact
raw and strict frame set on a known-positive real-IQ CELESTA window twice. The
complete 4 GiB qualification processed 1,789 windows at 123,985,920-byte peak
RSS with zero swap, candidates or failures. These deployment checks are
recorded in `reports/fsk-packaged-real-iq-parity-v2.json` and
`reports/fsk-4gib-streaming-v2.json`; the former is deliberately not presented
as a new blind scientific trial.
