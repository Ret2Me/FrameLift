#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=/home/ubuntu/telemetry-yield
RUN_ROOT="$PROJECT_ROOT/work/prospective-v4c"
REPORT_ROOT="$PROJECT_ROOT/reports/observation-planning-prospective-v4c"
RUN_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NOW_EPOCH=$(date -u +%s)
CAMPAIGN_END_EPOCH=$(date -u -d '2026-10-05T00:00:00Z' +%s)
PYTHON="$PROJECT_ROOT/.venv/bin/python"
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/src"

mkdir -p "$RUN_ROOT/forecasts" "$RUN_ROOT/plans" "$RUN_ROOT/reconciliation" \
  "$RUN_ROOT/source-manifests" "$RUN_ROOT/covariate-evidence" "$REPORT_ROOT"
exec 9>"$RUN_ROOT/daily-run.lock"
flock -n 9

HARDWARE_ARGS=()
if [[ -f "$RUN_ROOT/local-hardware.json" ]]; then
  HARDWARE_ARGS=(--hardware-archive "$RUN_ROOT/local-hardware.json")
fi
BLOCKER_ARGS=()
if [[ -f "$RUN_ROOT/blockers.json" ]]; then
  BLOCKER_ARGS=(--blockers "$RUN_ROOT/blockers.json")
fi
TARGET_ARGS=()
if [[ -f "$RUN_ROOT/target-overrides.json" ]]; then
  TARGET_ARGS=(--target-overrides "$RUN_ROOT/target-overrides.json")
fi

"$PYTHON" -m telemetry_yield.cli planning-prospective-reconcile-shadow \
  --config "$PROJECT_ROOT/configs/prospective/observation-planning-shadow-v4c.json" \
  --ledger "$RUN_ROOT/ledger.jsonl" \
  --raw-snapshot "$RUN_ROOT/reconciliation/network-$RUN_STAMP.json" \
  --outcomes "$RUN_ROOT/reconciliation/outcomes-$RUN_STAMP.json" \
  --cache-dir "$RUN_ROOT/reconciliation-http-cache" \
  --user-agent 'telemetry-yield-research/0.2 contact=research@example.invalid' \
  --lookback-hours 168 \
  --shared-rate-limit-file "$PROJECT_ROOT/work/satnogs-observations-anonymous.rate"

if (( NOW_EPOCH < CAMPAIGN_END_EPOCH )); then
  "$PYTHON" -m telemetry_yield.cli planning-fetch-forecast \
    --config "$PROJECT_ROOT/configs/prospective/observation-planning-shadow-v4c.json" \
    --output "$RUN_ROOT/forecasts/forecast-$RUN_STAMP.json" \
    --cache-dir "$RUN_ROOT/forecast-http-cache" \
    --user-agent 'telemetry-yield-research/0.2 contact=research@example.invalid' \
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

  "$PYTHON" -m telemetry_yield.cli planning-prospective-run-shadow \
    --config "$PROJECT_ROOT/configs/prospective/observation-planning-shadow-v4c.json" \
    --publication-config "$PROJECT_ROOT/configs/observation-planning-prospective-target-pool-v4b.json" \
    --transmitters "$PROJECT_ROOT/work/a1/satnogs-transmitters-active-direct-2026-08-31.json" \
    --covariates "$RUN_ROOT/forecasts/forecast-$RUN_STAMP.json" \
    --history-dataset "$PROJECT_ROOT/work/observation-planning-publication-v3/normalized-enriched-v2.jsonl" \
    --ledger "$RUN_ROOT/ledger.jsonl" \
    --plan "$RUN_ROOT/plans/plan-$RUN_STAMP.json" \
    --state-db "$RUN_ROOT/planning.sqlite3" \
    --cache-dir "$RUN_ROOT/run-http-cache" \
    --user-agent 'telemetry-yield-research/0.2 contact=research@example.invalid' \
    --source-manifest "$SOURCE_MANIFEST" \
    --input-artifact "$COVARIATE_EVIDENCE" \
    --input-artifact "$PROJECT_ROOT/configs/observation-planning-prospective-station-selection-v4c.json" \
    --input-artifact "$PROJECT_ROOT/work/prospective-v4c/station-selection/antenna-evidence-20260903T180500Z.json" \
    "${BLOCKER_ARGS[@]}" \
    "${TARGET_ARGS[@]}"
fi

"$PYTHON" -m telemetry_yield.cli planning-prospective-report \
  --config "$PROJECT_ROOT/configs/prospective/observation-planning-shadow-v4c.json" \
  --ledger "$RUN_ROOT/ledger.jsonl" \
  --output "$REPORT_ROOT/status.json"

HISTORICAL_REPORT_ROOT="$PROJECT_ROOT/reports/observation-planning-publication-v4"
if [[ -f "$HISTORICAL_REPORT_ROOT/manuscript/results-manifest.json" ]]; then
  "$PYTHON" -m telemetry_yield.cli planning-publication-readiness \
    --snapshot-manifest "$HISTORICAL_REPORT_ROOT/snapshot-manifest.json" \
    --dataset-manifest "$HISTORICAL_REPORT_ROOT/dataset-manifest.json" \
    --evaluation "$HISTORICAL_REPORT_ROOT/analysis/evaluation.json" \
    --independent-audit "$HISTORICAL_REPORT_ROOT/independent-audit.json" \
    --source-manifest "$PROJECT_ROOT/reports/observation-planning-provenance-v4.json" \
    --results-manifest "$HISTORICAL_REPORT_ROOT/manuscript/results-manifest.json" \
    --junit "$PROJECT_ROOT/reports/pytest-planning-v4.xml" \
    --probability-model "$HISTORICAL_REPORT_ROOT/deployment/probability-model.json" \
    --covariate-evidence "$HISTORICAL_REPORT_ROOT/covariate-evidence.json" \
    --publication-config "$PROJECT_ROOT/configs/observation-planning-publication-v4.json" \
    --prospective-config "$PROJECT_ROOT/configs/prospective/observation-planning-shadow-v4c.json" \
    --prospective-ledger "$RUN_ROOT/ledger.jsonl" \
    --output "$HISTORICAL_REPORT_ROOT/readiness.json"
fi
