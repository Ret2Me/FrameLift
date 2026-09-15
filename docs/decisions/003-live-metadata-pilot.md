# ADR 003: CAMRAS–SatNOGS metadata pilot

Status: **confirmed on a bounded, stratified sample; 2026-08-31 UTC**.

The pilot read one CAMRAS index and metadata for 10 observations (four `good`,
three `bad`, three `unknown`). It downloaded no IQ or audio. The machine-readable
result is `reports/camras-satnogs-join-pilot.json`.
The exact 141,761-byte index snapshot is stored at
`manifests/camras-index-2026-08-31.html`; the report records its SHA-256 and the
SHA-256 of every API response used, making selection and summary reproducible.

## Results

- 10/10 observation IDs exist in SatNOGS Network.
- 10/10 start timestamps match exactly.
- 10/10 belong to Dwingeloo stations `PI9RD` or `PI9RD-V`.
- 10/10 have a transmitter UUID, TLE, `observation_frequency`, and historical
  `client_metadata` showing `enable-iq-dump=1`.
- 3/10 contain `demoddata`.
- 7/10 current API `status` values equal the CAMRAS index status.

The three mismatches are CAMRAS `unknown` rows that are now `good` or `bad` in
the live API. Labels therefore need accessed-at snapshots; the live API is not
an immutable historical label store.

## Contract corrections

CAMRAS `good/bad/unknown` corresponds to the observation `status` domain, not
to `waterfall_status`, whose current values in the sample are
`with-signal/without-signal`. Analyses must preserve both fields and their
timestamps instead of comparing them as identical enums.

The current API exposes `observation_frequency` directly, and `client_metadata`
contains receiver parameters such as `rx-freq`, gain, sample rate, flowgraph
version, and IQ-dump state. These are preferred evidence for historical C0.
The transmitter database is only a versioned fallback. Historical C0 is still
incomplete until the exact fields needed by each decoder are audited.

The current index parser found 467 recordings: 417 `good`, 38 `bad`, 11
`unknown`, and one incomplete row. Rounded sizes sum to about 72.4 GB and the
largest listed file is 1.4 GB. Earlier counts and the claimed ~570 MB maximum
must not be hard-coded.
