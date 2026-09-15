#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=/home/ubuntu/telemetry-yield
RUN_ROOT="$PROJECT_ROOT/work/prospective-v4g"
REPORT_ROOT="$PROJECT_ROOT/reports/observation-planning-prospective-v4g"
HISTORICAL_RUN_ROOT="$PROJECT_ROOT/work/observation-planning-publication-v4"
HISTORICAL_REPORT_ROOT="$PROJECT_ROOT/reports/observation-planning-publication-v4"
TEMPLATE="$PROJECT_ROOT/configs/prospective/observation-planning-shadow-v4g-template.json"
CONFIG="$RUN_ROOT/config.json"
HISTORY_DATASET="$HISTORICAL_RUN_ROOT/normalized-enriched.jsonl"
DATASET_MANIFEST="$HISTORICAL_REPORT_ROOT/dataset-manifest.json"
EVALUATION="$HISTORICAL_REPORT_ROOT/analysis/evaluation.json"
INDEPENDENT_AUDIT="$HISTORICAL_REPORT_ROOT/independent-audit.json"
PROBABILITY_MODEL="$HISTORICAL_REPORT_ROOT/deployment/probability-model.json"
RELEASE_ATTESTATION="$HISTORICAL_REPORT_ROOT/release-attestation.json"
TEST_ATTESTATION="$HISTORICAL_REPORT_ROOT/test-attestation.json"
API_CONTACT="$PROJECT_ROOT/work/operations/publication-api-contact.json"
BLOCKER_TEMPLATE="$PROJECT_ROOT/configs/prospective/observation-planning-blockers-v4g.json"
BLOCKER_CONTROL="$RUN_ROOT/blockers.json"
TARGET_OVERRIDE_TEMPLATE="$PROJECT_ROOT/configs/prospective/observation-planning-target-overrides-v4g.json"
TARGET_OVERRIDE_CONTROL="$RUN_ROOT/target-overrides.json"
RUN_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NOW_EPOCH=$(date -u +%s)
PYTHON="$PROJECT_ROOT/.venv/bin/python"
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/src"

mkdir -p "$RUN_ROOT/forecasts" "$RUN_ROOT/plans" "$RUN_ROOT/reconciliation" \
  "$RUN_ROOT/source-manifests" "$RUN_ROOT/covariate-evidence" \
  "$RUN_ROOT/blocker-snapshots" "$RUN_ROOT/target-override-snapshots" \
  "$REPORT_ROOT"
exec 9>"$RUN_ROOT/daily-run.lock"
flock -n 9

RUNTIME_USER_AGENT=$("$PYTHON" -m telemetry_yield.planning.api_contact \
  --contact-file "$API_CONTACT")

# Materialization is idempotent. On the first successful call it chooses the
# first UTC midnight at least 48 hours away; later calls verify every frozen
# dependency byte-for-byte and refuse to rewrite the registered protocol.
"$PYTHON" -m telemetry_yield.planning.campaign_materialization \
  --project-root "$PROJECT_ROOT" \
  --template "$TEMPLATE" \
  --output "$CONFIG" \
  --snapshot-manifest "$HISTORICAL_REPORT_ROOT/snapshot-manifest.json" \
  --history-dataset "$HISTORY_DATASET" \
  --dataset-manifest "$DATASET_MANIFEST" \
  --evaluation "$EVALUATION" \
  --independent-audit "$INDEPENDENT_AUDIT" \
  --probability-model "$PROBABILITY_MODEL" \
  --api-contact "$API_CONTACT"

# These redundant local gates remain before reconciliation, forecast retrieval,
# or ledger initialization. A partial or replaced cohort must never launch the
# prospective campaign.
test -s "$HISTORY_DATASET"
jq -e '.complete == true' "$HISTORICAL_REPORT_ROOT/snapshot-manifest.json" >/dev/null
jq -e '.publication_count_gate == "pass"' "$DATASET_MANIFEST" >/dev/null
jq -e '.schema_version == "observation-planning-evaluation-v1" and .publication_count_gate_enforced == true' "$EVALUATION" >/dev/null
jq -e '.passed == true and .failed_check_count == 0' "$INDEPENDENT_AUDIT" >/dev/null
jq -e --arg start "$(jq -er '.start' "$CONFIG")" '.schema_version == "planning-learned-probability-model-v1" and .training_data_end < $start' "$PROBABILITY_MODEL" >/dev/null
if [[ ! -e "$BLOCKER_CONTROL" ]]; then
  install -m 0644 "$BLOCKER_TEMPLATE" "$BLOCKER_CONTROL"
