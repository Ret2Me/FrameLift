# Reproducing observation-planning publication v3

Run from the project root with Python 3.12 and the locked environment. The
acquisition command is resumable: rerun it after a transient error or anonymous
rate-limit response. Existing raw pages are hash-checked and not downloaded
again.

```console
PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-fetch-publication \
  --config configs/observation-planning-publication-v3.json \
  --raw-dir work/observation-planning-publication-v3/raw-v3 \
  --cache-dir work/observation-planning-publication-v3/http-cache-v3 \
  --manifest reports/observation-planning-publication-v3/snapshot-manifest-v3.json
```

Do not proceed while the snapshot manifest has `complete: false`. Build the
normalized, embedded-TLE geometry dataset and inspect only the predeclared
count gate:

```console
PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-build-dataset \
  --config configs/observation-planning-publication-v3.json \
  --snapshot-manifest reports/observation-planning-publication-v3/snapshot-manifest-v3.json \
  --raw-dir work/observation-planning-publication-v3/raw-v3 \
  --output work/observation-planning-publication-v3/normalized.jsonl \
  --manifest reports/observation-planning-publication-v3/dataset-manifest.json

jq -e '.publication_count_gate == "pass"' \
  reports/observation-planning-publication-v3/dataset-manifest.json
```

If the gate fails, report that failure and freeze a successor protocol before
changing dates or targets. The evaluation entry point independently recomputes
the gate and refuses to fit models when it fails; only the small synthetic
integration test has an explicit bypass, which is recorded in its evaluation.
If it passes, run the frozen models, replay, TLE audit and independent arithmetic audit:

The retained history demonstrates this rule: v1 failed at 217 conditional
decode rows, v2 prospectively extended 60535 and failed at 237, and v3
prospectively added filtered historical windows for 40071 and 56933 before any
performance inspection. v3 passed with 382 rows. Parent hashes and exact
count-only reasons are frozen in the v2/v3 configs.

```console
PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-evaluate-publication \
  --config configs/observation-planning-publication-v3.json \
  --dataset work/observation-planning-publication-v3/normalized.jsonl \
  --output-dir reports/observation-planning-publication-v3/analysis

PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-audit-publication \
  --dataset work/observation-planning-publication-v3/normalized.jsonl \
  --dataset-manifest reports/observation-planning-publication-v3/dataset-manifest.json \
  --evaluation reports/observation-planning-publication-v3/analysis/evaluation.json \
  --output reports/observation-planning-publication-v3/independent-audit.json

PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-write-publication-results \
  --dataset-manifest reports/observation-planning-publication-v3/dataset-manifest.json \
  --evaluation reports/observation-planning-publication-v3/analysis/evaluation.json \
  --independent-audit reports/observation-planning-publication-v3/independent-audit.json \
  --output-dir reports/observation-planning-publication-v3/manuscript
```

Capture the full verification and source identity only after source changes
have stopped:

```console
PYTHONPATH=.:src .venv/bin/pytest -q \
  --junitxml=reports/observation-planning-publication-v3/pytest-junit.xml

PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli env \
  --output reports/observation-planning-publication-v3/environment.json

PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-write-provenance --project-root . \
  --output reports/observation-planning-publication-v3/source-manifest.json

PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-publication-readiness \
  --snapshot-manifest reports/observation-planning-publication-v3/snapshot-manifest-v3.json \
  --dataset-manifest reports/observation-planning-publication-v3/dataset-manifest.json \
  --evaluation reports/observation-planning-publication-v3/analysis/evaluation.json \
  --independent-audit reports/observation-planning-publication-v3/independent-audit.json \
  --source-manifest reports/observation-planning-publication-v3/source-manifest.json \
  --results-manifest reports/observation-planning-publication-v3/manuscript/results-manifest.json \
  --junit reports/observation-planning-publication-v3/pytest-junit.xml \
  --output reports/observation-planning-publication-v3/readiness.json
```

The v4 workflow additionally requires an operational contact before any
post-acquisition API stage or campaign registration. Copy
`configs/observation-planning-api-contact-template.json` to
`work/operations/publication-api-contact.json`, replace the placeholder with a
real email address or HTTPS project URL, and attest non-commercial research use
of the configured free Open-Meteo endpoints only when accurate. The installed
five-minute resume timer starts a historical service that already stopped at
that gate. The resulting User-Agent and complete contact-file hash are frozen
into the prospective config.

The last command intentionally reports `external_release_ready: false` until a
human chooses a project code license, supplies a reachable API contact, and
reviews the SatNOGS CC BY-SA attribution/share-alike obligations. For the v4
campaign, copy `configs/observation-planning-release-attestation-template.json`
to `reports/observation-planning-publication-v4/release-attestation.json` only
after the final source manifest exists, replace every placeholder, bind the
exact dataset-config and source-identity hashes, and set each assertion to
`true`. The historical bootstrap and every prospective daily run then pass this
same file through `--release-attestation`; malformed, placeholder, unbound, or
partially approved files fail closed. The three one-shot CLI flags remain only
for reproducing older runs and cannot be combined with the persistent file.
Technical readiness and external-release readiness are separate claims. The
readiness gate also checks
that the audit embeds the exact dataset/manifest/evaluation SHA-256 values and
that the result manifest embeds the exact dataset-manifest/evaluation/audit
SHA-256 values; a passing audit copied from another run is rejected.
