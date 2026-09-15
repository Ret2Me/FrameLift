# Fail-closed raw-IQ input contract

This contract is the entry gate for an arbitrary headerless IQ file supplied
by a user. It deliberately does not infer radio parameters from a filename,
file length, decoder defaults, a likely satellite, or a previous recording.
An unresolved field blocks decoding rather than selecting a plausible value.

## Minimal manifest

The JSON root uses `schema_version: raw-iq-input-v1` and exactly these groups:

- `source`: local `path`, exact `size_bytes`, and lowercase SHA-256;
- `encoding`: explicit dtype, `iq`/`qi`/native-complex layout, Q sign,
  scalar zero, scale, byte offset, and complex-sample count;
- `capture`: sample rate, RF center frequency, timezone-aware UTC start,
  named clock reference, and evidence;
- `doppler`: `pre_correction` or `post_correction` plus evidence. `unknown` is
  representable but intentionally blocks decoding;
- `satellite`: positive NORAD ID plus evidence;
- `signal`: non-unknown modulation and positive baud plus evidence;
- `clipping`: explicit lower/upper endpoints, their provenance, a declared
  maximum endpoint fraction, the fail action, and the A/B contract.

Accepted dtypes are `int8`, `uint8`, `int16_le`, `int16_be`, `float32_le`,
`float32_be`, `complex64_le`, and `complex64_be`. Native-endian aliases are
not accepted because their interpretation changes between hosts. The file
body must be exactly `complex_sample_count * 2 * scalar_bytes` after the
declared byte offset; trailing or truncated bytes fail validation.

Example skeleton (the values are illustrative, not defaults):

```json
{
  "schema_version": "raw-iq-input-v1",
  "source": {
    "path": "capture.raw",
    "sha256": "<64 lowercase hex characters>",
    "size_bytes": 2304000
  },
  "encoding": {
    "dtype": "int16_le",
    "interleaving": "iq",
    "q_sign": 1,
    "scalar_zero": 0,
    "iq_scale": 32768,
    "byte_offset": 0,
    "complex_sample_count": 576000,
    "evidence": "<recorder format declaration or byte-level evidence>"
  },
  "capture": {
    "sample_rate_hz": 57600,
    "center_frequency_hz": 437500000,
    "start_utc": "2026-09-01T12:00:00Z",
    "clock_reference": "<named clock>",
    "evidence": "<source record or measurement>"
  },
  "doppler": {
    "state": "pre_correction",
    "evidence": "<capture-tap or signal-ridge evidence>"
  },
  "satellite": {
    "norad_id": 99999,
    "evidence": "<pass and identity evidence>"
  },
  "signal": {
    "modulation": "GFSK/G3RUH",
    "baud": 9600,
    "evidence": "<transmitter specification or independent measurement>"
  },
  "clipping": {
    "lower_endpoint": -32768,
    "upper_endpoint": 32767,
    "endpoint_source": "dtype_limits",
    "max_endpoint_fraction_for_unclipped_claim": 0.001,
    "if_exceeded": "require_frozen_ab",
    "ab": {"enabled": false}
  }
}
```

Validate it without modifying data:

```bash
python -m telemetry_yield.raw_iq_manifest path/to/manifest.json
```

The command exits with status 2 when any blocker remains. Structural-only
validation is available with `--no-file-verification`, but it remains blocked
by `file_bytes_not_verified` and cannot authorize decoding.

## Clipping and identical A/B

Endpoint counts are computed from the complete declared IQ body. Integer
endpoints must exactly equal the dtype limits; floating recordings must name
the actual ADC/export limits. Non-finite float samples are blockers.

When the endpoint fraction exceeds the declared limit, either decoding is
blocked or a frozen A/B contract is required. An enabled A/B contract binds:

- both branches to the same full-file SHA-256;
- both branches to the same complex-sample window and independently repeated
  window SHA-256;
- branch A to `identity` and branch B to one named, hashed transform;
- both branches to one shared downstream configuration hash;
- a preregistered acceptance rule that cannot choose a branch per frame.

This distinguishes a clipping experiment from silently replacing the recorded
IQ with a reconstructed signal.

## Freeze before opening references

The blind freeze is a separate `raw-iq-blind-freeze-v1` JSON record. Create it
while the reference package is still sealed. It binds the canonical input
manifest hash and raw-file hash to:

1. the candidate decoder configuration hash;
2. the executed code hash;
3. the complete allowed parameter-grid hash;
4. a custodian-provided SHA-256 commitment to the sealed references;
5. position blindness, an explicit acceptance rule, negative controls, and at
   least two deterministic repeats.

Write the canonical record with `write_immutable_blind_freeze`; a different
rewrite is rejected. Validate both records with:

```bash
python -m telemetry_yield.raw_iq_manifest manifest.json \
  --blind-freeze blind-freeze.json
```

Only after the frozen candidate run, negative controls, output hashes, and two
repeat hashes have been committed should the reference package be opened.
Reference bytes, reference-derived event positions, expected lengths, and
payload/FCS values must never be decoder inputs. Full CRC plus whole-frame byte
equality is evaluated after opening; correlation remains diagnostic only.

## Claim boundary

A valid input manifest establishes that bytes and radio metadata are explicit.
A valid blind freeze establishes preregistration. Neither proves the satellite
identity, waveform description, or decoder performance; those claims still
depend on the cited evidence and the frozen holdout result.