fi
jq -e '.schema_version == "observation-planning-blockers-v1" and (.blockers | type == "array")' "$BLOCKER_CONTROL" >/dev/null
if [[ ! -e "$TARGET_OVERRIDE_CONTROL" ]]; then
  install -m 0644 "$TARGET_OVERRIDE_TEMPLATE" "$TARGET_OVERRIDE_CONTROL"
fi
jq -e '.schema_version == "observation-planning-target-overrides-v1" and (.targets | type == "array")' "$TARGET_OVERRIDE_CONTROL" >/dev/null

CAMPAIGN_END=$(jq -er '.end' "$CONFIG")
CAMPAIGN_END_EPOCH=$(date -u -d "$CAMPAIGN_END" +%s)
RECONCILIATION_STOP_EPOCH=$(date -u -d "$CAMPAIGN_END + 2 days" +%s)

if (( NOW_EPOCH >= RECONCILIATION_STOP_EPOCH )); then
  "$PYTHON" -m telemetry_yield.cli planning-prospective-report \
    --config "$CONFIG" \
    --ledger "$RUN_ROOT/ledger.jsonl" \
    --output "$REPORT_ROOT/status.json"
  /usr/bin/systemctl --user disable --now telemetry-yield-prospective-v4g.timer
  exit 0
fi

HARDWARE_ARGS=()
if [[ -f "$RUN_ROOT/local-hardware.json" ]]; then
  HARDWARE_ARGS=(--hardware-archive "$RUN_ROOT/local-hardware.json")
fi
if [[ ! -s "$RUN_ROOT/ledger.jsonl" ]]; then
  "$PYTHON" -m telemetry_yield.cli planning-prospective-init \
    --config "$CONFIG" \
    --ledger "$RUN_ROOT/ledger.jsonl"
fi

"$PYTHON" -m telemetry_yield.cli planning-prospective-reconcile-shadow \
  --config "$CONFIG" \
  --ledger "$RUN_ROOT/ledger.jsonl" \
  --raw-snapshot "$RUN_ROOT/reconciliation/network-$RUN_STAMP.json" \
  --outcomes "$RUN_ROOT/reconciliation/outcomes-$RUN_STAMP.json" \
  --cache-dir "$RUN_ROOT/reconciliation-http-cache" \
  --user-agent "$RUNTIME_USER_AGENT" \
  --lookback-hours 168 \
  --shared-rate-limit-file "$PROJECT_ROOT/work/satnogs-observations-anonymous.rate"

