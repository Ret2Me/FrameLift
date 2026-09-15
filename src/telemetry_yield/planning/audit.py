"""Independent arithmetic and hash audit for publication planning artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class AuditCheck:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _recompute_reception_success(
    row: Mapping[str, object],
) -> int | None:
    """Independently reconstruct the packet reception endpoint."""

    raw_mode = row.get("transmitter_mode")
    if raw_mode is None or not str(raw_mode).strip():
        return None
    if str(raw_mode).strip().upper() in {"CW", "FM", "AM", "USB", "LSB"}:
        return None
    signal = row.get("signal_present")
    decode = row.get("decode_success_given_signal")
    if signal == 0:
        return 0
    if signal == 1 and decode in {0, 1}:
        return int(decode)
    return None


def _recompute_publication_count_requirements(
    rows: Sequence[Mapping[str, object]],
    config: Mapping[str, object],
) -> dict[str, bool]:
    """Independently reproduce the pre-fit count and diversity gate."""

    signal_rows = [row for row in rows if row.get("signal_present") in {0, 1}]
    decode_rows = [
        row for row in rows if row.get("decode_success_given_signal") in {0, 1}
    ]
    requirements = {
        "known_signal_rows_at_least_1000": len(signal_rows) >= 1000,
        "signal_satellites_at_least_5": len(
            {int(row["norad_id"]) for row in signal_rows}
        )
        >= 5,
        "signal_stations_at_least_5": len(
            {int(row["station_id"]) for row in signal_rows}
        )
        >= 5,
        "conditional_decode_rows_at_least_300": len(decode_rows) >= 300,
        "conditional_decode_has_both_classes": {
            row.get("decode_success_given_signal") for row in decode_rows
        }
        == {0, 1},
        "conditional_decode_satellites_at_least_5": len(
            {int(row["norad_id"]) for row in decode_rows}
        )
        >= 5,
        "conditional_decode_stations_at_least_5": len(
            {int(row["station_id"]) for row in decode_rows}
        )
        >= 5,
    }
    if config.get("study_id") != "observation-planning-publication-v4":
        return requirements

    targets = config.get("targets")
    external = config.get("external_validation_norad_ids")
    sampling = config.get("sampling_intervals")
    target_ids = (
        {
            int(item.get("norad_id", 0))
            for item in targets
            if isinstance(item, Mapping)
        }
        if isinstance(targets, list)
        else set()
    )
    external_ids = (
        {int(value) for value in external}
        if isinstance(external, list)
        else set()
    )
    final_interval = sampling[-1] if isinstance(sampling, list) and sampling else {}
    cutoff_text = (
        str(final_interval.get("start", ""))
        if isinstance(final_interval, Mapping)
        else ""
    )
    cutoff = datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
    observed_ids = {int(row["norad_id"]) for row in rows}
    stations = sorted({int(row["station_id"]) for row in rows})
    fraction = float(config.get("external_station_holdout_fraction", 0.0))
    holdout_count = max(
        1, min(len(stations) - 1, round(len(stations) * fraction))
    )
    seed = int(config.get("random_seed", -1))
    station_holdout = set(
        sorted(
            stations,
            key=lambda value: hashlib.sha256(f"{seed}:{value}".encode()).hexdigest(),
        )[:holdout_count]
    )
    requirements.update(
        {
            "v4_target_count_is_50": len(target_ids) == 50,
            "v4_all_target_ids_represented": observed_ids == target_ids,
            "v4_signal_satellites_at_least_40": len(
                {int(row["norad_id"]) for row in signal_rows}
            )
            >= 40,
            "v4_conditional_decode_satellites_at_least_25": len(
                {int(row["norad_id"]) for row in decode_rows}
            )
            >= 25,
            "v4_predeclared_temporal_cutoff_matches_contract": (
                cutoff_text == "2026-08-01T00:00:00Z"
            ),
        }
    )

    def timestamp(row: Mapping[str, object], field: str) -> datetime:
        return datetime.fromisoformat(str(row[field]).replace("Z", "+00:00"))

    def has_both_classes(
        selected: Sequence[Mapping[str, object]], task: str
    ) -> bool:
        return {row.get(task) for row in selected} == {0, 1}

    for task in ("signal_present", "decode_success_given_signal"):
        eligible = [row for row in rows if row.get(task) in {0, 1}]
        training = [row for row in eligible if timestamp(row, "end") < cutoff]
        test = [row for row in eligible if timestamp(row, "start") >= cutoff]
        external_satellite_training = [
            row for row in training if int(row["norad_id"]) not in external_ids
        ]
        external_satellite_test = [
            row for row in test if int(row["norad_id"]) in external_ids
        ]
        external_station_training = [
            row for row in training if int(row["station_id"]) not in station_holdout
        ]
        external_station_test = [
            row for row in test if int(row["station_id"]) in station_holdout
        ]
        requirements.update(
            {
                f"v4_{task}_temporal_training_is_estimable": (
                    len(training) >= 10 and has_both_classes(training, task)
                ),
                f"v4_{task}_temporal_test_is_informative": (
                    len(test) >= 30 and has_both_classes(test, task)
                ),
                f"v4_{task}_external_satellite_training_is_estimable": (
                    len(external_satellite_training) >= 10
                    and has_both_classes(external_satellite_training, task)
                ),
                f"v4_{task}_external_satellite_test_is_informative": (
                    len(external_satellite_test) >= 30
                    and len(
                        {int(row["norad_id"]) for row in external_satellite_test}
                    )
                    >= 5
                    and has_both_classes(external_satellite_test, task)
                ),
                f"v4_{task}_external_station_training_is_estimable": (
                    len(external_station_training) >= 10
                    and has_both_classes(external_station_training, task)
                ),
                f"v4_{task}_external_station_test_is_informative": (
                    len(external_station_test) >= 30
                    and len(
                        {int(row["station_id"]) for row in external_station_test}
                    )
                    >= 5
                    and has_both_classes(external_station_test, task)
                ),
            }
        )

    reception_rows = [
        row for row in rows if _recompute_reception_success(row) in {0, 1}
    ]
    reception_test = [
        row for row in reception_rows if timestamp(row, "start") >= cutoff
    ]
    reception_external_satellite_test = [
        row
        for row in reception_test
        if int(row["norad_id"]) in external_ids
    ]
    reception_external_station_test = [
        row
        for row in reception_test
        if int(row["station_id"]) in station_holdout
    ]
    requirements.update(
        {
            "v4_reception_success_temporal_test_is_informative": (
                len(reception_test) >= 30
                and {_recompute_reception_success(row) for row in reception_test}
                == {0, 1}
            ),
            "v4_reception_success_external_satellite_test_is_informative": (
                len(reception_external_satellite_test) >= 30
                and len(
                    {
                        int(row["norad_id"])
                        for row in reception_external_satellite_test
                    }
                )
                >= 5
                and {
                    _recompute_reception_success(row)
                    for row in reception_external_satellite_test
                }
                == {0, 1}
            ),
            "v4_reception_success_external_station_test_is_informative": (
                len(reception_external_station_test) >= 30
                and len(
                    {
                        int(row["station_id"])
                        for row in reception_external_station_test
                    }
                )
                >= 5
                and {
                    _recompute_reception_success(row)
                    for row in reception_external_station_test
                }
                == {0, 1}
            ),
        }
    )
    return requirements


def _canonical_plan_fingerprint(plan: Mapping[str, object]) -> str:
    assignments = plan.get("assignments", [])
    if not isinstance(assignments, list):
        raise ValueError("plan assignments must be a list")
    schema_version = plan.get("schema_version")
    if schema_version not in {"observation-plan-v1", "observation-plan-v2"}:
        raise ValueError("unsupported plan schema")
    if any(not isinstance(item, Mapping) for item in assignments):
        raise ValueError("plan assignment must be an object")
    if schema_version == "observation-plan-v2":
        canonical_assignments = []
        for raw_item in assignments:
            assert isinstance(raw_item, Mapping)
            probability = raw_item.get("probability")
            if not isinstance(probability, Mapping):
                raise ValueError("plan probability must be an object")
            metadata = raw_item.get("metadata", {})
            if not isinstance(metadata, Mapping):
                raise ValueError("plan metadata must be an object")
            canonical_assignments.append(
                {
                    "opportunity_id": raw_item["opportunity_id"],
                    "norad_id": int(raw_item["norad_id"]),
                    "satellite_name": raw_item["satellite_name"],
                    "station_id": raw_item["station_id"],
                    "resource_id": raw_item["resource_id"],
                    "start": raw_item["start"],
                    "end": raw_item["end"],
                    "tle_fingerprint": raw_item["tle_fingerprint"],
                    "probability": {
                        "p_signal_present": float(
                            probability["p_signal_present"]
                        ),
                        "p_decode_given_signal": float(
                            probability["p_decode_given_signal"]
                        ),
                        "p_success": float(probability["p_success"]),
                        "standard_deviation": float(
                            probability["standard_deviation"]
                        ),
                        "lower_90": float(probability["lower_90"]),
                        "upper_90": float(probability["upper_90"]),
                        "missing_features": list(
                            probability.get("missing_features", [])
                        ),
                        "evidence_count": int(
                            probability.get("evidence_count", 0)
                        ),
                        "model_version": probability["model_version"],
                    },
                    "nominal_unique_samples": float(
                        raw_item["nominal_unique_samples"]
                    ),
                    "expected_unique_samples": float(
                        raw_item["expected_unique_samples"]
                    ),
                    "priority": float(raw_item["priority"]),
                    "max_elevation_deg": float(raw_item["max_elevation_deg"]),
                    "min_range_km": float(raw_item["min_range_km"]),
                    "frequency_hz": (
                        float(raw_item["frequency_hz"])
                        if raw_item.get("frequency_hz") is not None
                        else None
                    ),
                    "modulation": raw_item.get("modulation"),
                    "transmitter_uuid": raw_item.get("transmitter_uuid"),
                    "satnogs_station_id": raw_item.get("satnogs_station_id"),
                    "exclusive_transmission": bool(
                        raw_item.get("exclusive_transmission", True)
                    ),
                    "metadata": dict(metadata),
                }
            )
    else:
        canonical_assignments = [
            {
                "opportunity_id": item["opportunity_id"],
                "norad_id": int(item["norad_id"]),
                "station_id": item["station_id"],
                "resource_id": item["resource_id"],
                "start": item["start"],
                "end": item["end"],
                "tle_fingerprint": item["tle_fingerprint"],
                "p_success": float(item["probability"]["p_success"]),
                "nominal_unique_samples": float(item["nominal_unique_samples"]),
                "expected_unique_samples": float(item["expected_unique_samples"]),
                "priority": float(item["priority"]),
                "frequency_hz": (
                    float(item["frequency_hz"])
                    if item.get("frequency_hz") is not None
                    else None
                ),
                "transmitter_uuid": item.get("transmitter_uuid"),
                "satnogs_station_id": item.get("satnogs_station_id"),
                "exclusive_transmission": bool(
                    item.get("exclusive_transmission", True)
                ),
            }
            for item in assignments
        ]
    payload = {
        "horizon_start": plan["horizon_start"],
        "horizon_end": plan["horizon_end"],
        "assignments": canonical_assignments,
        "tle_fingerprints": {
            int(key): str(value)
            for key, value in sorted(
                dict(plan.get("tle_fingerprints", {})).items(),
                key=lambda item: int(item[0]),
            )
        },
    }
    if schema_version == "observation-plan-v2":
        payload.update(
            {
                "plan_id": plan["plan_id"],
                "revision": int(plan["revision"]),
                "created_at": plan["created_at"],
                "objective_value": float(plan["objective_value"]),
                "solver": plan["solver"],
                "trigger": plan["trigger"],
                "diagnostics": dict(plan.get("diagnostics", {})),
            }
        )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _jsonl(path: Path) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, Mapping):
                raise ValueError(f"non-object JSONL at {path}:{line_number}")
            rows.append(payload)
    return rows


def _close(left: object, right: object, tolerance: float = 1e-10) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return left == right


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot compute a quantile of no bootstrap draws")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _recompute_probability_metrics(
    outcomes: list[int], probabilities: list[float]
) -> dict[str, object]:
    """Independent implementation of every reported probability metric."""

    import numpy as np
    from scipy.optimize import minimize
    from scipy.special import expit
    from scipy.stats import rankdata

    if not outcomes or len(outcomes) != len(probabilities):
        raise ValueError("probability metrics require aligned non-empty values")
    y = np.asarray(outcomes, dtype=float)
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-8, 1 - 1e-8)
    positives = int(y.sum())
    negatives = len(y) - positives
    auroc: float | None = None
    if positives and negatives:
        ranks = rankdata(p, method="average")
        auroc = float(
            (ranks[y == 1].sum() - positives * (positives + 1) / 2)
            / (positives * negatives)
        )

    calibration_intercept: float | None = None
    calibration_slope: float | None = None
    if positives and negatives:
        logits = np.log(p / (1 - p))
        matrix = np.column_stack((np.ones(len(logits)), logits))

        def objective(coefficients):
            linear = matrix @ coefficients
            loss = float(np.mean(np.logaddexp(0, linear) - y * linear))
            gradient = matrix.T @ (expit(linear) - y) / len(y)
            return loss, gradient

        fitted = minimize(
            objective,
            np.asarray([0.0, 1.0]),
            method="L-BFGS-B",
            jac=True,
        )
        if fitted.success:
            calibration_intercept = float(fitted.x[0])
            calibration_slope = float(fitted.x[1])

    order = np.argsort(p, kind="stable")
    bins = [part for part in np.array_split(order, min(10, len(order))) if len(part)]
    ece = sum(
        len(part) / len(y) * abs(float(y[part].mean() - p[part].mean()))
        for part in bins
    )
    return {
        "count": len(outcomes),
        "positives": positives,
        "brier": float(np.mean((y - p) ** 2)),
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "auroc": auroc,
        "calibration_intercept": calibration_intercept,
        "calibration_slope": calibration_slope,
        "expected_calibration_error": float(ece),
        "interval_coverage_90": None,
    }


def _recompute_decision_metrics(
    outcomes: list[int],
    probabilities: list[float],
    *,
    threshold: float = 0.5,
) -> dict[str, object]:
    """Independent fixed-threshold confusion matrix and derived rates."""

    if not outcomes or len(outcomes) != len(probabilities):
        raise ValueError("decision metrics require aligned non-empty values")
    predicted = [value >= threshold for value in probabilities]
    true_positive = sum(
        prediction and outcome == 1
        for outcome, prediction in zip(outcomes, predicted, strict=True)
    )
    true_negative = sum(
        not prediction and outcome == 0
        for outcome, prediction in zip(outcomes, predicted, strict=True)
    )
    false_positive = sum(
        prediction and outcome == 0
        for outcome, prediction in zip(outcomes, predicted, strict=True)
    )
    false_negative = sum(
        not prediction and outcome == 1
        for outcome, prediction in zip(outcomes, predicted, strict=True)
    )

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    return {
        "threshold": threshold,
        "count": len(outcomes),
        "true_positive": true_positive,
        "true_negative": true_negative,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "accuracy": (true_positive + true_negative) / len(outcomes),
        "sensitivity": ratio(true_positive, true_positive + false_negative),
        "specificity": ratio(true_negative, true_negative + false_positive),
        "positive_predictive_value": ratio(
            true_positive, true_positive + false_positive
        ),
        "negative_predictive_value": ratio(
            true_negative, true_negative + false_negative
        ),
    }


def _audit_simulation(
    checks: list[AuditCheck],
    *,
    name: str,
    simulation: Mapping[str, object],
    assignments: list[Mapping[str, object]],
    nominal_expected: float,
    expected_trials: int,
    expected_seed: int,
) -> None:
    """Reproduce Monte Carlo draws without importing production simulation code."""

    scenario = simulation.get("scenario")
    correlated = isinstance(scenario, Mapping)
    scenario_values: dict[str, float] = {}
    if correlated:
        assert isinstance(scenario, Mapping)
        try:
            scenario_values = {
                key: float(scenario[key])
                for key in (
                    "station_day_availability",
                    "satellite_day_transmit_availability",
                    "severe_environment_probability",
                    "severe_environment_success_multiplier",
                )
            }
        except (KeyError, TypeError, ValueError):
            checks.append(
                AuditCheck(f"risk.{name}.scenario", False, "malformed scenario")
            )
            return
        checks.append(
            AuditCheck(
                f"risk.{name}.scenario",
                all(0 <= value <= 1 for value in scenario_values.values()),
                str(scenario_values),
            )
        )

    trials = int(simulation.get("trials", -1))
    seed = int(simulation.get("seed", -1))
    checks.append(
        AuditCheck(
            f"risk.{name}.identity",
            trials == expected_trials and seed == expected_seed,
            (
                f"trials={trials}; expected_trials={expected_trials}; "
                f"seed={seed}; expected_seed={expected_seed}"
            ),
        )
    )
    if trials <= 0 or seed < 0:
        return

    rng = random.Random(seed)
    totals: list[float] = []
    by_satellite: defaultdict[int, float] = defaultdict(float)
    any_success = 0
    below_half = 0
    for _ in range(trials):
        station_day: dict[tuple[str, str], bool] = {}
        satellite_day: dict[tuple[int, str], bool] = {}
        environment_day: dict[str, bool] = {}
        total = 0.0
        trial_by_satellite: defaultdict[int, float] = defaultdict(float)
        for item in assignments:
            norad_id = int(item["norad_id"])
            nominal = float(item["nominal_unique_samples"])
            probability = float(item["probability"]["p_success"])  # type: ignore[index]
            if correlated:
                day = str(item["start"])[:10]
                station_key = (str(item["station_id"]), day)
                satellite_key = (norad_id, day)
                if station_key not in station_day:
                    station_day[station_key] = (
                        rng.random()
                        < scenario_values["station_day_availability"]
                    )
                if satellite_key not in satellite_day:
                    satellite_day[satellite_key] = (
                        rng.random()
                        < scenario_values["satellite_day_transmit_availability"]
                    )
                if day not in environment_day:
                    environment_day[day] = (
                        rng.random()
                        < scenario_values["severe_environment_probability"]
                    )
                if not station_day[station_key] or not satellite_day[satellite_key]:
                    continue
                if environment_day[day]:
                    probability *= scenario_values[
                        "severe_environment_success_multiplier"
                    ]
            if rng.random() < probability:
                total += nominal
                trial_by_satellite[norad_id] += nominal
        totals.append(total)
        any_success += total > 0
        if correlated:
            below_half += total < 0.5 * nominal_expected
        for norad_id, value in trial_by_satellite.items():
            by_satellite[norad_id] += value

    expected_values: dict[str, object] = {
        "mean_unique_samples": sum(totals) / trials,
        "percentile_05": _quantile(totals, 0.05),
        "percentile_50": _quantile(totals, 0.50),
        "percentile_95": _quantile(totals, 0.95),
        "probability_of_any_success": any_success / trials,
    }
    if correlated:
        expected_values["probability_below_half_expected"] = below_half / trials
    for metric, actual in expected_values.items():
        checks.append(
            AuditCheck(
                f"risk.{name}.recomputed_{metric}",
                _close(actual, simulation.get(metric)),
                f"actual={actual}; expected={simulation.get(metric)}",
            )
        )

    raw_by_satellite = simulation.get("mean_by_satellite", {})
    try:
        reported_by_satellite = {
            int(key): float(value)
            for key, value in dict(raw_by_satellite).items()  # type: ignore[arg-type]
        }
    except (TypeError, ValueError):
        reported_by_satellite = {}
    expected_by_satellite = {
        norad_id: value / trials for norad_id, value in by_satellite.items()
    }
    satellite_match = set(reported_by_satellite) == set(expected_by_satellite) and all(
        _close(reported_by_satellite[key], expected_by_satellite[key])
        for key in expected_by_satellite
    )
    checks.append(
        AuditCheck(
            f"risk.{name}.recomputed_mean_by_satellite",
            satellite_match,
            (
                f"actual={expected_by_satellite}; "
                f"expected={reported_by_satellite}"
            ),
        )
    )


def _audit_bootstraps(
    checks: list[AuditCheck],
    *,
    task: str,
    task_report: Mapping[str, object],
    records: list[Mapping[str, object]],
    seed: int,
    expected_replicates: int,
) -> None:
    for report_key in ("temporal", "loso_satellite", "loso_station"):
        split = task_report.get(report_key)
        if not isinstance(split, Mapping):
            continue
        comparison_groups = (
            ("primary", split.get("paired_bootstrap_vs_global_rate")),
            ("ablation", split.get("paired_ablation_bootstraps")),
            (
                "station_cluster_sensitivity",
                split.get("paired_bootstrap_station_cluster_sensitivity"),
            ),
            (
                "station_cluster_ablation_sensitivity",
                split.get("paired_ablation_station_cluster_sensitivity"),
            ),
        )
        for comparison_group, comparisons in comparison_groups:
            if not isinstance(comparisons, Mapping):
                continue
            for model, summary in comparisons.items():
                if not isinstance(summary, Mapping) or "artifact_path" not in summary:
                    continue
                _audit_bootstrap(
                    checks,
                    task=task,
                    report_key=report_key,
                    model=f"{comparison_group}.{model}",
                    summary=summary,
                    records=records,
                    seed=seed,
                    expected_replicates=expected_replicates,
                )


def _audit_bootstrap(
    checks: list[AuditCheck],
    *,
    task: str,
    report_key: str,
    model: str,
    summary: Mapping[str, object],
    records: list[Mapping[str, object]],
    seed: int,
    expected_replicates: int,
) -> None:
    import numpy as np

    path = Path(str(summary["artifact_path"]))
    digest = _sha256(path)
    checks.append(
        AuditCheck(
            f"{task}.{report_key}.{model}.bootstrap_sha",
            digest == summary.get("artifact_sha256"),
            digest,
        )
    )
    artifact = json.loads(path.read_text(encoding="utf-8"))
    draws = [float(value) for value in artifact.get("replicates", [])]
    checks.append(
        AuditCheck(
            f"{task}.{report_key}.{model}.bootstrap_schema",
            artifact.get("schema_version") == "paired-cluster-bootstrap-v1",
            str(artifact.get("schema_version")),
        )
    )
    checks.append(
        AuditCheck(
            f"{task}.{report_key}.{model}.bootstrap_declared_replicates",
            len(draws) == expected_replicates,
            f"actual={len(draws)}; expected={expected_replicates}",
        )
    )
    split_name = {
        "temporal": "temporal",
        "loso_satellite": "loso-satellite",
        "loso_station": "loso-station",
        "external_satellite": "external-satellite",
        "external_station": "external-station",
        "covariate_ablation": "temporal-covariate-ablation",
        "composed_temporal": "temporal-composed",
        "composed_temporal_station_sensitivity": "temporal-composed",
        "composed_external_satellite": "external-norad_id-composed",
        "composed_external_station": "external-station_id-composed",
    }[report_key]
    for metric_name, actual in (
        ("lower_95", _quantile(draws, 0.025)),
        ("upper_95", _quantile(draws, 0.975)),
        ("replicate_count", len(draws)),
    ):
        expected = (
            artifact.get(metric_name)
            if metric_name != "replicate_count"
            else summary.get("replicate_count")
        )
        checks.append(
            AuditCheck(
                f"{task}.{report_key}.{model}.bootstrap_{metric_name}",
                _close(actual, expected),
                f"actual={actual}; expected={expected}",
            )
        )

    candidate_name = str(artifact.get("model", ""))
    baseline_name = str(artifact.get("baseline", ""))
    cluster_name = str(artifact.get("cluster", ""))
    checks.append(
        AuditCheck(
            f"{task}.{report_key}.{model}.bootstrap_summary_identity",
            candidate_name == str(summary.get("model", ""))
            and baseline_name == str(summary.get("baseline", ""))
            and cluster_name in {"norad_id", "station_id"},
            f"model={candidate_name}; baseline={baseline_name}; cluster={cluster_name}",
        )
    )
    split_records = [record for record in records if record.get("split") == split_name]
    candidates = {
        (record.get("observation_id"), record.get("fold")): record
        for record in split_records
        if record.get("model") == candidate_name
    }
    baselines = {
        (record.get("observation_id"), record.get("fold")): record
        for record in split_records
        if record.get("model") == baseline_name
    }
    keys = sorted(set(candidates) & set(baselines))
    differences: defaultdict[int, list[float]] = defaultdict(list)
    candidate_errors: list[float] = []
    baseline_errors: list[float] = []
    aligned = bool(keys)
    for key in keys:
        candidate, baseline = candidates[key], baselines[key]
        if candidate.get("outcome") != baseline.get("outcome"):
            aligned = False
            break
        try:
            outcome = int(candidate["outcome"])
            candidate_error = (outcome - float(candidate["probability"])) ** 2
            baseline_error = (outcome - float(baseline["probability"])) ** 2
            cluster_id = int(candidate[cluster_name])
        except (KeyError, TypeError, ValueError):
            aligned = False
            break
        differences[cluster_id].append(candidate_error - baseline_error)
        candidate_errors.append(candidate_error)
        baseline_errors.append(baseline_error)
    checks.append(
        AuditCheck(
            f"{task}.{report_key}.{model}.bootstrap_predictions_aligned",
            aligned,
            f"aligned_rows={len(candidate_errors)}",
        )
    )
    if aligned and differences and baseline_errors:
        cluster_ids = sorted(differences)
        rng = np.random.default_rng(seed)
        recomputed_draws: list[float] = []
        for _ in range(expected_replicates):
            selected = rng.choice(cluster_ids, size=len(cluster_ids), replace=True)
            values = [
                value
                for cluster_id in selected
                for value in differences[int(cluster_id)]
            ]
            recomputed_draws.append(float(np.mean(values)))
        draws_match = len(draws) == len(recomputed_draws) and all(
            _close(actual, expected)
            for actual, expected in zip(draws, recomputed_draws, strict=True)
        )
        checks.append(
            AuditCheck(
                f"{task}.{report_key}.{model}.bootstrap_draws_recomputed",
                draws_match,
                f"draws={len(draws)}; recomputed={len(recomputed_draws)}",
            )
        )
        estimate = sum(candidate_errors) / len(candidate_errors) - sum(
            baseline_errors
        ) / len(baseline_errors)
        baseline_brier = sum(baseline_errors) / len(baseline_errors)
        relative = (
            baseline_brier - sum(candidate_errors) / len(candidate_errors)
        ) / max(baseline_brier, 1e-15)
        for metric_name, actual in (
            ("estimate", estimate),
            ("relative_brier_reduction", relative),
        ):
            checks.append(
                AuditCheck(
                    f"{task}.{report_key}.{model}.bootstrap_recomputed_{metric_name}",
                    _close(actual, artifact.get(metric_name)),
                    f"actual={actual}; expected={artifact.get(metric_name)}",
                )
            )
        expected_effect = relative >= 0.05 and _quantile(recomputed_draws, 0.975) < 0
        expected_effect = len(cluster_ids) >= 5 and expected_effect
        for metric_name, actual in (
            ("aligned_prediction_count", len(candidate_errors)),
            ("cluster_count", len(cluster_ids)),
        ):
            checks.append(
                AuditCheck(
                    f"{task}.{report_key}.{model}.bootstrap_recomputed_{metric_name}",
                    int(artifact.get(metric_name, -1)) == actual
                    and int(summary.get(metric_name, -1)) == actual,
                    (
                        f"actual={actual}; artifact={artifact.get(metric_name)}; "
                        f"summary={summary.get(metric_name)}"
                    ),
                )
            )
        for effect_key in (
            "passes_predeclared_useful_effect",
            "passes_outcome_blind_deployment_candidate_gate",
        ):
            if effect_key in summary:
                checks.append(
                    AuditCheck(
                        f"{task}.{report_key}.{model}.bootstrap_{effect_key}",
                        summary.get(effect_key) is expected_effect,
                        f"actual={expected_effect}; reported={summary.get(effect_key)}",
                    )
                )
    for metric_name in (
        "estimate",
        "lower_95",
        "upper_95",
        "relative_brier_reduction",
    ):
        checks.append(
            AuditCheck(
                f"{task}.{report_key}.{model}.bootstrap_summary_{metric_name}",
                _close(summary.get(metric_name), artifact.get(metric_name)),
                (
                    f"summary={summary.get(metric_name)}; "
                    f"artifact={artifact.get(metric_name)}"
                ),
            )
        )


def _audit_covariate_ablation(
    checks: list[AuditCheck],
    *,
    task: str,
    task_report: Mapping[str, object],
    dataset_by_id: Mapping[object, Mapping[str, object]],
    eligible_rows: Sequence[Mapping[str, object]],
    predeclared_cutoff: str | None,
    seed: int,
    expected_replicates: int,
) -> None:
    """Recompute the frozen optional-covariate experiment and its gate."""

    ablation = task_report.get("covariate_ablation")
    if not isinstance(ablation, Mapping):
        return
    path = Path(str(ablation.get("predictions_path", "")))
    records = _jsonl(path)
    checks.append(
        AuditCheck(
            f"{task}.covariate_ablation.predictions_sha",
            _sha256(path) == ablation.get("predictions_sha256"),
            _sha256(path),
        )
    )
    raw_models = ablation.get("available_families", [])
    available_models = (
        {str(value) for value in raw_models}
        if isinstance(raw_models, list)
        else set()
    )
    cutoff = predeclared_cutoff or str(ablation.get("cutoff", ""))
    expected_test_rows = [
        row for row in eligible_rows if str(row.get("start")) >= cutoff
    ]
    expected_train_rows = [
        row
        for row in eligible_rows
        if str(row.get("start")) < cutoff and str(row.get("end")) < cutoff
    ]
    expected_ids = {row.get("observation_id") for row in expected_test_rows}
    models_by_id: defaultdict[object, set[str]] = defaultdict(set)
    identities: set[tuple[object, str]] = set()
    identity_unique = True
    aligned = True
    probabilities_valid = True
    expected_fold = (
        "predeclared-final-panel"
        if predeclared_cutoff is not None
        else "final-tail"
    )
    for record in records:
        model = str(record.get("model", ""))
        identity = (record.get("observation_id"), model)
        if identity in identities:
            identity_unique = False
        identities.add(identity)
        models_by_id[record.get("observation_id")].add(model)
        source = dataset_by_id.get(record.get("observation_id"))
        if source is None:
            aligned = False
            continue
        aligned = aligned and (
            record.get("task") == task
            and record.get("split") == "temporal-covariate-ablation"
            and record.get("fold") == expected_fold
            and record.get("outcome") == source.get(task)
            and record.get("norad_id") == source.get("norad_id")
            and record.get("station_id") == source.get("station_id")
            and record.get("start") == source.get("start")
        )
        try:
            probability = float(record["probability"])
        except (KeyError, TypeError, ValueError):
            probabilities_valid = False
        else:
            probabilities_valid = (
                probabilities_valid
                and math.isfinite(probability)
                and 0 <= probability <= 1
            )
    actual_ids = set(models_by_id)
    checks.extend(
        (
            AuditCheck(
                f"{task}.covariate_ablation.membership",
                bool(records)
                and actual_ids == expected_ids
                and all(models == available_models for models in models_by_id.values()),
                f"actual={len(actual_ids)}; expected={len(expected_ids)}",
            ),
            AuditCheck(
                f"{task}.covariate_ablation.identity_unique",
                identity_unique,
                f"records={len(records)}",
            ),
            AuditCheck(
                f"{task}.covariate_ablation.records_match_dataset",
                aligned and probabilities_valid,
                f"records={len(records)}",
            ),
            AuditCheck(
                f"{task}.covariate_ablation.boundary_and_counts",
                ablation.get("cutoff") == cutoff
                and int(ablation.get("train_count", -1)) == len(expected_train_rows)
                and int(ablation.get("test_count", -1)) == len(expected_test_rows),
                (
                    f"cutoff={cutoff}; train={len(expected_train_rows)}; "
                    f"test={len(expected_test_rows)}"
                ),
            ),
        )
    )
    metrics = ablation.get("metrics", {})
    if not isinstance(metrics, Mapping) or set(metrics) != available_models:
        checks.append(
            AuditCheck(
                f"{task}.covariate_ablation.metrics_shape",
                False,
                f"models={sorted(available_models)}",
            )
        )
    else:
        for model, reported in metrics.items():
            selected = [record for record in records if record.get("model") == model]
            if not selected or not isinstance(reported, Mapping):
                checks.append(
                    AuditCheck(
                        f"{task}.covariate_ablation.{model}.metrics_shape",
                        False,
                        f"predictions={len(selected)}",
                    )
                )
                continue
            recomputed = _recompute_probability_metrics(
                [int(record["outcome"]) for record in selected],
                [float(record["probability"]) for record in selected],
            )
            for metric_name, actual in recomputed.items():
                checks.append(
                    AuditCheck(
                        f"{task}.covariate_ablation.{model}.{metric_name}",
                        _close(actual, reported.get(metric_name)),
                        f"actual={actual}; expected={reported.get(metric_name)}",
                    )
                )

    paired = ablation.get("paired_bootstrap_vs_operational", {})
    deployment = ablation.get("deployment_candidate_gate", {})
    by_model = (
        deployment.get("by_model", {})
        if isinstance(deployment, Mapping)
        else {}
    )
    expected_decisions: dict[str, bool] = {}
    for model in sorted(available_models - {"operational_logit"}):
        cluster_decisions: list[bool] = []
        for cluster_name in ("satellite_cluster", "station_cluster_sensitivity"):
            cluster_report = (
                paired.get(cluster_name, {})
                if isinstance(paired, Mapping)
                else {}
            )
            summary = (
                cluster_report.get(model, {})
                if isinstance(cluster_report, Mapping)
                else {}
            )
            if not isinstance(summary, Mapping) or "artifact_path" not in summary:
                cluster_decisions.append(False)
                continue
            _audit_bootstrap(
                checks,
                task=task,
                report_key="covariate_ablation",
                model=f"{cluster_name}.{model}",
                summary=summary,
                records=records,
                seed=seed,
                expected_replicates=expected_replicates,
            )
            cluster_decisions.append(
                summary.get("passes_outcome_blind_deployment_candidate_gate")
                is True
            )
        expected_decisions[model] = (
            len(cluster_decisions) == 2 and all(cluster_decisions)
        )
    checks.append(
        AuditCheck(
            f"{task}.covariate_ablation.deployment_gate",
            isinstance(by_model, Mapping)
            and dict(by_model) == expected_decisions,
            f"actual={dict(by_model) if isinstance(by_model, Mapping) else None}; expected={expected_decisions}",
        )
    )


def _audit_replay_bootstraps(
    checks: list[AuditCheck],
    *,
    assignments: list[Mapping[str, object]],
    summaries: Mapping[str, object],
    seed: int,
    expected_replicates: int,
    namespace: str = "scheduling",
) -> None:
    import numpy as np

    for key, raw_summary in summaries.items():
        if not isinstance(raw_summary, Mapping):
            checks.append(
                AuditCheck(f"{namespace}.bootstrap.{key}.shape", False, "malformed")
            )
            continue
        path = Path(str(raw_summary.get("artifact_path", "")))
        digest = _sha256(path)
        checks.append(
            AuditCheck(
                f"{namespace}.bootstrap.{key}.sha",
                digest == raw_summary.get("artifact_sha256"),
                digest,
            )
        )
        artifact = json.loads(path.read_text(encoding="utf-8"))
        draws = [float(value) for value in artifact.get("replicates", [])]
        candidate = str(artifact.get("candidate", ""))
        baseline = str(artifact.get("baseline", ""))
        checks.append(
            AuditCheck(
                f"{namespace}.bootstrap.{key}.identity",
                artifact.get("schema_version") == "scheduling-day-bootstrap-v1"
                and baseline == str(key)
                and raw_summary.get("candidate") == candidate
                and raw_summary.get("baseline") == baseline,
                f"candidate={candidate}; baseline={baseline}",
            )
        )
        daily: defaultdict[str, defaultdict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for assignment in assignments:
            scheduler = str(assignment.get("scheduler", ""))
            if scheduler in {candidate, baseline}:
                daily[str(assignment.get("start", ""))[:10]][scheduler] += int(
                    assignment.get("outcome", 0)
                )
        days = sorted(daily)
        differences = np.asarray(
            [daily[day][candidate] - daily[day][baseline] for day in days],
            dtype=float,
        )
        rng = np.random.default_rng(seed)
        recomputed = [
            float(np.mean(rng.choice(differences, size=len(differences), replace=True)))
            for _ in range(expected_replicates)
        ] if len(differences) else []
        checks.append(
            AuditCheck(
                f"{namespace}.bootstrap.{key}.draws_recomputed",
                len(draws) == len(recomputed)
                and all(
                    _close(actual, expected)
                    for actual, expected in zip(draws, recomputed, strict=True)
                ),
                f"draws={len(draws)}; recomputed={len(recomputed)}",
            )
        )
        for metric_name, actual in (
            ("day_count", len(days)),
            (
                "estimate_mean_daily_success_difference",
                float(differences.mean()) if len(differences) else None,
            ),
            ("lower_95", _quantile(recomputed, 0.025) if recomputed else None),
            ("upper_95", _quantile(recomputed, 0.975) if recomputed else None),
            ("replicate_count", len(recomputed)),
        ):
            artifact_value = (
                artifact.get(metric_name)
                if metric_name != "replicate_count"
                else len(draws)
            )
            checks.append(
                AuditCheck(
                    f"{namespace}.bootstrap.{key}.{metric_name}",
                    _close(actual, artifact_value)
                    and _close(actual, raw_summary.get(metric_name)),
                    (
                        f"actual={actual}; artifact={artifact_value}; "
                        f"summary={raw_summary.get(metric_name)}"
                    ),
                )
            )


def _audit_scheduling_replay(
    checks: list[AuditCheck],
    *,
    replay: Mapping[str, object],
    prediction_records: Sequence[Mapping[str, object]],
    dataset_by_id: Mapping[object, Mapping[str, object]],
    evaluation: Mapping[str, object],
    namespace: str,
    expected_task: str,
    expected_split: str,
) -> None:
    assignment_path = Path(str(replay.get("assignments_path", "")))
    digest = _sha256(assignment_path)
    checks.append(
        AuditCheck(
            f"{namespace}.assignments_sha",
            digest == replay.get("assignments_sha256"),
            digest,
        )
    )
    checks.append(
        AuditCheck(
            f"{namespace}.outcome_task",
            replay.get("outcome_task") == expected_task,
            f"actual={replay.get('outcome_task')}; expected={expected_task}",
        )
    )
    assignments = _jsonl(assignment_path)
    by_scheduler: defaultdict[str, list[Mapping[str, object]]] = defaultdict(list)
    for assignment in assignments:
        by_scheduler[str(assignment["scheduler"])].append(assignment)
    expected_schedulers = {
        "chronological",
        "maximum_elevation",
        "longest_duration",
        "probability_milp",
        "oracle_milp_upper_bound",
    }
    replay_metrics = replay.get("metrics", {})
    checks.append(
        AuditCheck(
            f"{namespace}.scheduler_grid_complete",
            isinstance(replay_metrics, Mapping)
            and set(replay_metrics) == expected_schedulers
            and set(by_scheduler) <= expected_schedulers,
            (
                f"metrics={sorted(replay_metrics) if isinstance(replay_metrics, Mapping) else None}; "
                f"assignments={sorted(by_scheduler)}"
            ),
        )
    )
    temporal_full = {
        record.get("observation_id"): record
        for record in prediction_records
        if record.get("split") == expected_split
        and record.get("model") == "full_logit"
        and record.get("task") == expected_task
    }
    checks.append(
        AuditCheck(
            f"{namespace}.candidate_count",
            replay.get("candidate_count") == len(temporal_full),
            f"actual={replay.get('candidate_count')}; expected={len(temporal_full)}",
        )
    )
    candidate_rows = [
        dataset_by_id[identifier]
        for identifier in temporal_full
        if identifier in dataset_by_id
    ]
    natural_conflicts = 0
    by_station: defaultdict[object, list[Mapping[str, object]]] = defaultdict(list)
    for row in candidate_rows:
        by_station[row.get("station_id")].append(row)
    for station_rows in by_station.values():
        ordered = sorted(
            station_rows,
            key=lambda item: (str(item.get("start")), str(item.get("end"))),
        )
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if str(right.get("start")) >= str(left.get("end")):
                    break
                if str(left.get("start")) < str(right.get("end")):
                    natural_conflicts += 1
    checks.append(
        AuditCheck(
            f"{namespace}.natural_receiver_conflict_pairs",
            replay.get("natural_receiver_conflict_pairs") == natural_conflicts,
            (
                f"actual={replay.get('natural_receiver_conflict_pairs')}; "
                f"expected={natural_conflicts}"
            ),
        )
    )
    replay_rows_aligned = True
    replay_identity_unique = True
    seen_replay: set[tuple[str, object]] = set()
    for assignment in assignments:
        scheduler_name = str(assignment.get("scheduler", ""))
        identity = (scheduler_name, assignment.get("observation_id"))
        if identity in seen_replay:
            replay_identity_unique = False
        seen_replay.add(identity)
        source = dataset_by_id.get(assignment.get("observation_id"))
        prediction = temporal_full.get(assignment.get("observation_id"))
        if source is None or prediction is None:
            replay_rows_aligned = False
            continue
        expected_outcome = (
            source.get("signal_present")
            if expected_task == "signal_present"
            else _recompute_reception_success(source)
        )
        replay_rows_aligned = replay_rows_aligned and (
            assignment.get("norad_id") == source.get("norad_id")
            and assignment.get("station_id") == source.get("station_id")
            and assignment.get("start") == source.get("start")
            and assignment.get("end") == source.get("end")
            and assignment.get("outcome") == expected_outcome
            and _close(
                assignment.get("predicted_probability"),
                prediction.get("probability"),
            )
            and (
                scheduler_name != "oracle_milp_upper_bound"
                or assignment.get("outcome") == 1
            )
        )
    checks.extend(
        (
            AuditCheck(
                f"{namespace}.assignment_identity_unique",
                replay_identity_unique,
                f"rows={len(assignments)}",
            ),
            AuditCheck(
                f"{namespace}.assignments_match_dataset_predictions",
                replay_rows_aligned,
                f"rows={len(assignments)}",
            ),
        )
    )
    selected_conflicts = 0
    for scheduler_rows in by_scheduler.values():
        selected_by_station: defaultdict[object, list[tuple[str, str]]] = defaultdict(
            list
        )
        for assignment in scheduler_rows:
            selected_by_station[assignment.get("station_id")].append(
                (str(assignment.get("start")), str(assignment.get("end")))
            )
        for intervals in selected_by_station.values():
            ordered = sorted(intervals)
            selected_conflicts += sum(
                current_start < previous_end
                for (_, previous_end), (current_start, _) in zip(
                    ordered, ordered[1:]
                )
            )
    checks.append(
        AuditCheck(
            f"{namespace}.assignments_conflict_free",
            selected_conflicts == 0,
            f"overlap_pairs={selected_conflicts}",
        )
    )
    if isinstance(replay_metrics, Mapping):
        for scheduler, raw_metrics in replay_metrics.items():
            if not isinstance(raw_metrics, Mapping):
                checks.append(
                    AuditCheck(
                        f"{namespace}.{scheduler}.metrics_shape", False, "malformed"
                    )
                )
                continue
            selected = by_scheduler[str(scheduler)]
            successful_by_satellite: defaultdict[int, int] = defaultdict(int)
            for item in selected:
                successful_by_satellite[int(item["norad_id"])] += int(item["outcome"])
            expected_values = {
                "selected_observations": len(selected),
                "realized_successful_samples": sum(successful_by_satellite.values()),
                "predicted_expected_samples": sum(
                    float(item["predicted_probability"]) for item in selected
                ),
                "satellites_covered": sum(
                    value > 0 for value in successful_by_satellite.values()
                ),
                "successful_samples_by_satellite": {
                    str(key): value
                    for key, value in sorted(successful_by_satellite.items())
                },
            }
            for metric_name, expected in expected_values.items():
                actual = raw_metrics.get(metric_name)
                passed = (
                    _close(actual, expected)
                    if metric_name == "predicted_expected_samples"
                    else actual == expected
                )
                checks.append(
                    AuditCheck(
                        f"{namespace}.{scheduler}.{metric_name}",
                        passed,
                        f"actual={actual}; expected={expected}",
                    )
                )
    replay_bootstraps = replay.get("paired_day_bootstrap_vs_probability_milp", {})
    if isinstance(replay_bootstraps, Mapping):
        _audit_replay_bootstraps(
            checks,
            assignments=assignments,
            summaries=replay_bootstraps,
            seed=int(evaluation.get("random_seed", -1)),
            expected_replicates=int(evaluation.get("bootstrap_replicates", -1)),
            namespace=namespace,
        )


def _audit_composed_prediction_file(
    checks: list[AuditCheck],
    *,
    name: str,
    path: Path,
    expected_sha256: object,
    split_report: Mapping[str, object],
    dataset_by_id: Mapping[object, Mapping[str, object]],
    expected_ids: set[object],
    expected_split: str,
    expected_fold: str,
) -> list[Mapping[str, object]]:
    """Audit one composed-reception prediction grid independently."""

    digest = _sha256(path)
    checks.append(
        AuditCheck(
            f"{name}.predictions_sha",
            digest == expected_sha256,
            digest,
        )
    )
    records = _jsonl(path)
    expected_models = {
        "global_rate",
        "group_rate",
        "geometry_logit",
        "full_logit",
    }
    model_grid: defaultdict[object, set[object]] = defaultdict(set)
    seen: set[tuple[object, object]] = set()
    unique = True
    aligned = True
    factors_valid = True
    for record in records:
        identity = (record.get("observation_id"), record.get("model"))
        if identity in seen:
            unique = False
        seen.add(identity)
        model_grid[record.get("observation_id")].add(record.get("model"))
        source = dataset_by_id.get(record.get("observation_id"))
        endpoint = _recompute_reception_success(source) if source else None
        aligned = aligned and bool(
            source
            and record.get("task") == "reception_success"
            and record.get("split") == expected_split
            and record.get("fold") == expected_fold
            and record.get("outcome") == endpoint
            and record.get("norad_id") == source.get("norad_id")
            and record.get("station_id") == source.get("station_id")
            and record.get("start") == source.get("start")
        )
        try:
            probability = float(record["probability"])
            signal_probability = float(record["signal_probability"])
            decode_probability = float(
                record["decode_probability_given_signal"]
            )
        except (KeyError, TypeError, ValueError):
            factors_valid = False
        else:
            factors_valid = factors_valid and (
                math.isfinite(probability)
                and math.isfinite(signal_probability)
                and math.isfinite(decode_probability)
                and 0 <= probability <= 1
                and 0 <= signal_probability <= 1
                and 0 <= decode_probability <= 1
                and math.isclose(
                    probability,
                    signal_probability * decode_probability,
                    rel_tol=0,
                    abs_tol=1e-12,
                )
            )
    checks.extend(
        (
            AuditCheck(f"{name}.prediction_identity_unique", unique, f"rows={len(records)}"),
            AuditCheck(
                f"{name}.prediction_membership",
                set(model_grid) == expected_ids,
                f"actual={len(model_grid)}; expected={len(expected_ids)}",
            ),
            AuditCheck(
                f"{name}.prediction_model_grid_complete",
                bool(model_grid)
                and all(models == expected_models for models in model_grid.values()),
                f"observations={len(model_grid)}",
            ),
            AuditCheck(
                f"{name}.predictions_match_normalized_dataset",
                aligned,
                f"predictions={len(records)}",
            ),
            AuditCheck(
                f"{name}.probability_factors_multiply_exactly",
                factors_valid,
                f"predictions={len(records)}",
            ),
            AuditCheck(
                f"{name}.prediction_row_count",
                len(records) == 4 * len(expected_ids),
                f"actual={len(records)}; expected={4 * len(expected_ids)}",
            ),
        )
    )

    reported_metrics = split_report.get("metrics", {})
    reported_decisions = split_report.get("decision_metrics_at_0_5", {})
    metrics_shape = (
        isinstance(reported_metrics, Mapping)
        and set(reported_metrics) == expected_models
        and isinstance(reported_decisions, Mapping)
        and set(reported_decisions) == expected_models
    )
    checks.append(
        AuditCheck(f"{name}.reported_metric_grid", metrics_shape, "four models")
    )
    if metrics_shape:
        assert isinstance(reported_metrics, Mapping)
        assert isinstance(reported_decisions, Mapping)
        for model in sorted(expected_models):
            selected = [record for record in records if record.get("model") == model]
            outcomes = [int(record["outcome"]) for record in selected]
            probabilities = [float(record["probability"]) for record in selected]
            probability_report = reported_metrics.get(model)
            decision_report = reported_decisions.get(model)
            if not isinstance(probability_report, Mapping) or not isinstance(
                decision_report, Mapping
            ):
                checks.append(
                    AuditCheck(f"{name}.{model}.metric_shape", False, "malformed")
                )
                continue
            for metric_name, actual in _recompute_probability_metrics(
                outcomes, probabilities
            ).items():
                checks.append(
                    AuditCheck(
                        f"{name}.{model}.{metric_name}",
                        _close(actual, probability_report.get(metric_name)),
                        f"actual={actual}; expected={probability_report.get(metric_name)}",
                    )
                )
            for metric_name, actual in _recompute_decision_metrics(
                outcomes, probabilities
            ).items():
                checks.append(
                    AuditCheck(
                        f"{name}.{model}.decision.{metric_name}",
                        _close(actual, decision_report.get(metric_name)),
                        f"actual={actual}; expected={decision_report.get(metric_name)}",
                    )
                )
    return records


def audit_publication_artifacts(
    *,
    dataset_path: Path,
    dataset_manifest_path: Path,
    evaluation_path: Path,
) -> Mapping[str, object]:
    """Recompute primary counts/metrics without importing model/evaluation code."""

    checks: list[AuditCheck] = []
    dataset_manifest = json.loads(dataset_manifest_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    dataset_sha = _sha256(dataset_path)
    dataset_manifest_sha = _sha256(dataset_manifest_path)
    evaluation_sha = _sha256(evaluation_path)
    checks.append(
        AuditCheck(
            "dataset_sha_matches_manifest",
            dataset_sha == dataset_manifest.get("dataset_sha256"),
            dataset_sha,
        )
    )
    checks.append(
        AuditCheck(
            "dataset_sha_matches_evaluation",
            dataset_sha == evaluation.get("dataset_sha256"),
            dataset_sha,
        )
    )
    dataset_config_sha = dataset_manifest.get("config_sha256")
    evaluation_config_sha = evaluation.get("config_sha256")
    checks.append(
        AuditCheck(
            "config_identity_matches",
            isinstance(dataset_config_sha, str)
            and len(dataset_config_sha) == 64
            and dataset_config_sha == evaluation_config_sha,
            (
                f"dataset={dataset_config_sha}; "
                f"evaluation={evaluation_config_sha}"
            ),
        )
    )
    checks.append(
        AuditCheck(
            "study_identity_matches",
            isinstance(dataset_manifest.get("study_id"), str)
            and dataset_manifest.get("study_id") == evaluation.get("study_id"),
            (
                f"dataset={dataset_manifest.get('study_id')}; "
                f"evaluation={evaluation.get('study_id')}"
            ),
        )
    )
    publication_config: Mapping[str, object] = {}
    predeclared_temporal_cutoff: str | None = None
    config_path_value = dataset_manifest.get("config_path")
    if config_path_value is not None:
        try:
            config_path = Path(str(config_path_value))
            loaded_config = json.loads(config_path.read_text(encoding="utf-8"))
            config_file_valid = (
                isinstance(loaded_config, Mapping)
                and _sha256(config_path) == dataset_config_sha
            )
            if isinstance(loaded_config, Mapping):
                publication_config = loaded_config
                sampling = loaded_config.get("sampling_intervals")
                if (
                    isinstance(sampling, list)
                    and sampling
                    and isinstance(sampling[-1], Mapping)
                    and isinstance(sampling[-1].get("start"), str)
                ):
                    predeclared_temporal_cutoff = str(sampling[-1]["start"])
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            config_file_valid = False
        checks.append(
            AuditCheck(
                "config_file_sha_matches_manifest",
                config_file_valid,
                str(config_path_value),
            )
        )
    if "publication_count_gate" in dataset_manifest:
        manifest_requirements = dataset_manifest.get("publication_requirements")
        checks.append(
            AuditCheck(
                "publication_count_gate_was_enforced",
                dataset_manifest.get("publication_count_gate") == "pass"
                and evaluation.get("publication_count_gate_enforced") is True
                and isinstance(manifest_requirements, Mapping)
                and evaluation.get("publication_count_requirements")
                == manifest_requirements,
                (
                    f"manifest_gate={dataset_manifest.get('publication_count_gate')}; "
                    f"evaluation_enforced={evaluation.get('publication_count_gate_enforced')}"
                ),
            )
        )
    dataset_rows = _jsonl(dataset_path)
    forbidden_normalized_fields = {
        "observer",
        "vetted_user",
        "waterfall_status_user",
        "payload",
        "waterfall",
        "archive_url",
        "client_metadata",
    }
    leaked_fields = sorted(
        {
            field
            for row in dataset_rows
            for field in forbidden_normalized_fields
            if field in row
        }
    )
    checks.append(
        AuditCheck(
            "normalized_data_minimization",
            not leaked_fields,
            f"forbidden_fields_present={leaked_fields}",
        )
    )
    known_signal = sum(row.get("signal_present") in {0, 1} for row in dataset_rows)
    positive_signal = sum(row.get("signal_present") == 1 for row in dataset_rows)
    decode = sum(row.get("decode_success_given_signal") in {0, 1} for row in dataset_rows)
    positive_decode = sum(row.get("decode_success_given_signal") == 1 for row in dataset_rows)
    dataset_by_id: dict[object, Mapping[str, object]] = {}
    duplicate_dataset_id = False
    for row in dataset_rows:
        identifier = row.get("observation_id")
        if identifier in dataset_by_id:
            duplicate_dataset_id = True
        dataset_by_id[identifier] = row
    checks.append(
        AuditCheck(
            "normalized_observation_identity_unique",
            not duplicate_dataset_id and len(dataset_by_id) == len(dataset_rows),
            f"rows={len(dataset_rows)}; identities={len(dataset_by_id)}",
        )
    )
    for name, actual, expected_key in (
        ("known_signal_count", known_signal, "known_signal_rows"),
        ("positive_signal_count", positive_signal, "known_signal_positive_rows"),
        ("decode_count", decode, "conditional_decode_rows"),
        ("positive_decode_count", positive_decode, "conditional_decode_positive_rows"),
    ):
        expected = int(dataset_manifest.get(expected_key, -1))
        checks.append(AuditCheck(name, actual == expected, f"actual={actual}; expected={expected}"))

    if (
        "publication_count_gate" in dataset_manifest
        and isinstance(publication_config, Mapping)
    ):
        try:
            recomputed_requirements = _recompute_publication_count_requirements(
                dataset_rows, publication_config
            )
            reported_requirements = dataset_manifest.get(
                "publication_requirements"
            )
            evaluation_requirements = evaluation.get(
                "publication_count_requirements"
            )
            requirements_valid = (
                reported_requirements == recomputed_requirements
                and evaluation_requirements == recomputed_requirements
                and all(recomputed_requirements.values())
                and dataset_manifest.get("publication_count_gate") == "pass"
                and evaluation.get("publication_count_gate_enforced") is True
            )
            failed_requirements = sorted(
                name
                for name, passed in recomputed_requirements.items()
                if not passed
            )
        except (KeyError, TypeError, ValueError) as exc:
            requirements_valid = False
            failed_requirements = [f"{type(exc).__name__}: {exc}"]
        checks.append(
            AuditCheck(
                "publication_count_requirements_recomputed",
                requirements_valid,
                f"failed={failed_requirements}",
            )
        )

    availability = dataset_manifest.get("feature_availability")
    if (
        isinstance(availability, Mapping)
        and "historical_station_location_captured_present" in availability
    ):
        actual_metadata_statuses: defaultdict[str, int] = defaultdict(int)
        for row in dataset_rows:
            status = row.get("client_metadata_parse_status")
            if isinstance(status, str):
                actual_metadata_statuses[status] += 1
        receiver_fields = (
            "captured_receiver_radio_name",
            "captured_receiver_radio_version",
            "captured_receiver_driver",
            "captured_receiver_gain_mode",
            "captured_receiver_rf_gain_db",
            "captured_receiver_antenna_port",
            "captured_receiver_sample_rate_hz",
            "captured_receiver_ppm_error",
            "captured_receiver_bandwidth_hz",
        )
        actual_receiver_values = {
            name: sum(row.get(name) is not None for row in dataset_rows)
            for name in receiver_fields
        }
        capture_checks = (
            (
                "historical_capture_location_count",
                sum(
                    row.get("station_location_source")
                    == "satnogs-client-metadata"
                    and isinstance(row.get("station_location_recorded_at"), str)
                    and str(row.get("station_location_recorded_at"))
                    <= str(row.get("start"))
                    for row in dataset_rows
                ),
                availability.get("historical_station_location_captured_present"),
            ),
            (
                "current_api_station_location_fallback_count",
                sum(
                    row.get("station_location_source")
                    == "satnogs-api-current-station"
                    for row in dataset_rows
                ),
                availability.get("current_api_station_location_fallback_present"),
            ),
            (
                "client_metadata_parse_status_counts",
                dict(sorted(actual_metadata_statuses.items())),
                availability.get("client_metadata_parse_status_counts"),
            ),
            (
                "historical_receiver_configuration_count",
                sum(
                    row.get("captured_receiver_configuration_source")
                    == "satnogs-client-metadata"
                    and isinstance(
                        row.get("captured_receiver_configuration_recorded_at"), str
                    )
                    and str(row.get("captured_receiver_configuration_recorded_at"))
                    <= str(row.get("start"))
                    for row in dataset_rows
                ),
                availability.get("historical_receiver_configuration_present"),
            ),
            (
                "historical_receiver_configuration_value_counts",
                actual_receiver_values,
                availability.get("historical_receiver_configuration_value_counts"),
            ),
        )
        for name, actual, expected in capture_checks:
            checks.append(
                AuditCheck(
                    name,
                    actual == expected,
                    f"actual={actual}; expected={expected}",
                )
            )

    if (
        isinstance(availability, Mapping)
        and "historical_terrestrial_weather_complete_vector_present" in availability
    ):
        weather_fields = (
            "weather_air_temperature_c",
            "weather_relative_humidity_percent",
            "weather_surface_pressure_kpa",
            "weather_wind_speed_m_s",
            "weather_precipitation_corrected",
        )
        actual_weather_values = {
            name: sum(row.get(name) is not None for row in dataset_rows)
            for name in weather_fields
        }
        weather_checks = (
            (
                "historical_weather_snapshot_count",
                sum(row.get("weather_valid_at") is not None for row in dataset_rows),
                availability.get("historical_terrestrial_weather_snapshot_present"),
            ),
            (
                "historical_weather_any_value_count",
                sum(
                    any(row.get(name) is not None for name in weather_fields)
                    for row in dataset_rows
                ),
                availability.get("historical_terrestrial_weather_any_value_present"),
            ),
            (
                "historical_weather_complete_vector_count",
                sum(
                    all(row.get(name) is not None for name in weather_fields)
                    for row in dataset_rows
                ),
                availability.get(
                    "historical_terrestrial_weather_complete_vector_present"
                ),
            ),
            (
                "historical_weather_value_counts",
                actual_weather_values,
                availability.get("historical_terrestrial_weather_value_counts"),
            ),
        )
        for name, actual, expected in weather_checks:
            checks.append(
                AuditCheck(
                    name,
                    actual == expected,
                    f"actual={actual}; expected={expected}",
                )
            )
        weather_location_provenance_valid = all(
            row.get("weather_valid_at") is None
            or (
                row.get("station_location_source") == "satnogs-client-metadata"
                and isinstance(row.get("station_location_recorded_at"), str)
                and str(row.get("station_location_recorded_at"))
                <= str(row.get("start"))
            )
            for row in dataset_rows
        )
        checks.append(
            AuditCheck(
                "historical_weather_uses_capture_location_only",
                weather_location_provenance_valid,
                "every joined weather row must carry a pre-start client location",
            )
        )

    if "observed_transmitter_count" in dataset_manifest:
        observed_transmitters = {
            row.get("transmitter_uuid")
            for row in dataset_rows
            if isinstance(row.get("transmitter_uuid"), str)
        }
        selected_by_satellite = {
            int(item["norad_id"]): str(item["selection_transmitter_uuid"])
            for item in publication_config.get("targets", [])
            if isinstance(item, Mapping)
            and item.get("norad_id") is not None
            and item.get("selection_transmitter_uuid") is not None
        }
        matching_selected = sum(
            row.get("transmitter_uuid")
            == selected_by_satellite.get(int(row.get("norad_id", 0)))
            for row in dataset_rows
        )
        transmitter_checks = (
            (
                "observed_transmitter_count",
                len(observed_transmitters),
                "observed_transmitter_count",
            ),
            (
                "rows_matching_selection_transmitter",
                matching_selected,
                "normalized_rows_matching_selection_transmitter",
            ),
            (
                "rows_from_other_transmitters",
                len(dataset_rows) - matching_selected,
                "normalized_rows_from_other_transmitters",
            ),
        )
        for name, actual, expected_key in transmitter_checks:
            expected = int(dataset_manifest.get(expected_key, -1))
            checks.append(
                AuditCheck(name, actual == expected, f"actual={actual}; expected={expected}")
            )
        observed_satellites = {
            int(row["norad_id"])
            for row in dataset_rows
            if row.get("norad_id") is not None
        }
        checks.append(
            AuditCheck(
                "normalized_satellite_count",
                len(observed_satellites)
                == int(dataset_manifest.get("normalized_satellites", -1)),
                (
                    f"actual={len(observed_satellites)}; "
                    f"expected={dataset_manifest.get('normalized_satellites')}"
                ),
            )
        )
        recomputed_target_counts = []
        for target in publication_config.get("targets", []):
            if not isinstance(target, Mapping):
                continue
            norad_id = int(target["norad_id"])
            target_rows = [
                row for row in dataset_rows if int(row.get("norad_id", 0)) == norad_id
            ]
            signal_rows = [
                row for row in target_rows if row.get("signal_present") in {0, 1}
            ]
            decode_rows = [
                row
                for row in target_rows
                if row.get("decode_success_given_signal") in {0, 1}
            ]
            recomputed_target_counts.append(
                {
                    "norad_id": norad_id,
                    "normalized_rows": len(target_rows),
                    "known_signal_rows": len(signal_rows),
                    "known_signal_positive_rows": sum(
                        row.get("signal_present") == 1 for row in signal_rows
                    ),
                    "conditional_decode_rows": len(decode_rows),
                    "conditional_decode_positive_rows": sum(
                        row.get("decode_success_given_signal") == 1
                        for row in decode_rows
                    ),
                    "observed_transmitter_count": len(
                        {
                            row.get("transmitter_uuid")
                            for row in target_rows
                            if isinstance(row.get("transmitter_uuid"), str)
                        }
                    ),
                    "rows_matching_selection_transmitter": sum(
                        row.get("transmitter_uuid")
                        == target.get("selection_transmitter_uuid")
                        for row in target_rows
                    ),
                }
            )
        checks.append(
            AuditCheck(
                "per_target_counts",
                dataset_manifest.get("per_target_counts") == recomputed_target_counts,
                f"targets={len(recomputed_target_counts)}",
            )
        )

    if "sampling_intervals" in dataset_manifest:
        configured_sampling = publication_config.get("sampling_intervals")
        if not isinstance(configured_sampling, list) or not configured_sampling:
            configured_sampling = [
                {
                    "start": publication_config.get("start"),
                    "end": publication_config.get("end"),
                }
            ]
        checks.append(
            AuditCheck(
                "sampling_intervals_match_config",
                dataset_manifest.get("sampling_intervals") == configured_sampling,
                f"intervals={len(configured_sampling)}",
            )
        )

    for task, task_report in evaluation.get("tasks", {}).items():
        if not isinstance(task_report, Mapping) or task_report.get("status") != "evaluated":
            continue
        predictions_path = Path(str(task_report["predictions_path"]))
        predictions_sha = _sha256(predictions_path)
        checks.append(
            AuditCheck(
                f"{task}.predictions_sha",
                predictions_sha == task_report.get("predictions_sha256"),
                predictions_sha,
            )
        )
        records = _jsonl(predictions_path)
        identities: set[tuple[object, object, object, object]] = set()
        duplicate = False
        for record in records:
            identity = (
                record.get("observation_id"),
                record.get("split"),
                record.get("fold"),
                record.get("model"),
            )
            if identity in identities:
                duplicate = True
            identities.add(identity)
        checks.append(AuditCheck(f"{task}.prediction_identity_unique", not duplicate, f"rows={len(records)}"))
        expected_models = {
            "global_rate",
            "group_rate",
            "geometry_logit",
            "full_logit",
        }
        models_by_fold_row: defaultdict[
            tuple[object, object, object], set[object]
        ] = defaultdict(set)
        prediction_dataset_aligned = True
        prediction_probabilities_valid = True
        for record in records:
            models_by_fold_row[
                (
                    record.get("observation_id"),
                    record.get("split"),
                    record.get("fold"),
                )
            ].add(record.get("model"))
            source = dataset_by_id.get(record.get("observation_id"))
            if source is None:
                prediction_dataset_aligned = False
                continue
            prediction_dataset_aligned = prediction_dataset_aligned and (
                record.get("task") == task
                and record.get("outcome") == source.get(task)
                and record.get("norad_id") == source.get("norad_id")
                and record.get("station_id") == source.get("station_id")
                and record.get("start") == source.get("start")
            )
            try:
                probability = float(record["probability"])
            except (KeyError, TypeError, ValueError):
                prediction_probabilities_valid = False
            else:
                prediction_probabilities_valid = (
                    prediction_probabilities_valid
                    and math.isfinite(probability)
                    and 0 <= probability <= 1
                )
        checks.append(
            AuditCheck(
                f"{task}.predictions_match_normalized_dataset",
                prediction_dataset_aligned,
                f"predictions={len(records)}",
            )
        )
        checks.append(
            AuditCheck(
                f"{task}.prediction_probabilities_valid",
                prediction_probabilities_valid,
                f"predictions={len(records)}",
            )
        )
        incomplete_model_rows = sum(
            models != expected_models for models in models_by_fold_row.values()
        )
        checks.append(
            AuditCheck(
                f"{task}.prediction_model_grid_complete",
                bool(models_by_fold_row) and incomplete_model_rows == 0,
                (
                    f"fold_rows={len(models_by_fold_row)}; "
                    f"incomplete={incomplete_model_rows}"
                ),
            )
        )
        folds_by_split_row: defaultdict[tuple[object, object], set[object]] = (
            defaultdict(set)
        )
        for record in records:
            folds_by_split_row[
                (record.get("split"), record.get("observation_id"))
            ].add(record.get("fold"))
        checks.append(
            AuditCheck(
                f"{task}.prediction_fold_unique_per_split_row",
                all(len(folds) == 1 for folds in folds_by_split_row.values()),
                f"split_rows={len(folds_by_split_row)}",
            )
        )

        temporal_report = task_report.get("temporal", {})
        temporal_records = [
            record for record in records if record.get("split") == "temporal"
        ]
        temporal_ids = {
            record.get("observation_id") for record in temporal_records
        }
        eligible_rows = sorted(
            (
                row
                for row in dataset_rows
                if row.get(task) in {0, 1}
            ),
            key=lambda row: (str(row.get("start")), int(row.get("observation_id", -1))),
        )
        temporal_membership_valid = False
        temporal_counts_valid = False
        temporal_bounds_valid = False
        if temporal_ids and isinstance(temporal_report, Mapping):
            test_rows = [
                row for row in eligible_rows if row.get("observation_id") in temporal_ids
            ]
            if test_rows:
                test_start = min(str(row.get("start")) for row in test_rows)
                membership_cutoff = predeclared_temporal_cutoff or test_start
                train_rows = [
                    row
                    for row in eligible_rows
                    if str(row.get("start")) < membership_cutoff
                    and str(row.get("end")) < membership_cutoff
                ]
                expected_test_ids = {
                    row.get("observation_id")
                    for row in eligible_rows
                    if str(row.get("start")) >= membership_cutoff
                }
                expected_fold = (
                    "predeclared-final-panel"
                    if predeclared_temporal_cutoff is not None
                    else "final-20-percent"
                )
                temporal_membership_valid = (
                    temporal_ids == expected_test_ids
                    and all(
                        record.get("fold") == expected_fold
                        for record in temporal_records
                    )
                )
                temporal_counts_valid = bool(train_rows) and (
                    temporal_report.get("train_count") == len(train_rows)
                    and temporal_report.get("test_count") == len(test_rows)
                )
                temporal_bounds_valid = bool(train_rows) and (
                    temporal_report.get("train_start")
                    == min(str(row.get("start")) for row in train_rows)
                    and temporal_report.get("train_end")
                    == max(str(row.get("end")) for row in train_rows)
                    and temporal_report.get("test_start")
                    == min(str(row.get("start")) for row in test_rows)
                    and temporal_report.get("test_end")
                    == max(str(row.get("end")) for row in test_rows)
                    and max(str(row.get("end")) for row in train_rows) < test_start
                    and (
                        predeclared_temporal_cutoff is None
                        or (
                            evaluation.get("predeclared_temporal_cutoff")
                            == predeclared_temporal_cutoff
                            and max(str(row.get("end")) for row in train_rows)
                            < predeclared_temporal_cutoff
                            <= test_start
                        )
                    )
                )
        if len(eligible_rows) >= 20:
            checks.extend(
                (
                    AuditCheck(
                        f"{task}.temporal_membership_is_complete_suffix",
                        temporal_membership_valid,
                        f"test_ids={len(temporal_ids)}; eligible={len(eligible_rows)}",
                    ),
                    AuditCheck(
                        f"{task}.temporal_counts_recomputed",
                        temporal_counts_valid,
                        (
                            f"reported_train={temporal_report.get('train_count') if isinstance(temporal_report, Mapping) else None}; "
                            f"reported_test={temporal_report.get('test_count') if isinstance(temporal_report, Mapping) else None}"
                        ),
                    ),
                    AuditCheck(
                        f"{task}.temporal_bounds_recomputed",
                        temporal_bounds_valid,
                        "training timestamps must be strictly earlier than test timestamps",
                    ),
                )
            )
        for report_key, split_name, group_field in (
            ("loso_satellite", "loso-satellite", "norad_id"),
            ("loso_station", "loso-station", "station_id"),
        ):
            if report_key not in task_report:
                continue
            split_records = [
                record for record in records if record.get("split") == split_name
            ]
            split_ids = {record.get("observation_id") for record in split_records}
            folds_match = all(
                str(record.get("fold"))
                == str(dataset_by_id[record.get("observation_id")].get(group_field))
                for record in split_records
                if record.get("observation_id") in dataset_by_id
            )
            checks.append(
                AuditCheck(
                    f"{task}.{split_name}.membership_and_folds",
                    bool(split_records)
                    and split_ids == temporal_ids
                    and folds_match,
                    f"rows={len(split_records)}; unique_ids={len(split_ids)}",
                )
            )
        _audit_bootstraps(
            checks,
            task=str(task),
            task_report=task_report,
            records=records,
            seed=int(evaluation.get("random_seed", -1)),
            expected_replicates=int(evaluation.get("bootstrap_replicates", -1)),
        )
        temporal = temporal_records
        reported_metrics = task_report["temporal"]["metrics"]
        for model, metrics in reported_metrics.items():
            selected = [record for record in temporal if record.get("model") == model]
            outcomes = [int(record["outcome"]) for record in selected]
            probabilities = [
                min(1 - 1e-8, max(1e-8, float(record["probability"])))
                for record in selected
            ]
            recomputed_metrics = _recompute_probability_metrics(
                outcomes, probabilities
            )
            for metric_name, actual in recomputed_metrics.items():
                expected = metrics[metric_name]
                checks.append(
                    AuditCheck(
                        f"{task}.temporal.{model}.{metric_name}",
                        _close(actual, expected),
                        f"actual={actual}; expected={expected}",
                    )
                )

        for report_key, split_name in (
            ("loso_satellite", "loso-satellite"),
            ("loso_station", "loso-station"),
        ):
            split_report = task_report.get(report_key, {})
            reported = (
                split_report.get("metrics", {})
                if isinstance(split_report, Mapping)
                else {}
            )
            split_records = [
                record for record in records if record.get("split") == split_name
            ]
            if not isinstance(reported, Mapping):
                checks.append(
                    AuditCheck(f"{task}.{report_key}.metrics_shape", False, "malformed")
                )
                continue
            for model, model_report in reported.items():
                selected = [
                    record
                    for record in split_records
                    if record.get("model") == model
                ]
                if not isinstance(model_report, Mapping) or not selected:
                    checks.append(
                        AuditCheck(
                            f"{task}.{report_key}.{model}.metrics_shape",
                            False,
                            f"predictions={len(selected)}",
                        )
                    )
                    continue
                pooled = model_report.get("pooled", {})
                recomputed = _recompute_probability_metrics(
                    [int(record["outcome"]) for record in selected],
                    [float(record["probability"]) for record in selected],
                )
                for metric_name, actual in recomputed.items():
                    expected = (
                        pooled.get(metric_name)
                        if isinstance(pooled, Mapping)
                        else None
                    )
                    checks.append(
                        AuditCheck(
                            f"{task}.{report_key}.{model}.{metric_name}",
                            _close(actual, expected),
                            f"actual={actual}; expected={expected}",
                        )
                    )
                fold_aurocs: list[float] = []
                for fold in sorted({str(record.get("fold")) for record in selected}):
                    fold_rows = [
                        record for record in selected if str(record.get("fold")) == fold
                    ]
                    fold_metrics = _recompute_probability_metrics(
                        [int(record["outcome"]) for record in fold_rows],
                        [float(record["probability"]) for record in fold_rows],
                    )
                    if fold_metrics["auroc"] is not None:
                        fold_aurocs.append(float(fold_metrics["auroc"]))
                fold_mean = (
                    sum(fold_aurocs) / len(fold_aurocs) if fold_aurocs else None
                )
                for metric_name, actual in (
                    ("fold_auroc_mean", fold_mean),
                    ("fold_auroc_eligible_count", len(fold_aurocs)),
                    ("fold_count", len({record.get("fold") for record in selected})),
                ):
                    checks.append(
                        AuditCheck(
                            f"{task}.{report_key}.{model}.{metric_name}",
                            _close(actual, model_report.get(metric_name)),
                            f"actual={actual}; expected={model_report.get(metric_name)}",
                        )
                    )

        external = task_report.get("external_validation", {})
        if isinstance(external, Mapping):
            for label, group_field in (
                ("satellite", "norad_id"),
                ("station", "station_id"),
            ):
                split_report = external.get(label, {})
                if (
                    not isinstance(split_report, Mapping)
                    or split_report.get("status") != "evaluated"
                ):
                    continue
                external_path = Path(str(split_report.get("predictions_path", "")))
                external_records = _jsonl(external_path)
                checks.append(
                    AuditCheck(
                        f"{task}.external_{label}.predictions_sha",
                        _sha256(external_path)
                        == split_report.get("predictions_sha256"),
                        _sha256(external_path),
                    )
                )
                expected_split = f"external-{label}"
                held_out_raw = split_report.get("held_out_group_ids", [])
                held_out = (
                    {int(value) for value in held_out_raw}
                    if isinstance(held_out_raw, list)
                    else set()
                )
                if label == "satellite":
                    configured_groups = publication_config.get(
                        "external_validation_norad_ids", []
                    )
                    expected_held_out = (
                        {int(value) for value in configured_groups}
                        if isinstance(configured_groups, list)
                        else set()
                    )
                else:
                    try:
                        fraction = float(
                            publication_config.get(
                                "external_station_holdout_fraction", 0
                            )
                        )
                        seed = int(publication_config.get("random_seed", -1))
                    except (TypeError, ValueError):
                        fraction = 0.0
                        seed = -1
                    station_ids = sorted(
                        {int(row.get("station_id", -1)) for row in dataset_rows}
                    )
                    if station_ids and 0 < fraction < 0.5:
                        holdout_count = max(
                            1,
                            min(
                                len(station_ids) - 1,
                                round(len(station_ids) * fraction),
                            ),
                        )
                        ranked = sorted(
                            station_ids,
                            key=lambda value: hashlib.sha256(
                                f"{seed}:{value}".encode()
                            ).hexdigest(),
                        )
                        expected_held_out = set(ranked[:holdout_count])
                    else:
                        expected_held_out = set()
                expected_minimum_groups = min(5, len(held_out))
                actual_ids = {
                    record.get("observation_id") for record in external_records
                }
                expected_ids = {
                    row.get("observation_id")
                    for row in eligible_rows
                    if str(row.get("start"))
                    >= str(
                        predeclared_temporal_cutoff
                        or temporal_report.get("test_start", "")
                    )
                    and int(row.get(group_field, -1)) in held_out
                }
                actual_groups = {
                    int(record.get(group_field, -1))
                    for record in external_records
                }
                models_by_external_row: defaultdict[object, set[object]] = defaultdict(set)
                identities: set[tuple[object, object]] = set()
                identity_unique = True
                records_aligned = True
                probabilities_valid = True
                for record in external_records:
                    identity = (record.get("observation_id"), record.get("model"))
                    if identity in identities:
                        identity_unique = False
                    identities.add(identity)
                    models_by_external_row[record.get("observation_id")].add(
                        record.get("model")
                    )
                    source = dataset_by_id.get(record.get("observation_id"))
                    if source is None:
                        records_aligned = False
                        continue
                    records_aligned = records_aligned and (
                        record.get("task") == task
                        and record.get("split") == expected_split
                        and record.get("fold") == "predeclared-group-cohort"
                        and record.get("outcome") == source.get(task)
                        and record.get("norad_id") == source.get("norad_id")
                        and record.get("station_id") == source.get("station_id")
                        and record.get("start") == source.get("start")
                    )
                    try:
                        probability = float(record["probability"])
                    except (KeyError, TypeError, ValueError):
                        probabilities_valid = False
                    else:
                        probabilities_valid = (
                            probabilities_valid
                            and math.isfinite(probability)
                            and 0 <= probability <= 1
                        )
                reported_groups_raw = split_report.get("evaluated_group_ids", [])
                reported_groups = (
                    {int(value) for value in reported_groups_raw}
                    if isinstance(reported_groups_raw, list)
                    else set()
                )
                checks.extend(
                    (
                        AuditCheck(
                            f"{task}.external_{label}.held_out_selection",
                            bool(expected_held_out)
                            and held_out == expected_held_out,
                            (
                                f"reported={sorted(held_out)}; "
                                f"expected={sorted(expected_held_out)}"
                            ),
                        ),
                        AuditCheck(
                            f"{task}.external_{label}.identity_unique",
                            identity_unique,
                            f"predictions={len(external_records)}",
                        ),
                        AuditCheck(
                            f"{task}.external_{label}.records_match_dataset",
                            bool(external_records) and records_aligned,
                            f"predictions={len(external_records)}",
                        ),
                        AuditCheck(
                            f"{task}.external_{label}.probabilities_valid",
                            bool(external_records) and probabilities_valid,
                            f"predictions={len(external_records)}",
                        ),
                        AuditCheck(
                            f"{task}.external_{label}.model_grid_complete",
                            bool(models_by_external_row)
                            and all(
                                models == expected_models
                                for models in models_by_external_row.values()
                            ),
                            f"test_rows={len(models_by_external_row)}",
                        ),
                        AuditCheck(
                            f"{task}.external_{label}.future_group_membership",
                            bool(actual_ids)
                            and actual_ids == expected_ids
                            and actual_groups <= held_out
                            and split_report.get("future_only") is True
                            and split_report.get("group_disjoint") is True
                            and split_report.get("test_start")
                            == min(
                                str(record.get("start"))
                                for record in external_records
                            )
                            and (
                                predeclared_temporal_cutoff is None
                                or str(split_report.get("test_start"))
                                >= predeclared_temporal_cutoff
                            ),
                            (
                                f"actual_ids={len(actual_ids)}; "
                                f"expected_ids={len(expected_ids)}"
                            ),
                        ),
                        AuditCheck(
                            f"{task}.external_{label}.reported_counts",
                            split_report.get("prediction_rows")
                            == len(external_records)
                            and split_report.get("test_observation_rows")
                            == len(actual_ids),
                            (
                                f"predictions={len(external_records)}; "
                                f"test_rows={len(actual_ids)}"
                            ),
                        ),
                        AuditCheck(
                            f"{task}.external_{label}.group_coverage",
                            expected_minimum_groups > 0
                            and actual_groups == reported_groups
                            and actual_groups <= held_out
                            and len(actual_groups) >= expected_minimum_groups
                            and split_report.get("minimum_evaluated_groups")
                            == expected_minimum_groups
                            and split_report.get("evaluated_group_count")
                            == len(actual_groups)
                            and _close(
                                split_report.get(
                                    "held_out_group_coverage_fraction"
                                ),
                                len(actual_groups) / len(held_out)
                                if held_out
                                else 0.0,
                            ),
                            (
                                f"groups={len(actual_groups)}/{len(held_out)}; "
                                f"minimum={expected_minimum_groups}"
                            ),
                        ),
                    )
                )
                reported_metrics = split_report.get("metrics", {})
                if not isinstance(reported_metrics, Mapping):
                    checks.append(
                        AuditCheck(
                            f"{task}.external_{label}.metrics_shape",
                            False,
                            "malformed",
                        )
                    )
                else:
                    for model, model_report in reported_metrics.items():
                        selected = [
                            record
                            for record in external_records
                            if record.get("model") == model
                        ]
                        pooled = (
                            model_report.get("pooled", {})
                            if isinstance(model_report, Mapping)
                            else {}
                        )
                        if not selected or not isinstance(pooled, Mapping):
                            checks.append(
                                AuditCheck(
                                    f"{task}.external_{label}.{model}.metrics_shape",
                                    False,
                                    f"predictions={len(selected)}",
                                )
                            )
                            continue
                        recomputed = _recompute_probability_metrics(
                            [int(record["outcome"]) for record in selected],
                            [float(record["probability"]) for record in selected],
                        )
                        for metric_name, actual in recomputed.items():
                            checks.append(
                                AuditCheck(
                                    f"{task}.external_{label}.{model}.{metric_name}",
                                    _close(actual, pooled.get(metric_name)),
                                    (
                                        f"actual={actual}; "
                                        f"expected={pooled.get(metric_name)}"
                                    ),
                                )
                            )
                comparisons = split_report.get(
                    "paired_bootstrap_vs_global_rate", {}
                )
                if isinstance(comparisons, Mapping):
                    for model, summary in comparisons.items():
                        if not isinstance(summary, Mapping) or "artifact_path" not in summary:
                            continue
                        _audit_bootstrap(
                            checks,
                            task=str(task),
                            report_key=f"external_{label}",
                            model=f"primary.{model}",
                            summary=summary,
                            records=external_records,
                            seed=int(evaluation.get("random_seed", -1)),
                            expected_replicates=int(
                                evaluation.get("bootstrap_replicates", -1)
                            ),
                        )

        _audit_covariate_ablation(
            checks,
            task=str(task),
            task_report=task_report,
            dataset_by_id=dataset_by_id,
            eligible_rows=eligible_rows,
            predeclared_cutoff=predeclared_temporal_cutoff,
            seed=int(evaluation.get("random_seed", -1)),
            expected_replicates=int(
                evaluation.get("bootstrap_replicates", -1)
            ),
        )

        replay = task_report.get("scheduling_replay")
        if isinstance(replay, Mapping):
            _audit_scheduling_replay(
                checks,
                replay=replay,
                prediction_records=records,
                dataset_by_id=dataset_by_id,
                evaluation=evaluation,
                namespace="scheduling",
                expected_task="signal_present",
                expected_split="temporal",
            )

    composed = evaluation.get("composed_reception")
    if isinstance(composed, Mapping) and composed.get("status") == "evaluated":
        temporal_report = composed.get("temporal", {})
        if not isinstance(temporal_report, Mapping):
            checks.append(
                AuditCheck("reception_success.temporal.shape", False, "malformed")
            )
        else:
            cutoff_text = predeclared_temporal_cutoff or str(
                temporal_report.get("test_start", "")
            )
            endpoint_rows = [
                row
                for row in dataset_rows
                if _recompute_reception_success(row) in {0, 1}
            ]
            expected_test_rows = [
                row
                for row in endpoint_rows
                if str(row.get("start", "")) >= cutoff_text
            ]
            expected_test_ids = {
                row.get("observation_id") for row in expected_test_rows
            }
            signal_training_rows = [
                row
                for row in dataset_rows
                if row.get("signal_present") in {0, 1}
                and str(row.get("end", "")) < cutoff_text
            ]
            decode_training_rows = [
                row
                for row in dataset_rows
                if row.get("decode_success_given_signal") in {0, 1}
                and str(row.get("end", "")) < cutoff_text
            ]
            training_rows = signal_training_rows + decode_training_rows
            temporal_counts_valid = (
                temporal_report.get("signal_train_count")
                == len(signal_training_rows)
                and temporal_report.get("decode_train_count")
                == len(decode_training_rows)
                and temporal_report.get("test_count") == len(expected_test_rows)
            )
            temporal_bounds_valid = bool(training_rows and expected_test_rows) and (
                temporal_report.get("train_start")
                == min(str(row.get("start")) for row in training_rows)
                and temporal_report.get("train_end")
                == max(str(row.get("end")) for row in training_rows)
                and temporal_report.get("test_start")
                == min(str(row.get("start")) for row in expected_test_rows)
                and temporal_report.get("test_end")
                == max(str(row.get("end")) for row in expected_test_rows)
                and max(str(row.get("end")) for row in training_rows)
                < min(str(row.get("start")) for row in expected_test_rows)
            )
            checks.extend(
                (
                    AuditCheck(
                        "reception_success.temporal.counts_recomputed",
                        temporal_counts_valid,
                        (
                            f"signal_train={len(signal_training_rows)}; "
                            f"decode_train={len(decode_training_rows)}; "
                            f"test={len(expected_test_rows)}"
                        ),
                    ),
                    AuditCheck(
                        "reception_success.temporal.bounds_recomputed",
                        temporal_bounds_valid,
                        f"cutoff={cutoff_text}",
                    ),
                )
            )
            temporal_records = _audit_composed_prediction_file(
                checks,
                name="reception_success.temporal",
                path=Path(str(composed.get("predictions_path", ""))),
                expected_sha256=composed.get("predictions_sha256"),
                split_report=temporal_report,
                dataset_by_id=dataset_by_id,
                expected_ids=expected_test_ids,
                expected_split="temporal-composed",
                expected_fold=(
                    "predeclared-final-panel"
                    if predeclared_temporal_cutoff is not None
                    else "final-20-percent"
                ),
            )
            for report_key, audit_report_key in (
                ("paired_bootstrap_vs_global_rate", "composed_temporal"),
                (
                    "paired_bootstrap_station_cluster_sensitivity",
                    "composed_temporal_station_sensitivity",
                ),
            ):
                comparisons = temporal_report.get(report_key, {})
                if isinstance(comparisons, Mapping):
                    for model, summary in comparisons.items():
                        if not isinstance(summary, Mapping):
                            continue
                        _audit_bootstrap(
                            checks,
                            task="reception_success",
                            report_key=audit_report_key,
                            model=f"primary.{model}",
                            summary=summary,
                            records=temporal_records,
                            seed=int(evaluation.get("random_seed", -1)),
                            expected_replicates=int(
                                evaluation.get("bootstrap_replicates", -1)
                            ),
                        )

            composed_replay = composed.get("scheduling_replay")
            if isinstance(composed_replay, Mapping):
                _audit_scheduling_replay(
                    checks,
                    replay=composed_replay,
                    prediction_records=temporal_records,
                    dataset_by_id=dataset_by_id,
                    evaluation=evaluation,
                    namespace="reception_success.scheduling",
                    expected_task="reception_success",
                    expected_split="temporal-composed",
                )

            external = composed.get("external_validation", {})
            if isinstance(external, Mapping):
                for label, group_field in (
                    ("satellite", "norad_id"),
                    ("station", "station_id"),
                ):
                    split_report = external.get(label)
                    if not isinstance(split_report, Mapping) or split_report.get(
                        "status"
                    ) != "evaluated":
                        continue
                    held_out_values = split_report.get("held_out_group_ids", [])
                    held_out = (
                        {int(value) for value in held_out_values}
                        if isinstance(held_out_values, list)
                        else set()
                    )
                    expected_external_rows = [
                        row
                        for row in endpoint_rows
                        if str(row.get("start", "")) >= cutoff_text
                        and int(row.get(group_field, -1)) in held_out
                    ]
                    expected_external_ids = {
                        row.get("observation_id")
                        for row in expected_external_rows
                    }
                    signal_external_training = [
                        row
                        for row in signal_training_rows
                        if int(row.get(group_field, -1)) not in held_out
                    ]
                    decode_external_training = [
                        row
                        for row in decode_training_rows
                        if int(row.get(group_field, -1)) not in held_out
                    ]
                    external_training = (
                        signal_external_training + decode_external_training
                    )
                    represented = {
                        int(row.get(group_field, -1))
                        for row in expected_external_rows
                    }
                    external_summary_valid = (
                        split_report.get("signal_train_count")
                        == len(signal_external_training)
                        and split_report.get("decode_train_count")
                        == len(decode_external_training)
                        and split_report.get("test_observation_rows")
                        == len(expected_external_rows)
                        and split_report.get("prediction_rows")
                        == 4 * len(expected_external_rows)
                        and split_report.get("evaluated_group_ids")
                        == sorted(represented)
                        and split_report.get("evaluated_group_count")
                        == len(represented)
                        and _close(
                            split_report.get(
                                "held_out_group_coverage_fraction"
                            ),
                            len(represented) / len(held_out) if held_out else 0.0,
                        )
                        and split_report.get("group_disjoint") is True
                        and split_report.get("future_only") is True
                    )
                    external_bounds_valid = bool(
                        external_training and expected_external_rows
                    ) and (
                        split_report.get("train_start")
                        == min(
                            str(row.get("start")) for row in external_training
                        )
                        and split_report.get("train_end")
                        == max(str(row.get("end")) for row in external_training)
                        and split_report.get("test_start")
                        == min(
                            str(row.get("start"))
                            for row in expected_external_rows
                        )
                        and split_report.get("test_end")
                        == max(
                            str(row.get("end")) for row in expected_external_rows
                        )
                        and max(str(row.get("end")) for row in external_training)
                        < min(
                            str(row.get("start"))
                            for row in expected_external_rows
                        )
                    )
                    checks.extend(
                        (
                            AuditCheck(
                                f"reception_success.external_{label}.summary",
                                external_summary_valid,
                                (
                                    f"test={len(expected_external_rows)}; "
                                    f"groups={len(represented)}"
                                ),
                            ),
                            AuditCheck(
                                f"reception_success.external_{label}.bounds",
                                external_bounds_valid,
                                f"cutoff={cutoff_text}",
                            ),
                        )
                    )
                    external_records = _audit_composed_prediction_file(
                        checks,
                        name=f"reception_success.external_{label}",
                        path=Path(str(split_report.get("predictions_path", ""))),
                        expected_sha256=split_report.get("predictions_sha256"),
                        split_report=split_report,
                        dataset_by_id=dataset_by_id,
                        expected_ids=expected_external_ids,
                        expected_split=f"external-{group_field}-composed",
                        expected_fold="predeclared-held-out-groups",
                    )
                    comparisons = split_report.get(
                        "paired_bootstrap_vs_global_rate", {}
                    )
                    if isinstance(comparisons, Mapping):
                        for model, summary in comparisons.items():
                            if not isinstance(summary, Mapping):
                                continue
                            _audit_bootstrap(
                                checks,
                                task="reception_success",
                                report_key=f"composed_external_{label}",
                                model=f"primary.{model}",
                                summary=summary,
                                records=external_records,
                                seed=int(evaluation.get("random_seed", -1)),
                                expected_replicates=int(
                                    evaluation.get("bootstrap_replicates", -1)
                                ),
                            )

    tle_report = evaluation.get("tle_refresh_audit")
    if isinstance(tle_report, Mapping):
        path = Path(str(tle_report["records_path"]))
        checks.append(
            AuditCheck(
                "tle_drift_records_sha",
                _sha256(path) == tle_report.get("records_sha256"),
                _sha256(path),
            )
        )
        records = _jsonl(path)
        checks.append(
            AuditCheck(
                "tle_drift_transition_count",
                len(records) == int(tle_report["transition_count"]),
                f"actual={len(records)}; expected={tle_report['transition_count']}",
            )
        )
        paired = [record for record in records if record.get("start_shift_seconds") is not None]
        for name, actual in (
            ("paired_window_count", len(paired)),
            ("window_added_count", sum(record.get("window_added") is True for record in records)),
            ("window_removed_count", sum(record.get("window_removed") is True for record in records)),
            (
                "indeterminate_unmatched_both_count",
                sum(
                    record.get("indeterminate_unmatched_both") is True
                    for record in records
                ),
            ),
            (
                "propagation_error_count",
                sum(record.get("propagation_error") is not None for record in records),
            ),
        ):
            checks.append(
                AuditCheck(
                    f"tle_drift_{name}",
                    actual == int(tle_report.get(name, -1)),
                    f"actual={actual}; expected={tle_report.get(name)}",
                )
            )
        state_partition_valid = all(
            sum(
                (
                    record.get("start_shift_seconds") is not None,
                    record.get("window_added") is True,
                    record.get("window_removed") is True,
                    record.get("indeterminate_unmatched_both") is True,
                    record.get("propagation_error") is not None,
                )
            )
            == 1
            for record in records
        )
        checks.append(
            AuditCheck(
                "tle_drift_state_partition",
                state_partition_valid,
                f"records={len(records)}",
            )
        )
        absolute_starts = sorted(
            abs(float(record["start_shift_seconds"])) for record in paired
        )

        def nearest_percentile(fraction: float) -> float | None:
            if not absolute_starts:
                return None
            index = min(
                len(absolute_starts) - 1,
                round((len(absolute_starts) - 1) * fraction),
            )
            return absolute_starts[index]

        absolute_report = tle_report.get("absolute_start_shift_seconds", {})
        if isinstance(absolute_report, Mapping):
            for name, actual in (
                ("median", nearest_percentile(0.5)),
                ("p90", nearest_percentile(0.9)),
                ("p95", nearest_percentile(0.95)),
                ("max", absolute_starts[-1] if absolute_starts else None),
            ):
                checks.append(
                    AuditCheck(
                        f"tle_drift_absolute_start_{name}",
                        _close(actual, absolute_report.get(name)),
                        f"actual={actual}; expected={absolute_report.get(name)}",
                    )
                )
        thresholds = tle_report.get("thresholds", {})
        if isinstance(thresholds, Mapping):
            for threshold_text, threshold_report in thresholds.items():
                if not isinstance(threshold_report, Mapping):
                    checks.append(
                        AuditCheck(
                            f"tle_drift_threshold_{threshold_text}.shape",
                            False,
                            "malformed",
                        )
                    )
                    continue
                threshold = float(threshold_text)
                material = [
                    record
                    for record in records
                    if record.get("window_added") is True
                    or record.get("window_removed") is True
                    or (
                        record.get("start_shift_seconds") is not None
                        and abs(float(record["start_shift_seconds"])) >= threshold
                    )
                    or (
                        record.get("duration_shift_seconds") is not None
                        and abs(float(record["duration_shift_seconds"])) >= threshold
                    )
                ]
                checks.append(
                    AuditCheck(
                        f"tle_drift_threshold_{threshold_text}.count",
                        len(material)
                        == int(threshold_report.get("material_transition_count", -1)),
                        (
                            f"actual={len(material)}; "
                            f"expected={threshold_report.get('material_transition_count')}"
                        ),
                    )
                )
                expected_fraction = len(material) / len(records) if records else None
                checks.append(
                    AuditCheck(
                        f"tle_drift_threshold_{threshold_text}.fraction",
                        _close(
                            expected_fraction,
                            threshold_report.get("material_fraction"),
                        ),
                        (
                            f"actual={expected_fraction}; "
                            f"expected={threshold_report.get('material_fraction')}"
                        ),
                    )
                )
                freezes = threshold_report.get("transitions_inside_freeze_proxy", {})
                if isinstance(freezes, Mapping):
                    for minutes_text, reported_count in freezes.items():
                        seconds = float(minutes_text) * 60
                        actual = sum(
                            record.get("reference_lead_from_tle_epoch_seconds")
                            is not None
                            and 0
                            <= float(record["reference_lead_from_tle_epoch_seconds"])
                            < seconds
                            for record in material
                        )
                        checks.append(
                            AuditCheck(
                                f"tle_drift_threshold_{threshold_text}.freeze_{minutes_text}",
                                actual == int(reported_count),
                                f"actual={actual}; expected={reported_count}",
                            )
                        )

    figures = evaluation.get("figures", [])
    if isinstance(figures, list):
        for index, figure in enumerate(figures):
            if not isinstance(figure, Mapping):
                checks.append(AuditCheck(f"figure.{index}.shape", False, "malformed"))
                continue
            path = Path(str(figure.get("path", "")))
            digest = _sha256(path)
            checks.append(
                AuditCheck(
                    f"figure.{index}.sha",
                    digest == figure.get("sha256"),
                    digest,
                )
            )

    risk = evaluation.get("correlated_risk_sensitivity")
    if isinstance(risk, Mapping):
        plan_path = Path(str(risk.get("plan_path", "")))
        plan_digest = _sha256(plan_path)
        checks.append(
            AuditCheck(
                "risk.plan_sha",
                plan_digest == risk.get("plan_sha256"),
                plan_digest,
            )
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        assignments = plan.get("assignments", [])
        plan_fingerprint = _canonical_plan_fingerprint(plan)
        checks.append(
            AuditCheck(
                "risk.plan_canonical_fingerprint",
                plan_fingerprint == plan.get("canonical_fingerprint")
                and plan_fingerprint == risk.get("plan_canonical_fingerprint"),
                plan_fingerprint,
            )
        )
        expected = sum(float(item["expected_unique_samples"]) for item in assignments)
        diagnostics = plan.get("diagnostics", {})
        checks.append(
            AuditCheck(
                "risk.plan_solver_status",
                isinstance(diagnostics, Mapping)
                and diagnostics.get("status") == "optimal"
                and risk.get("solver_status") == "optimal",
                (
                    f"plan={diagnostics.get('status') if isinstance(diagnostics, Mapping) else None}; "
                    f"report={risk.get('solver_status')}"
                ),
            )
        )
        checks.append(
            AuditCheck(
                "risk.plan_selected_count",
                len(assignments) == int(risk.get("selected_opportunities", -1)),
                f"actual={len(assignments)}; expected={risk.get('selected_opportunities')}",
            )
        )
        checks.append(
            AuditCheck(
                "risk.plan_expected_samples",
                _close(expected, risk.get("nominal_expected_samples")),
                f"actual={expected}; expected={risk.get('nominal_expected_samples')}",
            )
        )
        simulations = {"independent": risk.get("independent")}
        scenarios = risk.get("scenarios", {})
        frozen_scenarios = {
            "mild_correlated": {
                "station_day_availability": 0.98,
                "satellite_day_transmit_availability": 0.95,
                "severe_environment_probability": 0.10,
                "severe_environment_success_multiplier": 0.70,
            },
            "stress_correlated": {
                "station_day_availability": 0.90,
                "satellite_day_transmit_availability": 0.85,
                "severe_environment_probability": 0.25,
                "severe_environment_success_multiplier": 0.50,
            },
        }
        if isinstance(scenarios, Mapping):
            simulations.update(scenarios)
        checks.append(
            AuditCheck(
                "risk.frozen_scenario_names",
                isinstance(scenarios, Mapping)
                and set(scenarios) == set(frozen_scenarios),
                (
                    f"actual={sorted(scenarios) if isinstance(scenarios, Mapping) else None}; "
                    f"expected={sorted(frozen_scenarios)}"
                ),
            )
        )
        if isinstance(scenarios, Mapping):
            for scenario_name, expected_scenario in frozen_scenarios.items():
                raw_simulation = scenarios.get(scenario_name)
                raw_scenario = (
                    raw_simulation.get("scenario")
                    if isinstance(raw_simulation, Mapping)
                    else None
                )
                matches = isinstance(raw_scenario, Mapping) and all(
                    _close(raw_scenario.get(key), value)
                    for key, value in expected_scenario.items()
                )
                checks.append(
                    AuditCheck(
                        f"risk.{scenario_name}.frozen_scenario",
                        matches,
                        f"actual={raw_scenario}; expected={expected_scenario}",
                    )
                )
        expected_trials = int(evaluation.get("bootstrap_replicates", -1))
        expected_seed = int(evaluation.get("random_seed", -1))
        for name, simulation in simulations.items():
            if not isinstance(simulation, Mapping):
                checks.append(AuditCheck(f"risk.{name}.shape", False, "missing"))
                continue
            ordered = (
                float(simulation.get("percentile_05", -1)),
                float(simulation.get("percentile_50", -1)),
                float(simulation.get("percentile_95", -1)),
            )
            checks.append(
                AuditCheck(
                    f"risk.{name}.quantile_order",
                    0 <= ordered[0] <= ordered[1] <= ordered[2],
                    str(ordered),
                )
            )
            checks.append(
                AuditCheck(
                    f"risk.{name}.trial_count",
                    int(simulation.get("trials", -1)) == expected_trials,
                    str(simulation.get("trials")),
                )
            )
            _audit_simulation(
                checks,
                name=str(name),
                simulation=simulation,
                assignments=assignments,
                nominal_expected=expected,
                expected_trials=expected_trials,
                expected_seed=expected_seed,
            )
    check_name_counts: defaultdict[str, int] = defaultdict(int)
    for check in checks:
        check_name_counts[check.name] += 1
    duplicate_check_names = sorted(
        name for name, count in check_name_counts.items() if count > 1
    )
    checks.append(
        AuditCheck(
            "audit.check_names_unique",
            not duplicate_check_names,
            f"duplicates={duplicate_check_names}",
        )
    )
    return {
        "schema_version": "observation-planning-independent-audit-v1",
        "input_artifacts": {
            "dataset": {"path": str(dataset_path), "sha256": dataset_sha},
            "dataset_manifest": {
                "path": str(dataset_manifest_path),
                "sha256": dataset_manifest_sha,
            },
            "evaluation": {
                "path": str(evaluation_path),
                "sha256": evaluation_sha,
            },
        },
        "passed": all(check.passed for check in checks),
        "check_count": len(checks),
        "failed_check_count": sum(not check.passed for check in checks),
        "checks": [check.as_dict() for check in checks],
        "implementation_boundary": (
            "hashes, dataset-prediction identities, model-grid completeness, counts, "
            "Brier, log loss, bootstrap draws, replay identities/conflicts/totals, "
            "figures, risk-plan totals and seeded Monte Carlo draws checked without "
            "importing evaluation/model/simulation code"
        ),
    }
