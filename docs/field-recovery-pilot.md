# Historical field-recovery coverage pilot

This pilot separates original mono OGG recordings from original complex IQ.
It does not treat post-FM audio as recoverable IQ and does not transfer an IQ
algorithm's claims to the audio receiver.

## Explicit mission bindings

| Mission and downlink | Physical and frame binding | Comparator profile |
|---|---|---|
| ISS APRS, transmitter `ZJxCeQmih9zDfYNVrB4wRN` | 145.825 MHz, Bell-202 AFSK 1200 baud, NRZI AX.25 | `configs/protocols/field-iss-afsk1200.yml` |
| SONATE-2 telemetry, transmitter `Fo5WYJpLBKNyNQyqiuNXnw` | 437.025 MHz, GMSK 9600 baud, AX.25/G3RUH | `configs/protocols/field-sonate-gmsk9600.yml` |
| CANVAS AstroDev Li-2, transmitter `GCmN6RULea8dAT7Qoat8z2` | 437.250 MHz, GMSK 9600 baud; explicit AX.25/G3RUH engineering binding | `configs/protocols/field-canvas-gmsk9600.yml` |

ISS packet operation is documented by the
[APRS project](https://www.aprs.org/iss-faq.html).
SONATE-2's exact GMSK/AX.25-G3RUH downlink is named in the
[SatNOGS transmitter catalogue](https://db.satnogs.org/satellite/PBTZ-3525-0189-2410-5216/),
which links the mission operator's radio-amateur information.
The [CANVAS catalogue](https://db.satnogs.org/satellite/GLFY-8231-7125-4883-6529/)
establishes radio, rate and frequency, and its telemetry schema establishes an
AX.25 envelope. It does not independently specify every G3RUH line-coding
detail: the latter is the previously exercised receiver binding, not a new
operator-certified protocol definition. These profiles do not claim support
for the missions' other transmitters.

The external YAML uses gr-satellites' binary `FSK` demodulator for GMSK,
consistent with that implementation's interface. The IQ comparator explicitly
uses a 2400 Hz deviation (h=0.5 at 9600 baud), not its generic 5000 Hz default.
This is a parameterized existing gr-satellites receiver, not a new coherent CPM
implementation. Both external receivers run offline, without telemetry uploads.

## Selection and accounting

`field_recovery_benchmark freeze` admits exactly four downloadable OGG URLs
per mission from one saved API page for 2026-06-01. It ranks eligible IDs by a
fixed SHA-256 rule independent of decode outcomes. Pagination is deliberately
not exhausted: this is a small page-prefix coverage pilot, not a representative
population sample. Metadata, source URLs and selected IDs are retained before
waveform access. All acquisition and decoder failures remain in the denominator.

Archive `waterfall_status` determines the signal-labelled subgroup before the
experiment. Unknown does not mean no signal; `with-signal` does not establish
transmitter identity or error-free payload ground truth. Existing local IQ is
explicitly development data. Freshly downloaded OGG is `exposure: unknown`
until a source-lineage exposure audit proves otherwise. New download is not
proof of independence.

## Receivers and numerical inputs

The audio runner converts each original OGG once to a common PCM16 WAV without
per-arm resampling or gain adjustment. It then executes old and new FrameLift
`decode-audio`, Dire Wolf `atest -F 0 -h`, and the explicitly profiled
gr-satellites receiver. Native full-bank audio is not the larger progressive
portfolio; this comparison cannot be substituted for that portfolio's benchmark.

The IQ runner processes the two complete archived CANVAS files in fixed
16-second windows with 15-second hops. No crop is selected after looking at
frames. It compares the unchanged generic receiver, the new HDLC baseline,
the additive validated-anchor BCJR lane, and gr-satellites. Windows are combined
into recording-level frame sets. They are never treated as independent samples.
The original CI16 bytes are identical for every arm; gr-satellites internally
converts them to float32 divided by 32767, while FrameLift reads integer-valued
float64. Thus original-source identity, not identical intermediate floating-point
arrays, is asserted for IQ. No synthetic padding, resampling or payload truth
enters the input.

New HDLC recovery currently tests the variable-frame, anchor-trained sequence
lane. It does not yet connect coherent CPM, repetition combination or SIC to
arbitrary HDLC traffic. Training spans are excluded from supplemental acceptance.
When no validated training anchor exists, this lane cannot add packets.

Native frames retain received FCS bytes and undergo independent CRC and AX.25
UI structure checks. External KISS/hexdump payloads lack received FCS, so their
integrity is decoder-attested, not independently rechecked. The common endpoint
is unique strict AX.25 UI payloads per recording, excluding FCS; non-UI external
packets remain in raw logs but are outside this endpoint. CRC acceptance alone
does not establish true transmitted payloads or a population false-alarm rate.

## Reproducibility and limits

Each run freezes executable hashes, profiles, cohort and source identities,
exact arguments, common-input hashes, wall time and user/system CPU time.
Frame sets, failed arms and raw external logs are retained. Installed dynamic
dependencies are version-audited but not a sealed runtime closure. Consequently
these are transparent development receipts, not a hermetic publication release.
Concurrent host load means timing is descriptive, not an isolated speed claim.
Native in-process IQ time and external process time have different startup
boundaries and are labelled accordingly.

The paired study receipts are accepted by `summarize-recovery-study`; reports
must always accompany the selected-input denominator. A small pilot may expose
regressions or profile mistakes, but cannot establish broad superiority.
