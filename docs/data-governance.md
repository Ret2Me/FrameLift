# Data governance

- Raw inputs are immutable and addressed by SHA-256.
- Every derived artifact records parent hash, transform configuration hash,
  pipeline version, source URL, access time, and byte size.
- Source records preserve `license_url`, `license_text_hash`, attribution,
  redistribution permission, and publication-review state.
- `license_verified=false` or `publication_reviewed=false` blocks publication
  export in code.
- ADR 004 records user authorization for continued internal research, so those
  flags do not block bounded computation or metadata ingest.
- API tokens, Space-Track credentials, contact details, raw data, caches, and
  decoder logs are excluded from version control.
- Batches above 10 GB require a reviewed `download-plan.json`; this repository
  performs no bulk download by default.
- External contact, account creation, publication, BitTorrent, and database
  corrections require explicit user authorization.
- CAMRAS ingest additionally requires per-recording evidence for sample rate,
  Doppler state, spectral sign/component convention, and raw-to-API time basis.
  Archive-wide defaults are prohibited by ADR 001.
- The current output is a local evidence bundle, not a dataset release. A0 and
  A0.5 failures block scale even when local storage is sufficient.
