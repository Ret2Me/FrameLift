# Private SatNOGS audio analysis dispatcher

`examples/polyitan_audio_analyze.rs` consumes completed local audio downloads from
`https://polyitan.duckdns.org:8001`. It does not download files or submit telemetry.
Private observation IDs always retain this instance namespace; they are not public
SatNOGS IDs.

## Input and routing

The acquisition root contains `inventory.json` with `observations` and
`metadata_complete`, and per-observation directories `obs-ID/`. Each directory has
the complete original API object in `metadata.json`, and an atomically committed
`downloaded.json` containing `instance`, `observation_id`, and
`source: {path, sha256, bytes}`. The source path, not a guessed extension, is used.
An incomplete metadata receipt is not EOF. Sources must remain regular files in
their own acquisition directories and match their recorded byte identities.

The dispatcher handles all committed recordings, latest private ID first, with
no filter on previous decoding success or visible signal. It watches acquisition
while processing. An operational `--max-new-observations` is for smoke tests only;
omit it for the full run. The watch budget limits waiting after available work is
drained; it is not an aggregate hard deadline for all queued decoding.

Installed SatYAML is matched by unique NORAD, baud, modulation family and frequency
within 25 kHz. Missing metadata can only be resolved when one transmitter remains.
GMSK/GFSK/2FSK map explicitly to FSK; DUV maps to FSK subaudio. No ambiguous match
is silently chosen. Each selected profile preserves DSP/deframer parameters and
replaces data/transport sinks with a raw offline sink. Two disclosed supplemental
profiles fill gaps in the installed historical catalogue: the already exercised
CANVAS 437.250 MHz G3RUH/9600 contract, and ISS 145.825 MHz Mode V APRS AFSK/1200.
These mappings are not evidence that arbitrary FSK or AFSK uses AX.25.

## Receiver arms and evidence

- Frozen native generic receiver: FSK with an explicit AX.25/G3RUH profile, or
  AFSK1200 with the explicit 1200/2200 Hz Bell 202 tone contract.
- Frozen innovation v2: AX.25 G3RUH FSK9600 on original 48 kHz OGG with codec-source
  binding. This is optional per observation, not universal modulation support.
- Dire Wolf: `atest -F 0 -h`, AFSK1200 or compatible FSK rates.
- Installed gr-satellites: exactly one matched, recorded profile, real audio input.

The full older progressive bank is **not** run by this first-pass dispatcher.
Native generic plus innovations is therefore not equivalent to every previous
native pipeline combined. Original recordings remain available for deeper passes.

All active arms see the same FFmpeg bitexact PCM16 WAV: first audio stream, no
gain, resampling, cropping or downmix. Non-mono representations are explicitly
unsupported. The original OGG/WAV hash remains the primary source identity. The
innovation arm independently binds the original codec source to that exact WAV.

Native received FCS is independently rechecked and strict AX.25 UI structure is
validated. Dire Wolf and AX.25 gr-satellites strip received FCS, so their CRC
evidence is decoder-attested, not independently reconstructed. Other framing,
including CCSDS/AX100/DUV-related profiles, retains raw deframer PDU candidates;
without a dedicated downstream validator it is not labelled CRC-verified telemetry.
Unsupported branches and timeouts have null recovery counts, not fabricated zeros.
Summary candidate counts must not be conflated with verified telemetry counts.

## Durability and execution

The first run freezes native executable hashes, dispatcher identity, external
executables, profile files, configuration and namespace in `plan.json`. Native
executables are additionally checked against their previously frozen SHA-256.
This is not a complete operating-system/shared-library dependency closure.

Each arm has immutable attempt directories and a durable `committed.json` pointer.
Observation `result.json` and aggregate `summary.json` are replaceable indexes of
these receipts. Resume verifies original source/metadata identities and committed
artifact hashes. Existing outputs require `--resume`; failed attempts are retried
only with `--retry-failed`, with prior attempt evidence preserved. An exclusive
file lock prevents two dispatchers from owning one output root.

Each child has a separate owned process group, fixed wall limit, bounded output
file size and recorded wall/CPU/RSS. Timeouts are incomplete, not zero decoding.
Default concurrency is two OS workers with two native DSP threads each. At most
four workers and eight aggregate requested native threads are permitted. Timings
are shared-host concurrent costs, not isolated speed comparisons.

Run a frozen copied binary through a parent-owned systemd service so analysis
survives a tool session or agent ending. Example arguments:

```text
--acquisition /home/ubuntu/telemetry-yield/work/polyitan-audio-all-20260910-v1/acquisition
--output /home/ubuntu/telemetry-yield/work/polyitan-audio-all-20260910-v1/analysis
--workers 2 --threads 2 --decoder-seconds 900 --watch-seconds 172800
```

This operational corpus analysis is not a preregistered holdout benchmark. No
claim of improvement over the instance's archived frames is made until those
reference frames are independently normalized and compared after decoding.
