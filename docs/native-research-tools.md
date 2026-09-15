# Native research tools

`framelift-research` provides offline research primitives in Rust. It does not
call Python, a shell, a network client or a database executable. SQLite is
compiled from the bundled dependency. This tool is not a replacement for every
command in the historical Python CLI; see the [migration status](rust-migration-status.md).

```sh
cargo build --locked --release --bin framelift-research
target/release/framelift-research --help
target/release/framelift-research metrics --input frames.json
```

All commands emit one JSON document on success. Invalid input produces a nonzero
exit status and a diagnostic on stderr, without a success document on stdout.
Input paths must identify regular files, not final-component symlinks. JSON
requests are limited to 16 MiB. Typed request envelopes reject unknown fields
and duplicate fields; opaque nested metadata objects use normal JSON map semantics.
The CLI does not silently fetch missing data.

## Commands and trust boundaries

| Command | Input | Result and limitation |
|---|---|---|
| `metrics` | Frame records, optional CPU time and negative-control exposure | Unique `(transmission_event_id, payload_sha256)` keys; trusts supplied CRC flags |
| `check-splits` | Array of `[event_id, split_name]` pairs | Rejects an event in multiple splits; repeats inside one split are allowed |
| `same-event` | Two events, time tolerance and optional Doppler-consistency flag | Requires compatible identity, temporal overlap and shared payload or supplied Doppler evidence; does not establish spacecraft identity |
| `export-gate` | Array of provenance records | Requires nonempty, verified and publication-reviewed records; does not independently establish permissions |
| `config-hash` | Effective decoder configuration | SHA-256 of canonical JSON, including decoder identity, version, seed and all parameters |
| `derive-selection-salt` | Domain, preregistration hash, beacon output | Domain-separated SHA-256 salt; does not authenticate the beacon |
| `validate-beacon` | Pulse document, expected time, certificate PEM | Exact pulse metadata and DER hash binding; **does not verify the pulse signature or certificate trust** |
| `select-cohort` | Catalogue, download plan, eligibility rules and salt | Outcome-blind projection, deterministic rank and satellite-diversity caps; does not download recordings |
| `audit-pcap` | Classic PCAP file | Strict complete IPv4/ICMP echo requests, checksums and byte identities; not arbitrary packet or spacecraft authentication |
| `audit-ipv4-pdus` | Big-endian uint32-length-prefixed packets | Same IPv4/ICMP checks, deduplicated inventory |
| `attempts` | Operation JSON and `--database attempts.sqlite` | Atomic reservation, lease-guarded completion or read-only inspection |

### Frame metrics

```json
{
  "frames": [
    {"transmission_event_id": "pass-1", "payload_sha256": "digest-a", "crc_valid": true},
    {"transmission_event_id": "pass-1", "payload_sha256": "digest-a", "crc_valid": true}
  ],
  "cpu_seconds": 2.0,
  "negative": {"n_frames_on_negative": 3, "total_hours_negative": 1.5}
}
```

This counts one distinct frame, 0.5 frames/CPU-second and 2 false accepts/hour.
Identical bytes in different transmission events count separately. The diagnostic
`input_crc_flags_independently_verified` remains false; this is accounting, not
an independent CRC audit. Zero, negative and nonfinite exposure are rejected.

### Cohort selection

```json
{
  "catalogue": {"observations": [
    {"observation_id": 7, "satellite_id": "example", "mode": "FSK",
     "frequency_hz": 435000000, "start": "2026-01-01T00:00:00Z",
     "end": "2026-01-01T00:01:00Z", "frame_count": 123}
  ]},
  "download_plan": {"candidates": [
    {"observation_id": 7, "key": "observation_7.iq", "size_bytes": 400,
     "url": "https://example.invalid/observation_7.iq"}
  ]},
  "eligible_modes": ["FSK"],
  "development_exclusions": [],
  "selection_salt_hex": "6161616161616161616161616161616161616161616161616161616161616161",
  "target_observations": 1,
  "maximum_per_satellite": 1,
  "minimum_satellites": 1
}
```

`frame_count` and other decoder outcomes are removed before ranking. The example
salt is a fixture, not a preregistration. Operational studies must freeze their
population, exclusions and diversity rules before observing decoder outcomes.
The tool enforces those supplied rules; it cannot prove they were chosen blindly.

### Durable attempts

```sh
target/release/framelift-research attempts \
  --database attempts.sqlite --input reserve.json
```

```json
{
  "action": "reserve",
  "recording_id": "pass-1",
  "config_hash": "effective-config-sha256",
  "config": {"baud": 9600},
  "stale_after_seconds": 3600
}
```

Keep the returned `lease_token` and use it for completion:

```json
{
  "action": "finish",
  "recording_id": "pass-1",
  "config_hash": "effective-config-sha256",
  "status": "ok",
  "result": {"frames": 3},
  "lease_token": "the-token-returned-by-reserve"
}
```

The store accepts `ok`, `error` and `timeout` as terminal states. Only an expired
`running` reservation can be reclaimed; a former lease cannot finish it afterward.
Terminal attempts cannot be reserved again. The caller is responsible for matching
the supplied config hash to its actual parameters. Use the `config-hash` command
when generating that identity; the example label above is not a real digest.

`{"action":"get","recording_id":"pass-1","config_hash":"effective-config-sha256"}`
inspects the existing database without creating a missing database. Parent
directories must exist. WAL databases belong on a suitable local filesystem,
not a shared network filesystem. These are local research leases, not a
multi-tenant authorization system. The legacy SQLite table layout is retained.

## Compatibility and verification

The salt, integer rank and configuration fingerprint tests include literal
outputs from the Python reference. Cohort tests also freeze the selected ID
order and test invariance to catalogue order and decoder outcomes. PCAP tests
cover both byte orders and both timestamp resolutions, cooked-v2 framing,
duplicates, invalid checksums and truncation. CLI integration tests run with an
empty `PATH`; concurrency tests require exactly one winner among 16 workers.

Deliberately stricter native boundaries: finite rates, UTC event timestamps,
nonnegative lease expiry intervals, representable Rust integers, regular-file
inputs, input-size limits and valid PCAP timestamp fractions. Positive IDs are
`u64`, seeds are `i64`, cohort counts are `usize`. These bounds are not a claim
of equivalence for Python's arbitrary-precision integer inputs. Packet files
are limited to 256 MiB and one million records per audit. Beacon certificate
PEM content is limited to 1 MiB. Checksums detect corruption, not authenticity.

```sh
cargo test --locked --lib research::
cargo test --locked --bin framelift-research --test research_cli
cargo xtask check
```

The scientific paper, frozen packet sets and published recovery figures are
unchanged by these ports. Passing these checks is not a new satellite benchmark.
