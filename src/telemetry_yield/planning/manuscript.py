"""Deterministic, claim-guarded tables and narrative for the planning study."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Mapping


def _load(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_bound_artifact(
    reference: str, *, anchor: Path, expected_sha256: object
) -> Path:
    raw = Path(reference)
    candidates = (
        [raw]
        if raw.is_absolute()
        else [Path.cwd() / raw, *(parent / raw for parent in anchor.parents)]
    )
    visited: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in visited:
            continue
        visited.add(resolved)
        if resolved.is_file() and _sha256(resolved) == expected_sha256:
            return resolved
    raise ValueError("dataset manifest artifact binding failed")


def _number(value: object, digits: int = 4) -> str:
    if value is None:
        return "NA"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.{digits}f}"


def _write_csv(path: Path, header: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_publication_results(
    *,
    dataset_manifest_path: Path,
    evaluation_path: Path,
    independent_audit_path: Path,
    output_dir: Path,
    probability_model_path: Path | None = None,
    covariate_evidence_path: Path | None = None,
) -> Mapping[str, object]:
    """Create paper-ready result tables without changing analytical outputs."""

    dataset = _load(dataset_manifest_path)
    evaluation = _load(evaluation_path)
    audit = _load(independent_audit_path)
    if audit.get("passed") is not True:
        raise ValueError("independent artifact audit must pass before reporting results")
    output_dir.mkdir(parents=True, exist_ok=True)

    covariate_evidence_summary: Mapping[str, int] | None = None
    if covariate_evidence_path is not None:
        from .covariates import validate_covariate_evidence_manifest

        raw_covariate_archive_path = dataset.get("covariate_archive_path")
        if not isinstance(raw_covariate_archive_path, str):
            raise ValueError("dataset manifest does not identify a covariate archive")
        covariate_archive_path = _resolve_bound_artifact(
            raw_covariate_archive_path,
            anchor=dataset_manifest_path,
            expected_sha256=dataset.get("covariate_archive_sha256"),
        )
        covariate_evidence_summary = validate_covariate_evidence_manifest(
            covariate_evidence_path, covariate_archive_path
        )

    cohort_path = output_dir / "table-cohort.csv"
    raw_target_counts = dataset.get("per_target_counts", [])
    target_counts = (
        [item for item in raw_target_counts if isinstance(item, Mapping)]
        if isinstance(raw_target_counts, list)
        else []
    )
    _write_csv(
        cohort_path,
        (
            "norad_id",
            "normalized_rows",
            "known_signal_rows",
            "known_signal_positive_rows",
            "conditional_decode_rows",
            "conditional_decode_positive_rows",
            "observed_transmitter_count",
            "rows_matching_selection_transmitter",
        ),
        [
            tuple(
                item.get(name)
                for name in (
                    "norad_id",
                    "normalized_rows",
                    "known_signal_rows",
                    "known_signal_positive_rows",
                    "conditional_decode_rows",
                    "conditional_decode_positive_rows",
                    "observed_transmitter_count",
                    "rows_matching_selection_transmitter",
                )
            )
            for item in target_counts
        ],
    )

    deployment_model: Mapping[str, object] | None = None
    deployment_path: Path | None = None
    deployment_summary = (
        "No frozen deployment model was supplied with this result package."
    )
    if probability_model_path is not None:
        from .learned_probability import FrozenLogitProbabilityEstimator

        deployment_model = _load(probability_model_path)
        FrozenLogitProbabilityEstimator.load(probability_model_path)
        if (
            deployment_model.get("training_dataset_sha256")
            != dataset.get("dataset_sha256")
            or deployment_model.get("dataset_manifest_sha256")
            != _sha256(dataset_manifest_path)
            or deployment_model.get("evaluation_sha256") != _sha256(evaluation_path)
        ):
            raise ValueError("probability model is not bound to these publication inputs")
        raw_deployment_tasks = deployment_model.get("tasks")
        if not isinstance(raw_deployment_tasks, Mapping):
            raise ValueError("probability model tasks are missing")
        deployment_rows: list[tuple[object, ...]] = []
        selected_parts: list[str] = []
        for task_name in ("signal_present", "decode_success_given_signal"):
            task_model = raw_deployment_tasks.get(task_name)
            if not isinstance(task_model, Mapping):
                raise ValueError(f"probability model task {task_name} is missing")
            deployment_rows.append(
                (
                    task_name,
                    task_model.get("selected_model"),
                    task_model.get("feature_family"),
                    task_model.get("optional_covariate_gate_passed"),
                    _number(task_model.get("regularization"), 8),
                    task_model.get("training_count"),
                    task_model.get("training_positives"),
                    task_model.get("validation_count"),
                    _number(task_model.get("validation_brier"), 8),
                    _number(task_model.get("validation_absolute_error_q90"), 8),
                )
            )
            selected_parts.append(
                f"`{task_name}`={task_model.get('selected_model')}"
            )
        deployment_path = output_dir / "table-deployment-model.csv"
        _write_csv(
            deployment_path,
            (
                "task",
                "selected_model",
                "feature_family",
                "optional_covariate_gate_passed",
                "regularization",
                "training_n",
                "training_positives",
                "validation_n",
                "validation_brier",
                "validation_absolute_error_q90",
            ),
            deployment_rows,
        )
        deployment_summary = (
            "The content-bound successor-campaign artifact froze "
            + ", ".join(selected_parts)
            + ". Optional weather/hardware terms enter only where both predeclared "
            "cluster gates passed; this refit is not evaluated on the reused historical holdout."
        )

    probability_rows: list[tuple[object, ...]] = []
    tasks = evaluation.get("tasks", {})
    if not isinstance(tasks, Mapping):
        raise ValueError("evaluation tasks must be an object")
    for task_name, task in tasks.items():
        if not isinstance(task, Mapping) or task.get("status") != "evaluated":
            probability_rows.append(
                (
                    task_name,
                    "temporal",
                    "NA",
                    task.get("status"),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                )
            )
            continue
        temporal = task.get("temporal", {})
        metrics = temporal.get("metrics", {}) if isinstance(temporal, Mapping) else {}
        bootstrap = (
            temporal.get("paired_bootstrap_vs_global_rate", {})
            if isinstance(temporal, Mapping)
            else {}
        )
        station_sensitivity = (
            temporal.get("paired_bootstrap_station_cluster_sensitivity", {})
            if isinstance(temporal, Mapping)
            else {}
        )
        if isinstance(metrics, Mapping):
            for model, metric in metrics.items():
                if not isinstance(metric, Mapping):
                    continue
                comparison = bootstrap.get(model, {}) if isinstance(bootstrap, Mapping) else {}
                station_comparison = (
                    station_sensitivity.get(model, {})
                    if isinstance(station_sensitivity, Mapping)
                    else {}
                )
                probability_rows.append(
                    (
                        task_name,
                        "temporal",
                        model,
                        metric.get("count"),
                        metric.get("positives"),
                        _number(metric.get("brier"), 6),
                        _number(metric.get("log_loss"), 6),
                        _number(metric.get("auroc"), 4),
                        _number(comparison.get("relative_brier_reduction"), 4) if isinstance(comparison, Mapping) else "NA",
                        _number(comparison.get("lower_95"), 6) if isinstance(comparison, Mapping) else "NA",
                        _number(comparison.get("upper_95"), 6) if isinstance(comparison, Mapping) else "NA",
                        comparison.get("cluster_count") if isinstance(comparison, Mapping) else "NA",
                        station_comparison.get("cluster_count") if isinstance(station_comparison, Mapping) else "NA",
                        _number(station_comparison.get("relative_brier_reduction"), 4) if isinstance(station_comparison, Mapping) else "NA",
                        _number(station_comparison.get("lower_95"), 6) if isinstance(station_comparison, Mapping) else "NA",
                        _number(station_comparison.get("upper_95"), 6) if isinstance(station_comparison, Mapping) else "NA",
                    )
                )
        for report_key, split in (("loso_satellite", "loso-satellite"), ("loso_station", "loso-station")):
            transfer = task.get(report_key, {})
            transfer_metrics = transfer.get("metrics", {}) if isinstance(transfer, Mapping) else {}
            transfer_bootstrap = (
                transfer.get("paired_bootstrap_vs_global_rate", {})
                if isinstance(transfer, Mapping)
                else {}
            )
            if not isinstance(transfer_metrics, Mapping):
                continue
            for model, model_metrics in transfer_metrics.items():
                pooled = model_metrics.get("pooled", {}) if isinstance(model_metrics, Mapping) else {}
                if not isinstance(pooled, Mapping):
                    continue
                comparison = (
                    transfer_bootstrap.get(model, {})
                    if isinstance(transfer_bootstrap, Mapping)
                    else {}
                )
                probability_rows.append(
                    (
                        task_name,
                        split,
                        model,
                        pooled.get("count"),
                        pooled.get("positives"),
                        _number(pooled.get("brier"), 6),
                        _number(pooled.get("log_loss"), 6),
                        _number(pooled.get("auroc"), 4),
                        _number(comparison.get("relative_brier_reduction"), 4) if isinstance(comparison, Mapping) else "NA",
                        _number(comparison.get("lower_95"), 6) if isinstance(comparison, Mapping) else "NA",
                        _number(comparison.get("upper_95"), 6) if isinstance(comparison, Mapping) else "NA",
                        comparison.get("cluster_count") if isinstance(comparison, Mapping) else "NA",
                        "",
                        "",
                        "",
                        "",
                    )
                )
        external = task.get("external_validation", {})
        if isinstance(external, Mapping):
            for label in ("satellite", "station"):
                split_report = external.get(label, {})
                if (
                    not isinstance(split_report, Mapping)
                    or split_report.get("status") != "evaluated"
                ):
                    continue
                external_metrics = split_report.get("metrics", {})
                external_bootstrap = split_report.get(
                    "paired_bootstrap_vs_global_rate", {}
                )
                if not isinstance(external_metrics, Mapping):
                    continue
                for model, model_metrics in external_metrics.items():
                    pooled = (
                        model_metrics.get("pooled", {})
                        if isinstance(model_metrics, Mapping)
                        else {}
                    )
                    if not isinstance(pooled, Mapping):
                        continue
                    comparison = (
                        external_bootstrap.get(model, {})
                        if isinstance(external_bootstrap, Mapping)
                        else {}
                    )
                    probability_rows.append(
                        (
                            task_name,
                            f"external-{label}",
                            model,
                            pooled.get("count"),
                            pooled.get("positives"),
                            _number(pooled.get("brier"), 6),
                            _number(pooled.get("log_loss"), 6),
                            _number(pooled.get("auroc"), 4),
                            _number(comparison.get("relative_brier_reduction"), 4)
                            if isinstance(comparison, Mapping)
                            else "NA",
                            _number(comparison.get("lower_95"), 6)
                            if isinstance(comparison, Mapping)
                            else "NA",
                            _number(comparison.get("upper_95"), 6)
                            if isinstance(comparison, Mapping)
                            else "NA",
                            comparison.get("cluster_count")
                            if isinstance(comparison, Mapping)
                            else "NA",
                            "",
                            "",
                            "",
                            "",
                        )
                    )
    probability_path = output_dir / "table-probability.csv"
    _write_csv(
        probability_path,
        (
            "task",
            "split",
            "model",
            "n",
            "positives",
            "brier",
            "log_loss",
            "auroc",
            "relative_brier_reduction",
            "brier_diff_ci_low",
            "brier_diff_ci_high",
            "primary_cluster_count",
            "station_sensitivity_cluster_count",
            "station_sensitivity_relative_brier_reduction",
            "station_sensitivity_ci_low",
            "station_sensitivity_ci_high",
        ),
        probability_rows,
    )

    reception_rows: list[tuple[object, ...]] = []
    composed = evaluation.get("composed_reception", {})
    composed_splits: list[tuple[str, object]] = []
    if isinstance(composed, Mapping) and composed.get("status") == "evaluated":
        composed_splits.append(("temporal", composed.get("temporal", {})))
        composed_external = composed.get("external_validation", {})
        if isinstance(composed_external, Mapping):
            for label in ("satellite", "station"):
                composed_splits.append(
                    (f"external-{label}", composed_external.get(label, {}))
                )
    for split_name, split in composed_splits:
        if not isinstance(split, Mapping):
            continue
        metrics = split.get("metrics", {})
        decisions = split.get("decision_metrics_at_0_5", {})
        if not isinstance(metrics, Mapping) or not isinstance(decisions, Mapping):
            continue
        for model, metric in metrics.items():
            decision = decisions.get(model, {})
            if not isinstance(metric, Mapping) or not isinstance(decision, Mapping):
                continue
            reception_rows.append(
                (
                    split_name,
                    model,
                    metric.get("count"),
                    metric.get("positives"),
                    _number(metric.get("brier"), 6),
                    _number(metric.get("log_loss"), 6),
                    _number(metric.get("auroc"), 4),
                    _number(metric.get("expected_calibration_error"), 4),
                    _number(decision.get("accuracy"), 4),
                    _number(decision.get("sensitivity"), 4),
                    _number(decision.get("specificity"), 4),
                    _number(decision.get("positive_predictive_value"), 4),
                    _number(decision.get("negative_predictive_value"), 4),
                    decision.get("true_positive"),
                    decision.get("true_negative"),
                    decision.get("false_positive"),
                    decision.get("false_negative"),
                )
            )
    reception_path = output_dir / "table-reception.csv"
    _write_csv(
        reception_path,
        (
            "split",
            "model",
            "n",
            "positives",
            "brier",
            "log_loss",
            "auroc",
            "expected_calibration_error",
            "accuracy_at_0_5",
            "sensitivity_at_0_5",
            "specificity_at_0_5",
            "positive_predictive_value_at_0_5",
            "negative_predictive_value_at_0_5",
            "true_positive",
            "true_negative",
            "false_positive",
            "false_negative",
        ),
        reception_rows,
    )

    ablation_rows: list[tuple[object, ...]] = []
    for task_name, task in tasks.items():
        if not isinstance(task, Mapping) or task.get("status") != "evaluated":
            continue
        for report_key, split_name in (
            ("temporal", "temporal"),
            ("loso_satellite", "loso-satellite"),
            ("loso_station", "loso-station"),
        ):
            split = task.get(report_key, {})
            comparisons = (
                split.get("paired_ablation_bootstraps", {})
                if isinstance(split, Mapping)
                else {}
            )
            if not isinstance(comparisons, Mapping):
                continue
            for comparison_name, comparison in comparisons.items():
                if not isinstance(comparison, Mapping):
                    continue
                ablation_rows.append(
                    (
                        task_name,
                        split_name,
                        comparison_name,
                        _number(comparison.get("estimate"), 6),
                        _number(comparison.get("relative_brier_reduction"), 4),
                        _number(comparison.get("lower_95"), 6),
                        _number(comparison.get("upper_95"), 6),
                        comparison.get("cluster_count"),
                        comparison.get("passes_predeclared_useful_effect"),
                    )
                )
        temporal = task.get("temporal", {})
        sensitivity = (
            temporal.get("paired_ablation_station_cluster_sensitivity", {})
            if isinstance(temporal, Mapping)
            else {}
        )
        if isinstance(sensitivity, Mapping):
            for comparison_name, comparison in sensitivity.items():
                if not isinstance(comparison, Mapping):
                    continue
                ablation_rows.append(
                    (
                        task_name,
                        "temporal-station-cluster-sensitivity",
                        comparison_name,
                        _number(comparison.get("estimate"), 6),
                        _number(comparison.get("relative_brier_reduction"), 4),
                        _number(comparison.get("lower_95"), 6),
                        _number(comparison.get("upper_95"), 6),
                        comparison.get("cluster_count"),
                        comparison.get("passes_predeclared_useful_effect"),
                    )
                )
    ablation_path = output_dir / "table-ablation.csv"
    _write_csv(
        ablation_path,
        (
            "task",
            "split",
            "comparison",
            "brier_difference",
            "relative_brier_reduction",
            "ci_low",
            "ci_high",
            "cluster_count",
            "passes_useful_effect",
        ),
        ablation_rows,
    )

    scheduler_rows: list[tuple[object, ...]] = []
    signal = tasks.get("signal_present", {})
    signal_replay = (
        signal.get("scheduling_replay", {}) if isinstance(signal, Mapping) else {}
    )
    composed_replay = (
        composed.get("scheduling_replay", {})
        if isinstance(composed, Mapping)
        else {}
    )
    for endpoint, endpoint_replay in (
        ("signal_present", signal_replay),
        ("reception_success", composed_replay),
    ):
        replay_metrics = (
            endpoint_replay.get("metrics", {})
            if isinstance(endpoint_replay, Mapping)
            else {}
        )
        if isinstance(replay_metrics, Mapping):
            for scheduler, metric in replay_metrics.items():
                if isinstance(metric, Mapping):
                    scheduler_rows.append(
                        (
                            endpoint,
                            scheduler,
                            metric.get("selected_observations"),
                            metric.get("realized_successful_samples"),
                            _number(metric.get("predicted_expected_samples"), 4),
                            metric.get("satellites_covered"),
                        )
                    )
    scheduler_path = output_dir / "table-scheduler.csv"
    _write_csv(
        scheduler_path,
        (
            "endpoint",
            "scheduler",
            "selected",
            "realized_successes",
            "predicted_expected",
            "satellites_covered",
        ),
        scheduler_rows,
    )

    tle = evaluation.get("tle_refresh_audit", {})
    thresholds = tle.get("thresholds", {}) if isinstance(tle, Mapping) else {}
    tle_rows: list[tuple[object, ...]] = []
    if isinstance(thresholds, Mapping):
        for threshold, value in sorted(thresholds.items(), key=lambda item: float(item[0])):
            if isinstance(value, Mapping):
                freezes = value.get("transitions_inside_freeze_proxy", {})
                tle_rows.append(
                    (
                        threshold,
                        value.get("material_transition_count"),
                        _number(value.get("material_fraction"), 4),
                        freezes.get("0") if isinstance(freezes, Mapping) else None,
                        freezes.get("15") if isinstance(freezes, Mapping) else None,
                        freezes.get("30") if isinstance(freezes, Mapping) else None,
                        freezes.get("60") if isinstance(freezes, Mapping) else None,
                    )
                )
    tle_path = output_dir / "table-tle-drift.csv"
    _write_csv(
        tle_path,
        ("threshold_seconds", "material_transitions", "material_fraction", "inside_freeze_0m", "inside_freeze_15m", "inside_freeze_30m", "inside_freeze_60m"),
        tle_rows,
    )

    signal_useful = bool(
        isinstance(signal, Mapping)
        and signal.get("status") == "evaluated"
        and isinstance(signal.get("temporal"), Mapping)
        and signal["temporal"].get("primary_full_logit_useful_effect") is True
    )
    signal_station_sensitivity_useful = bool(
        isinstance(signal, Mapping)
        and signal.get("status") == "evaluated"
        and isinstance(signal.get("temporal"), Mapping)
        and signal["temporal"].get(
            "station_cluster_sensitivity_full_logit_useful_effect"
        )
        is True
    )
    operational_replay = (
        composed_replay if isinstance(composed_replay, Mapping) and composed_replay else signal_replay
    )
    replay_informative = bool(
        isinstance(operational_replay, Mapping)
        and operational_replay.get("informative_for_scheduler_comparison") is True
    )
    synthetic = evaluation.get("synthetic_scheduler_verification", {})
    exact = bool(
        isinstance(synthetic, Mapping)
        and int(synthetic.get("instances", 0)) > 0
        and synthetic.get("milp_exact_match_count") == synthetic.get("instances")
        and int(synthetic.get("multi_asset_instances", 0)) > 0
        and synthetic.get("multi_asset_milp_exact_match_count")
        == synthetic.get("multi_asset_instances")
    )
    def task_summary(task_name: str) -> str:
        task = tasks.get(task_name, {})
        if not isinstance(task, Mapping) or task.get("status") != "evaluated":
            return f"`{task_name}` was not evaluated: {task.get('status', 'missing') if isinstance(task, Mapping) else 'missing'}."
        temporal = task.get("temporal", {})
        metrics = temporal.get("metrics", {}) if isinstance(temporal, Mapping) else {}
        baseline = metrics.get("global_rate", {}) if isinstance(metrics, Mapping) else {}
        full = metrics.get("full_logit", {}) if isinstance(metrics, Mapping) else {}
        comparisons = (
            temporal.get("paired_bootstrap_vs_global_rate", {})
            if isinstance(temporal, Mapping)
            else {}
        )
        comparison = comparisons.get("full_logit", {}) if isinstance(comparisons, Mapping) else {}
        return (
            f"`{task_name}` temporal holdout: global-rate Brier {_number(baseline.get('brier'), 6) if isinstance(baseline, Mapping) else 'NA'}, "
            f"full-model Brier {_number(full.get('brier'), 6) if isinstance(full, Mapping) else 'NA'}, "
            f"relative reduction {_number(comparison.get('relative_brier_reduction'), 4) if isinstance(comparison, Mapping) else 'NA'}, "
            f"paired difference 95% CI [{_number(comparison.get('lower_95'), 6) if isinstance(comparison, Mapping) else 'NA'}, "
            f"{_number(comparison.get('upper_95'), 6) if isinstance(comparison, Mapping) else 'NA'}]."
        )

    def ablation_summary(task_name: str) -> str:
        task = tasks.get(task_name, {})
        temporal = task.get("temporal", {}) if isinstance(task, Mapping) else {}
        comparisons = (
            temporal.get("paired_ablation_bootstraps", {})
            if isinstance(temporal, Mapping)
            else {}
        )
        comparison = (
            comparisons.get("full_logit_vs_geometry_logit", {})
            if isinstance(comparisons, Mapping)
            else {}
        )
        if not isinstance(comparison, Mapping) or not comparison:
            return f"`{task_name}` full-versus-geometry ablation was unavailable."
        return (
            f"`{task_name}` full-versus-geometry ablation: relative Brier reduction "
            f"{_number(comparison.get('relative_brier_reduction'), 4)}, paired 95% CI "
            f"[{_number(comparison.get('lower_95'), 6)}, {_number(comparison.get('upper_95'), 6)}], "
            f"useful-effect gate={comparison.get('passes_predeclared_useful_effect')}."
        )

    def station_sensitivity_summary(task_name: str) -> str:
        task = tasks.get(task_name, {})
        temporal = task.get("temporal", {}) if isinstance(task, Mapping) else {}
        comparisons = (
            temporal.get("paired_bootstrap_station_cluster_sensitivity", {})
            if isinstance(temporal, Mapping)
            else {}
        )
        comparison = (
            comparisons.get("full_logit", {})
            if isinstance(comparisons, Mapping)
            else {}
        )
        if not isinstance(comparison, Mapping) or not comparison:
            return f"`{task_name}` station-cluster sensitivity was unavailable."
        return (
            f"`{task_name}` station-cluster sensitivity: "
            f"{comparison.get('cluster_count')} held-out station clusters, paired 95% CI "
            f"[{_number(comparison.get('lower_95'), 6)}, {_number(comparison.get('upper_95'), 6)}], "
            f"useful-effect gate={comparison.get('passes_predeclared_useful_effect')}."
        )

    def external_summary(task_name: str, label: str) -> str:
        task = tasks.get(task_name, {})
        external = (
            task.get("external_validation", {})
            if isinstance(task, Mapping)
            else {}
        )
        split = external.get(label, {}) if isinstance(external, Mapping) else {}
        if not isinstance(split, Mapping) or split.get("status") != "evaluated":
            return (
                f"`{task_name}` external-{label} validation was unavailable: "
                f"{split.get('reason', split.get('status', 'missing')) if isinstance(split, Mapping) else 'missing'}."
            )
        metrics = split.get("metrics", {})
        baseline_model = metrics.get("global_rate", {}) if isinstance(metrics, Mapping) else {}
        full_model = metrics.get("full_logit", {}) if isinstance(metrics, Mapping) else {}
        baseline = (
            baseline_model.get("pooled", {})
            if isinstance(baseline_model, Mapping)
            else {}
        )
        full = (
            full_model.get("pooled", {})
            if isinstance(full_model, Mapping)
            else {}
        )
        comparisons = split.get("paired_bootstrap_vs_global_rate", {})
        comparison = (
            comparisons.get("full_logit", {})
            if isinstance(comparisons, Mapping)
            else {}
        )
        return (
            f"`{task_name}` external-{label}: {split.get('test_observation_rows')} test observations "
            f"({split.get('prediction_rows')} model predictions) "
            f"from {split.get('evaluated_group_count')}/{len(split.get('held_out_group_ids', [])) if isinstance(split.get('held_out_group_ids'), list) else 0} "
            f"predeclared unseen groups; global-rate Brier "
            f"{_number(baseline.get('brier'), 6) if isinstance(baseline, Mapping) else 'NA'}, "
            f"full-model Brier {_number(full.get('brier'), 6) if isinstance(full, Mapping) else 'NA'}, "
            f"paired difference 95% CI "
            f"[{_number(comparison.get('lower_95'), 6) if isinstance(comparison, Mapping) else 'NA'}, "
            f"{_number(comparison.get('upper_95'), 6) if isinstance(comparison, Mapping) else 'NA'}]."
        )

    def composed_summary(label: str) -> str:
        if not isinstance(composed, Mapping) or composed.get("status") != "evaluated":
            return "End-to-end packet reception was not evaluated."
        split = (
            composed.get("temporal", {})
            if label == "temporal"
            else (
                composed.get("external_validation", {}).get(label, {})
                if isinstance(composed.get("external_validation"), Mapping)
                else {}
            )
        )
        if not isinstance(split, Mapping) or (
            label != "temporal" and split.get("status") != "evaluated"
        ):
            return (
                f"End-to-end reception {label} validation was unavailable: "
                f"{split.get('reason', split.get('status', 'missing')) if isinstance(split, Mapping) else 'missing'}."
            )
        metrics = split.get("metrics", {})
        decisions = split.get("decision_metrics_at_0_5", {})
        full = metrics.get("full_logit", {}) if isinstance(metrics, Mapping) else {}
        decision = (
            decisions.get("full_logit", {})
            if isinstance(decisions, Mapping)
            else {}
        )
        count = (
            split.get("test_count")
            if label == "temporal"
            else split.get("test_observation_rows")
        )
        return (
            f"End-to-end packet reception {label}: n={count}, full-model Brier "
            f"{_number(full.get('brier'), 6) if isinstance(full, Mapping) else 'NA'}, "
            f"AUROC {_number(full.get('auroc'), 4) if isinstance(full, Mapping) else 'NA'}, "
            f"0.5-threshold accuracy "
            f"{_number(decision.get('accuracy'), 4) if isinstance(decision, Mapping) else 'NA'}, "
            f"sensitivity {_number(decision.get('sensitivity'), 4) if isinstance(decision, Mapping) else 'NA'}, "
            f"specificity {_number(decision.get('specificity'), 4) if isinstance(decision, Mapping) else 'NA'}, "
            f"positive predictive value "
            f"{_number(decision.get('positive_predictive_value'), 4) if isinstance(decision, Mapping) else 'NA'}."
        )

    risk = evaluation.get("correlated_risk_sensitivity", {})
    risk_scenarios = risk.get("scenarios", {}) if isinstance(risk, Mapping) else {}
    mild = risk_scenarios.get("mild_correlated", {}) if isinstance(risk_scenarios, Mapping) else {}
    stress = risk_scenarios.get("stress_correlated", {}) if isinstance(risk_scenarios, Mapping) else {}
    exclusions = dataset.get("exclusion_counts", {})
    feature_availability = dataset.get("feature_availability", {})
    if not isinstance(feature_availability, Mapping):
        feature_availability = {}
    historical_weather_rows = int(
        feature_availability.get("historical_terrestrial_weather_snapshot_present", 0) or 0
    )
    historical_kp_rows = int(
        feature_availability.get("historical_space_weather_snapshot_present", 0) or 0
    )
    historical_antenna_rows = int(
        feature_availability.get("historical_antenna_configuration_present", 0) or 0
    )
    historical_location_rows = int(
        feature_availability.get("historical_station_location_captured_present", 0)
        or 0
    )
    current_location_fallback_rows = int(
        feature_availability.get("current_api_station_location_fallback_present", 0)
        or 0
    )
    historical_receiver_rows = int(
        feature_availability.get("historical_receiver_configuration_present", 0)
        or 0
    )
    receiver_value_counts = feature_availability.get(
        "historical_receiver_configuration_value_counts", {}
    )
    receiver_gain_rows = int(
        receiver_value_counts.get("captured_receiver_rf_gain_db", 0) or 0
    ) if isinstance(receiver_value_counts, Mapping) else 0
    if historical_weather_rows or historical_kp_rows:
        historical_environment_boundary = (
            f"Historical terrestrial weather was time-aligned for {historical_weather_rows} "
            f"of {historical_location_rows} rows carrying a capture-time client location; "
            f"{current_location_fallback_rows} rows with only current API station coordinates "
            f"were ineligible for that join. Kp was aligned for {historical_kp_rows} rows from "
            "independently archived environmental sources. These retrospectively retrieved "
            "values support an association/ablation analysis; they are not misrepresented "
            "as forecasts that were available when the original SatNOGS jobs were scheduled."
        )
    else:
        historical_environment_boundary = (
            "No time-aligned historical terrestrial- or space-weather record was available, "
            "so environmental values remained explicitly missing rather than being imputed."
        )
    if historical_antenna_rows:
        historical_antenna_boundary = (
            f"Antenna configuration was joined for {historical_antenna_rows} rows only where "
            "a versioned record was known no later than the observation."
        )
    else:
        historical_antenna_boundary = (
            "The public Network API exposes the current station antenna profile but not a "
            "versioned antenna history, so current hardware was not backdated into historical "
            f"observations. Capture-time client metadata supplied receiver configuration for "
            f"{historical_receiver_rows} rows, including an RF receiver-gain setting for "
            f"{receiver_gain_rows}; this gain and the reported receiver port are not physical "
            "antenna gain/type and remain audit-only for the same observation. Prospective "
            "plans instead freeze the public antenna type and frequency ranges before each "
            "decision."
        )
    sampling_intervals = dataset.get("sampling_intervals", [])
    sampling_summary = (
        f"The common outcome-blind sampling panel contains "
        f"{len(sampling_intervals) if isinstance(sampling_intervals, list) else 0} UTC "
        f"intervals totaling {_number(dataset.get('sampled_days_per_target'), 1)} days per "
        f"satellite. Normalization retained {dataset.get('observed_transmitter_count')} "
        f"distinct transmitters: {dataset.get('normalized_rows_matching_selection_transmitter')} "
        f"rows matched the transmitter used to construct the satellite sampling frame and "
        f"{dataset.get('normalized_rows_from_other_transmitters')} came from other transmitters "
        f"on the same selected satellites."
    )
    report_path = output_dir / "results.md"
    report_path.write_text(
        "\n".join(
            [
                (
                    "# Results: probabilistic satellite reception and scheduling "
                    f"({dataset.get('study_id')})"
                ),
                "",
                "## Cohort and integrity",
                "",
                f"The immutable snapshot yielded {dataset.get('input_rows')} raw observations and {dataset.get('normalized_rows')} normalized observations. "
                f"The primary signal task contains {dataset.get('known_signal_rows')} labeled observations from {dataset.get('signal_satellites')} satellites and {dataset.get('signal_stations')} stations. "
                f"The conditional decode proxy contains {dataset.get('conditional_decode_rows')} observations. The predeclared count gate was **{dataset.get('publication_count_gate')}**.",
                "",
                sampling_summary,
                "",
                f"Counted exclusions were `{json.dumps(exclusions, sort_keys=True)}`. The independent arithmetic and hash audit passed {audit.get('check_count')} checks with {audit.get('failed_check_count')} failures.",
                "",
                "## Reception-probability result",
                "",
                "The sole confirmatory comparison is temporal-holdout `full_logit` versus `global_rate` for `signal_present`. Conditional decode, composed end-to-end reception and its scheduler replay, transfer, station-cluster, alternative-model and ablation intervals are secondary sensitivity analyses without a family-wise multiplicity claim. The composed endpoint was added during documented input-level hardening before V4 model fitting or performance evaluation and is not retroactively promoted to confirmatory status.",
                "",
                (
                    "The full frozen model met the predeclared useful-effect criterion: at least 5% relative Brier improvement over the global rate and a paired 95% cluster-bootstrap interval wholly below zero."
                    if signal_useful
                    else "The full frozen model did **not** meet the predeclared useful-effect criterion. This is a valid negative/below-threshold result and does not justify a post-hoc model or cohort change."
                ),
                "",
                task_summary("signal_present"),
                "",
                station_sensitivity_summary("signal_present"),
                "",
                external_summary("signal_present", "satellite"),
                "",
                external_summary("signal_present", "station"),
                "",
                task_summary("decode_success_given_signal"),
                "",
                station_sensitivity_summary("decode_success_given_signal"),
                "",
                external_summary("decode_success_given_signal", "satellite"),
                "",
                external_summary("decode_success_given_signal", "station"),
                "",
                "The operational end-to-end quantity is evaluated separately as `P(signal) * P(decode artifact | signal)`. Its fixed 0.5-threshold classification figures are descriptive and do not replace probability calibration metrics.",
                "",
                composed_summary("temporal"),
                "",
                composed_summary("satellite"),
                "",
                composed_summary("station"),
                "",
                ablation_summary("signal_present"),
                "",
                ablation_summary("decode_success_given_signal"),
                "",
                "Per-satellite sample composition is in `table-cohort.csv`; complete temporal and transfer metrics are in `table-probability.csv`; end-to-end reception calibration and fixed-threshold confusion matrices are in `table-reception.csv`; direct full-model ablations are in `table-ablation.csv`. Every bootstrap draw remains in the machine-readable evaluation artifacts.",
                "",
                "## Frozen successor-campaign model",
                "",
                deployment_summary,
                "",
                "## Scheduling result",
                "",
                (
                    f"The held-out end-to-end replay contained {operational_replay.get('natural_receiver_conflict_pairs')} natural same-station conflict pairs and is informative for comparing the frozen schedulers."
                    if replay_informative
                    else "The held-out end-to-end SatNOGS replay is non-informative for scheduler gain because the already-admitted Network jobs contain no natural same-station conflicts. It is retained as a degeneracy check, not evidence of equality or improvement."
                ),
                "",
                (
                    "The MILP matched independent exact solvers on every single-receiver interval instance and every crossed station/satellite contention instance. This verifies implementation exactness only; it is not empirical yield evidence."
                    if exact
                    else "The scheduler did not pass the independent synthetic exactness check, so no optimization claim is supported."
                ),
                "",
                f"The month-like engineering instance contained {risk.get('candidate_opportunities') if isinstance(risk, Mapping) else None} candidates and {risk.get('selected_opportunities') if isinstance(risk, Mapping) else None} selected opportunities; the proven-optimal solve took {_number(risk.get('planning_wall_seconds'), 3) if isinstance(risk, Mapping) else 'NA'} s in the captured environment.",
                "",
                f"The declared mild and stress correlated-risk simulations produced mean yields {_number(mild.get('mean_unique_samples'), 2) if isinstance(mild, Mapping) else 'NA'} and {_number(stress.get('mean_unique_samples'), 2) if isinstance(stress, Mapping) else 'NA'} synthetic sample units, respectively. These values are scenario sensitivity, not forecasts.",
                "",
                "## TLE refresh result",
                "",
                f"The audit compared {tle.get('transition_count') if isinstance(tle, Mapping) else None} successive distinct embedded-TLE transitions; {tle.get('indeterminate_unmatched_both_count') if isinstance(tle, Mapping) else None} had no same-pass match for either element set and {tle.get('propagation_error_count') if isinstance(tle, Mapping) else None} encountered an SGP4 propagation error, with both states reported as indeterminate. Shifts are relative to the newer Network planning geometry evaluated at capture-time client coordinates when available and explicitly flagged current-coordinate fallbacks otherwise; TLE epoch is only a proxy for availability time and neither quantity is physical orbit truth.",
                "",
                "## Scope and limitations",
                "",
                "`without-signal` is evidence of no visible satellite signal in a vetted waterfall, not proof that the spacecraft did not transmit. The decode endpoint is the presence of an uploaded demodulation artifact conditional on a confirmed signal, not strict frame validity. Observation-cached schedule geometry is used by the probability model. Capture-time client coordinates support historical weather and diagnostic range/Doppler; current API station-coordinate fallbacks and all coordinate-derived range/Doppler remain excluded from probability models. "
                + historical_environment_boundary
                + " "
                + historical_antenna_boundary
                + " The study does not establish transfer to proprietary stations.",
                "",
                "SatNOGS source data are identified as CC BY-SA 4.0 by the project. External redistribution remains subject to a human attribution/share-alike review. See the frozen protocol, methods, data card, manifests and independent audit shipped with this package.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifact_paths = [
        report_path,
        cohort_path,
        probability_path,
        reception_path,
        ablation_path,
        scheduler_path,
        tle_path,
    ]
    if deployment_path is not None:
        artifact_paths.append(deployment_path)
    manifest: dict[str, object] = {
        "schema_version": "observation-planning-publication-results-manifest-v1",
        "dataset_manifest_path": str(dataset_manifest_path),
        "dataset_manifest_sha256": _sha256(dataset_manifest_path),
        "evaluation_path": str(evaluation_path),
        "evaluation_sha256": _sha256(evaluation_path),
        "independent_audit_path": str(independent_audit_path),
        "independent_audit_sha256": _sha256(independent_audit_path),
        "probability_useful_effect": signal_useful,
        "probability_station_cluster_sensitivity_useful_effect": signal_station_sensitivity_useful,
        "scheduler_empirical_claim_informative": replay_informative,
        "synthetic_scheduler_exactness_passed": exact,
        "artifacts": [
            {
                "relative_path": path.name,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in artifact_paths
        ],
    }
    if probability_model_path is not None and deployment_model is not None:
        manifest["probability_model_path"] = str(probability_model_path)
        manifest["probability_model_sha256"] = _sha256(probability_model_path)
        manifest["probability_model_payload_sha256"] = deployment_model.get(
            "artifact_payload_sha256"
        )
    if covariate_evidence_path is not None and covariate_evidence_summary is not None:
        manifest["covariate_evidence_path"] = str(covariate_evidence_path)
        manifest["covariate_evidence_sha256"] = _sha256(covariate_evidence_path)
        manifest["covariate_evidence_summary"] = dict(covariate_evidence_summary)
    manifest_path = output_dir / "results-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
