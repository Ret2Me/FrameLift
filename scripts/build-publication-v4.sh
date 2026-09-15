#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=/home/ubuntu/telemetry-yield
RUN_ROOT="$PROJECT_ROOT/work/observation-planning-publication-v4"
REPORT_ROOT="$PROJECT_ROOT/reports/observation-planning-publication-v4"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
CONFIG="$PROJECT_ROOT/configs/observation-planning-publication-v4.json"
PROSPECTIVE_RUN_ROOT="$PROJECT_ROOT/work/prospective-v4g"
PROSPECTIVE_CONFIG="$PROSPECTIVE_RUN_ROOT/config.json"
PROSPECTIVE_TEMPLATE="$PROJECT_ROOT/configs/prospective/observation-planning-shadow-v4g-template.json"
BLOCKER_TEMPLATE="$PROJECT_ROOT/configs/prospective/observation-planning-blockers-v4g.json"
TARGET_OVERRIDE_TEMPLATE="$PROJECT_ROOT/configs/prospective/observation-planning-target-overrides-v4g.json"
RELEASE_ATTESTATION="$REPORT_ROOT/release-attestation.json"
API_CONTACT="$PROJECT_ROOT/work/operations/publication-api-contact.json"
TEST_ATTESTATION="$REPORT_ROOT/test-attestation.json"
SATNOGS_TOKEN_FILE="/home/ubuntu/.config/telemetry-yield/satnogs-token"
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/src"

mkdir -p "$RUN_ROOT" "$REPORT_ROOT"
exec 9>"$RUN_ROOT/build.lock"
flock -n 9

load_runtime_user_agent() {
  if [[ ! -s "$API_CONTACT" ]]; then
    echo "historical snapshot may continue, but later API stages require $API_CONTACT" >&2
    return 1
  fi
  RUNTIME_USER_AGENT=$("$PYTHON" -m telemetry_yield.planning.api_contact \
    --contact-file "$API_CONTACT")
}

finish_campaign_bootstrap() {
  local -a release_attestation_args=()
  "$PYTHON" -m telemetry_yield.planning.test_attestation \
    --project-root "$PROJECT_ROOT" \
    --source-manifest "$PROJECT_ROOT/reports/observation-planning-provenance-v4.json" \
    --junit "$PROJECT_ROOT/reports/pytest-planning-v4.xml" \
    --attestation "$TEST_ATTESTATION"
  mkdir -p "$PROSPECTIVE_RUN_ROOT"
  if [[ ! -e "$PROSPECTIVE_RUN_ROOT/blockers.json" ]]; then
    install -m 0644 "$BLOCKER_TEMPLATE" "$PROSPECTIVE_RUN_ROOT/blockers.json"
  fi
  if [[ ! -e "$PROSPECTIVE_RUN_ROOT/target-overrides.json" ]]; then
    install -m 0644 "$TARGET_OVERRIDE_TEMPLATE" "$PROSPECTIVE_RUN_ROOT/target-overrides.json"
  fi
  if [[ -s "$RELEASE_ATTESTATION" ]]; then
    release_attestation_args=(--release-attestation "$RELEASE_ATTESTATION")
  fi

  "$PYTHON" -m telemetry_yield.planning.campaign_materialization \
    --project-root "$PROJECT_ROOT" \
    --template "$PROSPECTIVE_TEMPLATE" \
    --output "$PROSPECTIVE_CONFIG" \
    --snapshot-manifest "$REPORT_ROOT/snapshot-manifest.json" \
    --history-dataset "$RUN_ROOT/normalized-enriched.jsonl" \
    --dataset-manifest "$REPORT_ROOT/dataset-manifest.json" \
    --evaluation "$REPORT_ROOT/analysis/evaluation.json" \
    --independent-audit "$REPORT_ROOT/independent-audit.json" \
    --probability-model "$REPORT_ROOT/deployment/probability-model.json" \
    --api-contact "$API_CONTACT"

  "$PROJECT_ROOT/scripts/prospective-v4g-daily.sh"

  "$PYTHON" -m telemetry_yield.cli planning-publication-readiness \
    --snapshot-manifest "$REPORT_ROOT/snapshot-manifest.json" \
    --dataset-manifest "$REPORT_ROOT/dataset-manifest.json" \
    --evaluation "$REPORT_ROOT/analysis/evaluation.json" \
    --independent-audit "$REPORT_ROOT/independent-audit.json" \
    --source-manifest "$PROJECT_ROOT/reports/observation-planning-provenance-v4.json" \
    --results-manifest "$REPORT_ROOT/manuscript/results-manifest.json" \
    --junit "$PROJECT_ROOT/reports/pytest-planning-v4.xml" \
    --test-attestation "$TEST_ATTESTATION" \
    --probability-model "$REPORT_ROOT/deployment/probability-model.json" \
    --covariate-evidence "$REPORT_ROOT/covariate-evidence.json" \
    --publication-config "$CONFIG" \
    --prospective-config "$PROSPECTIVE_CONFIG" \
    --prospective-ledger "$PROSPECTIVE_RUN_ROOT/ledger.jsonl" \
    "${release_attestation_args[@]}" \
    --output "$REPORT_ROOT/readiness.json"

  # Enabling is the final step: a failed historical gate or failed pre-start
  # solve leaves the timer disabled instead of launching an invalid run.
  /usr/bin/systemctl --user enable --now telemetry-yield-prospective-v4g.timer
}

