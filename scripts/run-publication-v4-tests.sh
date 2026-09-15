#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=/home/ubuntu/telemetry-yield
PYTHON="$PROJECT_ROOT/.venv/bin/python"
PYTEST="$PROJECT_ROOT/.venv/bin/pytest"
SOURCE_MANIFEST="$PROJECT_ROOT/reports/observation-planning-provenance-v4.json"
JUNIT="$PROJECT_ROOT/reports/pytest-planning-v4.xml"
ATTESTATION="$PROJECT_ROOT/reports/observation-planning-publication-v4/test-attestation.json"
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/src"

cd "$PROJECT_ROOT"

# Freeze the exact test subject before pytest starts.  The attestation writer
# rejects a JUnit timestamp older than this manifest and binds both artifacts.
"$PYTHON" -m telemetry_yield.cli planning-write-provenance \
  --project-root "$PROJECT_ROOT" \
  --output "$SOURCE_MANIFEST"

"$PYTEST" -q \
  tests/test_observation_planning.py \
  tests/test_planning_history_pagination.py \
  tests/test_planning_input_audit.py \
  tests/test_planning_manuscript.py \
  tests/test_planning_publication_dataset.py \
  tests/test_planning_readiness.py \
  tests/test_planning_satnogs_submit.py \
  tests/test_planning_station_inventory.py \
  tests/test_planning_v4.py \
  tests/test_planning_v4_campaign_wiring.py \
  tests/test_satnogs.py \
  --junitxml=reports/pytest-planning-v4.xml

"$PYTHON" -m telemetry_yield.planning.test_attestation \
  --project-root "$PROJECT_ROOT" \
  --source-manifest "$SOURCE_MANIFEST" \
  --junit "$JUNIT" \
  --attestation "$ATTESTATION" \
  --write