if (( NOW_EPOCH < CAMPAIGN_END_EPOCH )); then
  "$PYTHON" -m telemetry_yield.cli planning-fetch-forecast \
    --config "$CONFIG" \
    --output "$RUN_ROOT/forecasts/forecast-$RUN_STAMP.json" \
    --cache-dir "$RUN_ROOT/forecast-http-cache" \
    --user-agent "$RUNTIME_USER_AGENT" \
    "${HARDWARE_ARGS[@]}"

  COVARIATE_EVIDENCE="$RUN_ROOT/covariate-evidence/evidence-$RUN_STAMP.json"
  "$PYTHON" -m telemetry_yield.cli planning-write-covariate-evidence \
    --covariates "$RUN_ROOT/forecasts/forecast-$RUN_STAMP.json" \
    --cache-dir "$RUN_ROOT/forecast-http-cache" \
    --output "$COVARIATE_EVIDENCE"

  SOURCE_MANIFEST="$RUN_ROOT/source-manifests/source-$RUN_STAMP.json"
  "$PYTHON" -m telemetry_yield.cli planning-write-provenance \
    --project-root "$PROJECT_ROOT" \
    --output "$SOURCE_MANIFEST"

  BLOCKER_SNAPSHOT="$RUN_ROOT/blocker-snapshots/blockers-$RUN_STAMP.json"
  if [[ ! -e "$BLOCKER_SNAPSHOT" ]]; then
    install -m 0444 "$BLOCKER_CONTROL" "$BLOCKER_SNAPSHOT"
  fi
  cmp -s "$BLOCKER_CONTROL" "$BLOCKER_SNAPSHOT"
  jq -e '.schema_version == "observation-planning-blockers-v1" and (.blockers | type == "array")' "$BLOCKER_SNAPSHOT" >/dev/null
  BLOCKER_ARGS=(--blockers "$BLOCKER_SNAPSHOT")

  TARGET_OVERRIDE_SNAPSHOT="$RUN_ROOT/target-override-snapshots/target-overrides-$RUN_STAMP.json"
  if [[ ! -e "$TARGET_OVERRIDE_SNAPSHOT" ]]; then
    install -m 0444 "$TARGET_OVERRIDE_CONTROL" "$TARGET_OVERRIDE_SNAPSHOT"
  fi
  cmp -s "$TARGET_OVERRIDE_CONTROL" "$TARGET_OVERRIDE_SNAPSHOT"
  jq -e '.schema_version == "observation-planning-target-overrides-v1" and (.targets | type == "array")' "$TARGET_OVERRIDE_SNAPSHOT" >/dev/null
  TARGET_ARGS=(--target-overrides "$TARGET_OVERRIDE_SNAPSHOT")

  "$PYTHON" -m telemetry_yield.cli planning-prospective-run-shadow \
    --config "$CONFIG" \
    --publication-config "$PROJECT_ROOT/configs/observation-planning-prospective-target-pool-v4g.json" \
    --transmitters "$PROJECT_ROOT/work/a1/satnogs-transmitters-active-direct-2026-08-31.json" \
    --covariates "$RUN_ROOT/forecasts/forecast-$RUN_STAMP.json" \
    --history-dataset "$HISTORY_DATASET" \
    --probability-model "$PROBABILITY_MODEL" \
    --ledger "$RUN_ROOT/ledger.jsonl" \
    --plan "$RUN_ROOT/plans/plan-$RUN_STAMP.json" \
    --state-db "$RUN_ROOT/planning.sqlite3" \
    --cache-dir "$RUN_ROOT/run-http-cache" \
    --user-agent "$RUNTIME_USER_AGENT" \
    --source-manifest "$SOURCE_MANIFEST" \
    --input-artifact "$COVARIATE_EVIDENCE" \
    --input-artifact "$DATASET_MANIFEST" \
    --input-artifact "$EVALUATION" \
    --input-artifact "$INDEPENDENT_AUDIT" \
    --input-artifact "$API_CONTACT" \
    --input-artifact "$PROJECT_ROOT/configs/observation-planning-prospective-station-selection-v4f.json" \
    --input-artifact "$PROJECT_ROOT/work/prospective-v4c/station-selection/antenna-evidence-20260903T180500Z.json" \
    --input-artifact "$PROJECT_ROOT/work/prospective-v4f/station-selection/antenna-evidence-20260903T192000Z.json" \
    "${BLOCKER_ARGS[@]}" \
    "${TARGET_ARGS[@]}"
fi

"$PYTHON" -m telemetry_yield.cli planning-prospective-report \
  --config "$CONFIG" \
  --ledger "$RUN_ROOT/ledger.jsonl" \
  --output "$REPORT_ROOT/status.json"

release_attestation_args=()
if [[ -s "$RELEASE_ATTESTATION" ]]; then
  release_attestation_args=(--release-attestation "$RELEASE_ATTESTATION")
fi

"$PYTHON" -m telemetry_yield.cli planning-publication-readiness \
  --snapshot-manifest "$HISTORICAL_REPORT_ROOT/snapshot-manifest.json" \
  --dataset-manifest "$DATASET_MANIFEST" \
  --evaluation "$EVALUATION" \
  --independent-audit "$INDEPENDENT_AUDIT" \
  --source-manifest "$PROJECT_ROOT/reports/observation-planning-provenance-v4.json" \
  --results-manifest "$HISTORICAL_REPORT_ROOT/manuscript/results-manifest.json" \
  --junit "$PROJECT_ROOT/reports/pytest-planning-v4.xml" \
  --test-attestation "$TEST_ATTESTATION" \
  --probability-model "$PROBABILITY_MODEL" \
  --covariate-evidence "$HISTORICAL_REPORT_ROOT/covariate-evidence.json" \
  --publication-config "$PROJECT_ROOT/configs/observation-planning-publication-v4.json" \
  --prospective-config "$CONFIG" \
  --prospective-ledger "$RUN_ROOT/ledger.jsonl" \
  "${release_attestation_args[@]}" \
  --output "$HISTORICAL_REPORT_ROOT/readiness.json"