# If a failure occurred after the dated prospective config was frozen, never
# regenerate historical artifacts or the model. Verify their exact hashes and
# resume only the idempotent campaign bootstrap.
if [[ -s "$PROSPECTIVE_CONFIG" ]]; then
  if ! load_runtime_user_agent; then
    exit 0
  fi
  finish_campaign_bootstrap
  exit 0
fi

ACQUISITION_AUTH_ARGS=()
ACQUISITION_RATE_FILE="$PROJECT_ROOT/work/satnogs-observations-anonymous.rate"
if [[ -s "$SATNOGS_TOKEN_FILE" ]]; then
  ACQUISITION_AUTH_ARGS=(--api-token-file "$SATNOGS_TOKEN_FILE")
  ACQUISITION_RATE_FILE="$PROJECT_ROOT/work/satnogs-observations-authenticated.rate"
fi

"$PYTHON" -m telemetry_yield.cli planning-fetch-publication \
  --config "$CONFIG" \
  --raw-dir "$RUN_ROOT/raw" \
  --cache-dir "$RUN_ROOT/http-cache" \
  --manifest "$REPORT_ROOT/snapshot-manifest.json" \
  --shared-rate-limit-file "$ACQUISITION_RATE_FILE" \
  "${ACQUISITION_AUTH_ARGS[@]}"

# The historical SatNOGS panel deliberately retains its frozen, disclosed
# placeholder identity. Every later external request and the registered
# prospective campaign require a user-supplied non-placeholder contact.
if ! load_runtime_user_agent; then
  exit 0
fi

"$PROJECT_ROOT/scripts/run-publication-v4-tests.sh"

"$PYTHON" -m telemetry_yield.cli planning-build-dataset \
  --config "$CONFIG" \
  --snapshot-manifest "$REPORT_ROOT/snapshot-manifest.json" \
  --raw-dir "$RUN_ROOT/raw" \
  --output "$RUN_ROOT/normalized-base.jsonl" \
  --manifest "$REPORT_ROOT/dataset-manifest-base.json"

# A complete transport snapshot can still contain a predeclared target with no
# observations. Stop cleanly at the input-only gate instead of repeatedly
# downloading covariates and letting systemd restart an impossible evaluation.
if ! "$PYTHON" -c \
  'import json,sys; raise SystemExit(0 if json.load(open(sys.argv[1], encoding="utf-8")).get("publication_count_gate") == "pass" else 1)' \
  "$REPORT_ROOT/dataset-manifest-base.json"; then
  echo "publication count gate failed; final training remains stopped before covariate fetch" >&2
  exit 0
fi

"$PYTHON" -m telemetry_yield.cli planning-fetch-covariates \
  --dataset "$RUN_ROOT/normalized-base.jsonl" \
  --output "$RUN_ROOT/covariates.json" \
  --cache-dir "$RUN_ROOT/covariate-http-cache" \
  --user-agent "$RUNTIME_USER_AGENT" \
  --minimum-request-interval-seconds 1 \
  --weather-maximum-missing-days 7

"$PYTHON" -m telemetry_yield.cli planning-write-covariate-evidence \
  --covariates "$RUN_ROOT/covariates.json" \
  --cache-dir "$RUN_ROOT/covariate-http-cache" \
  --output "$REPORT_ROOT/covariate-evidence.json"

"$PYTHON" -m telemetry_yield.cli planning-build-dataset \
  --config "$CONFIG" \
  --snapshot-manifest "$REPORT_ROOT/snapshot-manifest.json" \
  --raw-dir "$RUN_ROOT/raw" \
  --covariates "$RUN_ROOT/covariates.json" \
  --output "$RUN_ROOT/normalized-enriched.jsonl" \
  --manifest "$REPORT_ROOT/dataset-manifest.json"

"$PYTHON" -m telemetry_yield.cli planning-evaluate-publication \
  --config "$CONFIG" \
  --dataset "$RUN_ROOT/normalized-enriched.jsonl" \
  --output-dir "$REPORT_ROOT/analysis"

"$PYTHON" -m telemetry_yield.cli planning-build-probability-model \
  --dataset "$RUN_ROOT/normalized-enriched.jsonl" \
  --dataset-manifest "$REPORT_ROOT/dataset-manifest.json" \
  --evaluation "$REPORT_ROOT/analysis/evaluation.json" \
  --output "$REPORT_ROOT/deployment/probability-model.json"

"$PYTHON" -m telemetry_yield.cli planning-audit-publication \
  --dataset "$RUN_ROOT/normalized-enriched.jsonl" \
  --dataset-manifest "$REPORT_ROOT/dataset-manifest.json" \
  --evaluation "$REPORT_ROOT/analysis/evaluation.json" \
  --output "$REPORT_ROOT/independent-audit.json"

"$PYTHON" -m telemetry_yield.cli planning-write-publication-results \
  --dataset-manifest "$REPORT_ROOT/dataset-manifest.json" \
  --evaluation "$REPORT_ROOT/analysis/evaluation.json" \
  --independent-audit "$REPORT_ROOT/independent-audit.json" \
  --probability-model "$REPORT_ROOT/deployment/probability-model.json" \
  --covariate-evidence "$REPORT_ROOT/covariate-evidence.json" \
  --output-dir "$REPORT_ROOT/manuscript"

finish_campaign_bootstrap
