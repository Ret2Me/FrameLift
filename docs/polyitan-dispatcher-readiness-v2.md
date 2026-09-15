# Private-audio dispatcher correctness repair v2 (2026-09-11)

This is a versioned repair of benchmark orchestration and accounting, not a new
demodulator and not evidence of improved reception. No running historical service,
frozen executable, committed result, or remote data was changed.

## Independent backend contracts

`examples/polyitan_audio_analyze.rs` now selects every backend independently.
`gr_satellites` still requires a documented metadata-compatible SatYAML profile.
An acquired metadata label exactly identifying `FSK AX.25 G3RUH`, together with a
positive baud rate, can independently route the Rust generic decoder, Dire Wolf,
and (for 9600 baud / 48 kHz original OGG) the innovation probe without a satellite
definition. This is explicit metadata evidence, not a signal-derived protocol guess.
Generic FSK, GMSK, GFSK, AFSK, BPSK, and AX.100 labels do not invent AX.25 framing.

Missing/ambiguous profiles and unsupported audio are explicit per-backend states
with null recovery counts. An error or timeout remains distinct from a completed
zero-frame attempt. A backend failure does not suppress subsequent compatible
backend executions. Dire Wolf's high-rate G3RUH modem is no longer declared a
matched baseline for documented unscrambled FSK AX.25. The gr-satellites launch
does not demand a baud parameter needed only by other backends.

`native_audio` remains the historical machine-readable arm name, but v2 explicitly
identifies it as **our Rust generic audio receiver, not production SatNOGS**.
This executable still covers the generic FSK/AFSK and innovation subset, not the
complete progressive receiver bank or a native SatNOGS IQ baseline.

## Evidence tiers

Completed arms distinguish:

- `pdus` / `candidate_unique_count`: unique transport-normalized decoder output;
- `crc_passed_pdus`: received-FCS-passing AX.25-path output, independently checked
  for native full frames and only decoder-attested for external FCS-stripped output;
- `strict_ui_pdus`: that output accepted by the existing exact strict AX.25 UI
  parser; its current control-byte policy is 0x03;
- `crc_passed_non_ui_pdus`: CRC-passing output **outside that strict filter**,
  including noncanonical or other control forms, not proof of invalid telemetry;
- `protocol_unresolved_pdus`: output without the implemented strict validation.

In particular, `non_ui` in this compatibility field means *not accepted by the
existing strict UI filter*: it must not be used to claim that e.g. a legitimate
UI control byte with the P/F bit set is intrinsically non-UI. Unknown/non-AX.25
protocols retain null CRC/UI validation counts even if bytes look like AX.25.
CRC16 alone is not a universal telemetry or archive-gain certificate.

Native full-frame scoring now independently checks received FCS without aborting
on a CRC-passing non-UI body, then classifies evidence separately. The current
frozen native receiver itself still emits only strict UI; this accounting change
does not broaden its deframer or add recovered bytes.

KISS command 9 timestamps were already parsed separately from command 0 data.
The repair adds explicit transport counts, record/port/timestamp metadata and
regression tests; it does not claim to have fixed a timestamp-as-PDU bug.

## Input representation remains a scientific limitation

The shared WAV contract is still unity-gain mono PCM16 decoded from the original
OGG, with no resampling or trimming. Bitexact container flags do **not** make this
a lossless float-to-integer conversion. Vorbis overshoot outside the PCM16 range
can clip. Plans now state this limitation explicitly. A gain/float repair must
also update and verify the innovation probe's independent codec-source numeric
binding; silently changing only this dispatcher's conversion would break it.

## Verification

Frozen new executable:
`work/polyitan-dispatcher-readiness-20260911-v2/bin/polyitan_audio_analyze`.

- 33/33 normal tests passed (2 integration tests excluded from default suite).
- 2/2 explicitly invoked integration tests passed.
- Integration one constructs 13 transmitter variants across all 12 supplements
  in real offline gr-satellites on one second of 48 kHz PCM16 silence: zero PDUs.
- Integration two exercises acquisition receipts, conversion, backend routing,
  actual frozen Rust and Dire Wolf execution, commits, and summary generation on
  one second of silence **without any gr-satellites profile**. Rust and Dire Wolf
  both complete with zero frames; gr-satellites and the OGG-only innovation probe
  remain explicitly unsupported. No selected SatYAML file is created.
- These are correctness/smoke tests, not a statistically powered false-positive
  or sensitivity experiment.

Metadata-only audit of the existing 538 recordings admits 440 to at least one
backend, still 207 more than the original installed-profile policy. Preliminary
backend routing counts: gr-satellites 440, Rust generic 404, Dire Wolf 392,
innovation 302. Innovation's exact sample-rate check is reapplied after reading
the WAV. Routing coverage is **not** decoding success, and no real-recording DSP
was launched by this preflight.

Audit artifacts and hashes are recorded in
`work/polyitan-dispatcher-readiness-20260911-v2/build-and-tests.json`.

## Launch recommendation

Do not restart the old campaigns or resume v1 plans with v2. V2 uses new plan,
observation, arm and summary schemas and freezes the runner hash. Any corrected
experiment requires a new output directory and a separate readiness decision.
First resolve the positive-control IQ/OGG discrepancy, input clipping/binding,
the absent true native-SatNOGS comparator, and protocol-specific validation.
The dispatcher continues to report `publication_ready:false` and
`deployment_ready:false`; the repairs alone cannot make a paper ready.
