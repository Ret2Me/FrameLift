# AFSK1200 file API v1

The stable Python entry point is
`telemetry_yield.afsk1200_orchestration.run_afsk1200_file`. Its contract ID is
`telemetry-yield-afsk1200-file-api-v1`; callers may pass that value explicitly
through the `api_version` keyword or the CLI `--api-version` option. Any other
value fails before hashing IQ or starting DSP.

Inputs are a regular CI16-LE IQ file, an atomic JSON output path, an optional
checkpoint path, an optional validated-baseline ledger, and
`Afsk1200RunConfig`. The source size/hash guards, segment coordinates, profile
parameters, maximum candidate count and checkpoint identity all fail closed.

The output contract is `afsk1200-file-result-v1`, formalized in
`schemas/afsk1200-file-result-v1.schema.json`. A v1 producer may not add new
top-level keys. Incompatible changes require a new API and result schema ID;
new optional detail inside explicitly open nested research documents does not
change the acceptance semantics.

Checkpoint files use `afsk1200-file-checkpoint-v1`. A checkpoint is valid only
for the exact input content, effective configuration and external-baseline
content. Each completed window carries a digest, and resume reconstructs the
same final candidate ledger without duplicating payloads.
