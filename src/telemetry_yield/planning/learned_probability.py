"""Content-bound training and inference for a frozen planning probability model.

The publication evaluation decides which optional covariate family is eligible.
This module retrains that already-selected family on all historical rows and
serializes every preprocessing parameter and coefficient.  The resulting model
is intended only for a separately registered future campaign.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Sequence

from .dataset import parse_api_datetime, read_normalized_jsonl
from .evaluation import (
    FULL_CATEGORICAL,
    FULL_NUMERIC,
    HARDWARE_CATEGORICAL,
    HARDWARE_NUMERIC,
    OPERATIONAL_CATEGORICAL,
    OPERATIONAL_NUMERIC,
    WEATHER_NUMERIC,
    FeatureFamily,
    OutcomeTask,
    _fit_logit,
    fold_examples,
    task_rows,
)
from .models import ProbabilityEstimate
from .probability import ReceptionEvidence, ReceptionFeatures


SCHEMA_VERSION = "planning-learned-probability-model-v1"
TASKS: tuple[OutcomeTask, ...] = (
    "signal_present",
    "decode_success_given_signal",
)
MODEL_FAMILIES: Mapping[str, FeatureFamily] = {
    "operational_logit": "operational",
    "operational_weather_logit": "weather",
    "operational_hardware_logit": "hardware",
    "full_covariate_logit": "full",
}

_RUNTIME_TERM_TO_INPUT: Mapping[str, str] = {
    "log_duration_seconds": "duration_seconds",
    "log_tle_age_hours": "tle_age_hours",
    "rise_azimuth_sin": "rise_azimuth_deg",
    "rise_azimuth_cos": "rise_azimuth_deg",
    "set_azimuth_sin": "set_azimuth_deg",
    "set_azimuth_cos": "set_azimuth_deg",
    "log_frequency_hz": "frequency_hz",
    "log_transmitter_baud": "baud",
    "transmitter_mode": "modulation",
    "weather_air_temperature_c": "air_temperature_c",
    "weather_relative_humidity_percent": "relative_humidity_percent",
    "weather_surface_pressure_kpa": "surface_pressure_kpa",
    "weather_wind_speed_m_s": "wind_speed_m_s",
    "weather_precipitation_corrected": "precipitation_corrected",
    "log_system_noise_temperature_k": "system_noise_temperature_k",
}


def _runtime_input_name(model_term: str) -> str:
    return _RUNTIME_TERM_TO_INPUT.get(model_term, model_term)


class LearnedProbabilityModelError(ValueError):
    """A frozen model or one of its provenance inputs is inconsistent."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(payload: object) -> str:
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _read_mapping(path: Path, *, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LearnedProbabilityModelError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise LearnedProbabilityModelError(f"{label} must be a JSON object")
    return payload


def _resolve_bound_path(value: object, *, relative_to: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise LearnedProbabilityModelError("evaluation prediction path is missing")
    path = Path(value)
    return path if path.is_absolute() else relative_to / path


def _linear_quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise LearnedProbabilityModelError("validation residuals are empty")
    ordered = sorted(float(value) for value in values)
    location = (len(ordered) - 1) * probability
    lower = int(math.floor(location))
    upper = int(math.ceil(location))
    if lower == upper:
        return ordered[lower]
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _selected_model(covariate: Mapping[str, object]) -> tuple[str, str, tuple[str, ...]]:
    raw_metrics = covariate.get("metrics")
    raw_gate = covariate.get("deployment_candidate_gate")
    if not isinstance(raw_metrics, Mapping) or not isinstance(raw_gate, Mapping):
        raise LearnedProbabilityModelError("covariate evaluation is incomplete")
    by_model = raw_gate.get("by_model")
    if not isinstance(by_model, Mapping):
        raise LearnedProbabilityModelError("deployment candidate gate is missing")
    passing = tuple(
        sorted(
            model
            for model, passed in by_model.items()
            if passed is True and model in MODEL_FAMILIES and model != "operational_logit"
        )
    )
    for model, passed in by_model.items():
        if model not in MODEL_FAMILIES or not isinstance(passed, bool):
            raise LearnedProbabilityModelError("deployment candidate gate is malformed")
    if passing:
        try:
            candidate_brier = {
                model: float(raw_metrics[model]["brier"])  # type: ignore[index]
                for model in passing
            }
            if any(
                not math.isfinite(value) or not 0 <= value <= 1
                for value in candidate_brier.values()
            ):
                raise ValueError("non-finite Brier score")
            selected = min(
                passing,
                key=lambda model: (candidate_brier[model], model),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LearnedProbabilityModelError(
                "passing candidate lacks a finite validation Brier score"
            ) from exc
        basis = "lowest temporal-held-out Brier among candidates passing both cluster gates"
    else:
        selected = "operational_logit"
        basis = "no optional covariate family passed both predeclared cluster gates"
    if selected not in raw_metrics:
        raise LearnedProbabilityModelError("selected model is absent from evaluation metrics")
    return selected, basis, passing


def _validation_residuals(
    covariate: Mapping[str, object],
    *,
    task: OutcomeTask,
    model: str,
    evaluation_dir: Path,
) -> tuple[tuple[float, ...], str, str]:
    path = _resolve_bound_path(covariate.get("predictions_path"), relative_to=evaluation_dir)
    expected_sha = covariate.get("predictions_sha256")
    actual_sha = _sha256_file(path)
    if expected_sha != actual_sha:
        raise LearnedProbabilityModelError("covariate predictions SHA-256 mismatch")
    residuals: list[float] = []
    squared: list[float] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise LearnedProbabilityModelError("cannot read covariate predictions") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LearnedProbabilityModelError("covariate predictions are invalid JSONL") from exc
        if not isinstance(record, Mapping):
            raise LearnedProbabilityModelError("covariate prediction must be an object")
        if record.get("task") != task or record.get("model") != model:
            continue
        try:
            outcome = int(record["outcome"])
            probability = float(record["probability"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LearnedProbabilityModelError("invalid covariate prediction") from exc
        if outcome not in {0, 1} or not math.isfinite(probability) or not 0 <= probability <= 1:
            raise LearnedProbabilityModelError("invalid covariate prediction value")
        residual = abs(outcome - probability)
        residuals.append(residual)
        squared.append(residual * residual)
    if not residuals:
        raise LearnedProbabilityModelError("selected model has no bound validation predictions")
    metrics = covariate.get("metrics")
    assert isinstance(metrics, Mapping)
    selected_metrics = metrics.get(model)
    if not isinstance(selected_metrics, Mapping):
        raise LearnedProbabilityModelError("selected validation metrics are missing")
    reported_brier = float(selected_metrics.get("brier", math.nan))
    recomputed_brier = sum(squared) / len(squared)
    if not math.isfinite(reported_brier) or not math.isclose(
        reported_brier, recomputed_brier, rel_tol=1e-10, abs_tol=1e-12
    ):
        raise LearnedProbabilityModelError("selected validation Brier score does not reproduce")
    if int(selected_metrics.get("count", -1)) != len(residuals):
        raise LearnedProbabilityModelError("selected validation row count does not reproduce")
    return tuple(residuals), str(path), actual_sha


def _serialize_task(
    rows,
    *,
    task: OutcomeTask,
    task_report: Mapping[str, object],
    evaluation_dir: Path,
) -> Mapping[str, object]:
    if task_report.get("status") != "evaluated":
        raise LearnedProbabilityModelError(f"task {task} was not evaluated")
    covariate = task_report.get("covariate_ablation")
    if not isinstance(covariate, Mapping):
        raise LearnedProbabilityModelError(f"task {task} lacks covariate ablation")
    selected, basis, passing = _selected_model(covariate)
    family = MODEL_FAMILIES[selected]
    regularization_map = covariate.get("selected_regularization")
    if not isinstance(regularization_map, Mapping):
        raise LearnedProbabilityModelError("selected regularization is missing")
    try:
        regularization = float(regularization_map[selected])
    except (KeyError, TypeError, ValueError) as exc:
        raise LearnedProbabilityModelError("selected regularization is invalid") from exc
    if not math.isfinite(regularization) or regularization < 0:
        raise LearnedProbabilityModelError("selected regularization must be non-negative")
    residuals, predictions_path, predictions_sha = _validation_residuals(
        covariate,
        task=task,
        model=selected,
        evaluation_dir=evaluation_dir,
    )
    eligible = task_rows(rows, task)
    if len(eligible) < 20:
        raise LearnedProbabilityModelError(f"task {task} has fewer than 20 labels")
    positives = sum(int(getattr(row, task)) for row in eligible)
    if positives == 0 or positives == len(eligible):
        raise LearnedProbabilityModelError(f"task {task} requires both outcome classes")
    examples, _ = fold_examples(eligible, (), task)
    fitted = _fit_logit(examples, family, regularization)
    encoder = fitted.encoder
    coefficients = [float(value) for value in fitted.coefficients]  # type: ignore[union-attr]
    metrics = covariate["metrics"]
    assert isinstance(metrics, Mapping)
    selected_metrics = metrics[selected]
    assert isinstance(selected_metrics, Mapping)
    gate = covariate["deployment_candidate_gate"]
    assert isinstance(gate, Mapping)
    return {
        "task": task,
        "selected_model": selected,
        "feature_family": family,
        "selection_basis": basis,
        "passing_optional_candidates": list(passing),
        "optional_covariate_gate_passed": selected != "operational_logit",
        "deployment_candidate_gate_rule": gate.get("rule"),
        "regularization": regularization,
        "training_count": len(examples),
        "training_positives": positives,
        "training_start": eligible[0].start.isoformat().replace("+00:00", "Z"),
        "training_end": eligible[-1].end.isoformat().replace("+00:00", "Z"),
        "validation_count": len(residuals),
        "validation_brier": float(selected_metrics["brier"]),
        "validation_absolute_error_q90": _linear_quantile(residuals, 0.9),
        "validation_predictions_path": predictions_path,
        "validation_predictions_sha256": predictions_sha,
        "encoder": {
            "numeric_names": list(encoder.numeric_names),
            "categorical_names": list(encoder.categorical_names),
            "medians": dict(encoder.medians),
            "means": dict(encoder.means),
            "scales": dict(encoder.scales),
            "categories": {
                name: list(values) for name, values in encoder.categories.items()
            },
            "observed_numeric": sorted(encoder.observed_numeric),
        },
        "coefficients": coefficients,
    }


def build_frozen_probability_model(
    dataset_path: Path,
    dataset_manifest_path: Path,
    evaluation_path: Path,
    output_path: Path,
    *,
    now: datetime | None = None,
) -> Mapping[str, object]:
    """Train and write a content-bound model for a future prospective campaign."""

    manifest = _read_mapping(dataset_manifest_path, label="dataset manifest")
    evaluation = _read_mapping(evaluation_path, label="evaluation")
    dataset_sha = _sha256_file(dataset_path)
    if manifest.get("schema_version") != "observation-planning-normalized-dataset-manifest-v1":
        raise LearnedProbabilityModelError("unsupported dataset manifest schema")
    if evaluation.get("schema_version") != "observation-planning-evaluation-v1":
        raise LearnedProbabilityModelError("unsupported evaluation schema")
    if manifest.get("dataset_sha256") != dataset_sha or evaluation.get("dataset_sha256") != dataset_sha:
        raise LearnedProbabilityModelError("dataset is not bound to both manifest and evaluation")
    study_id = manifest.get("study_id")
    if not isinstance(study_id, str) or evaluation.get("study_id") != study_id:
        raise LearnedProbabilityModelError("study identity mismatch")
    raw_tasks = evaluation.get("tasks")
    if not isinstance(raw_tasks, Mapping):
        raise LearnedProbabilityModelError("evaluation tasks are missing")
    rows = read_normalized_jsonl(dataset_path)
    task_models: dict[str, object] = {}
    for task in TASKS:
        task_report = raw_tasks.get(task)
        if not isinstance(task_report, Mapping):
            raise LearnedProbabilityModelError(f"evaluation task {task} is missing")
        task_models[task] = _serialize_task(
            rows,
            task=task,
            task_report=task_report,
            evaluation_dir=evaluation_path.parent,
        )
    training_end = max(row.end for row in rows)
    created = (now or datetime.now(UTC)).astimezone(UTC)
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "study_id": study_id,
        "training_dataset_path": str(dataset_path),
        "training_dataset_sha256": dataset_sha,
        "dataset_manifest_path": str(dataset_manifest_path),
        "dataset_manifest_sha256": _sha256_file(dataset_manifest_path),
        "evaluation_path": str(evaluation_path),
        "evaluation_sha256": _sha256_file(evaluation_path),
        "training_data_end": training_end.isoformat().replace("+00:00", "Z"),
        "tasks": task_models,
        "deployment_scope": (
            "separately frozen prospective campaigns starting after training_data_end"
        ),
        "selection_and_refit_contract": (
            "select only with the bound temporal covariate gate, then refit the selected "
            "family on all bound historical rows; do not reuse that historical holdout as "
            "the final performance claim"
        ),
        "uncertainty_contract": (
            "runtime 90% bounds are an empirical validation absolute-error envelope, "
            "not a confidence or Bayesian credible interval"
        ),
    }
    payload["artifact_payload_sha256"] = _canonical_sha256(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return payload


def _expected_names(family: FeatureFamily) -> tuple[tuple[str, ...], tuple[str, ...]]:
    numeric = {
        "operational": OPERATIONAL_NUMERIC,
        "weather": OPERATIONAL_NUMERIC + WEATHER_NUMERIC,
        "hardware": OPERATIONAL_NUMERIC + HARDWARE_NUMERIC,
        "full": FULL_NUMERIC,
    }[family]
    categorical = {
        "operational": OPERATIONAL_CATEGORICAL,
        "weather": OPERATIONAL_CATEGORICAL,
        "hardware": OPERATIONAL_CATEGORICAL + HARDWARE_CATEGORICAL,
        "full": FULL_CATEGORICAL,
    }[family]
    return numeric, categorical


def _finite_mapping(value: object, names: Sequence[str], *, label: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise LearnedProbabilityModelError(f"{label} keys do not match the encoder")
    result: dict[str, float] = {}
    for name in names:
        try:
            number = float(value[name])
        except (TypeError, ValueError) as exc:
            raise LearnedProbabilityModelError(f"{label} contains a non-number") from exc
        if not math.isfinite(number):
            raise LearnedProbabilityModelError(f"{label} contains a non-finite number")
        result[name] = number
    return result


@dataclass(frozen=True, slots=True)
class _FrozenTask:
    task: OutcomeTask
    model: str
    family: FeatureFamily
    numeric_names: tuple[str, ...]
    categorical_names: tuple[str, ...]
    medians: Mapping[str, float]
    means: Mapping[str, float]
    scales: Mapping[str, float]
    categories: Mapping[str, tuple[str, ...]]
    observed_numeric: frozenset[str]
    coefficients: tuple[float, ...]
    validation_brier: float
    validation_error_q90: float
    training_count: int

    @classmethod
    def from_payload(cls, task: OutcomeTask, payload: object) -> "_FrozenTask":
        if not isinstance(payload, Mapping) or payload.get("task") != task:
            raise LearnedProbabilityModelError(f"invalid frozen task {task}")
        model = payload.get("selected_model")
        family = payload.get("feature_family")
        if not isinstance(model, str) or model not in MODEL_FAMILIES or MODEL_FAMILIES[model] != family:
            raise LearnedProbabilityModelError("frozen model/family mismatch")
        encoder = payload.get("encoder")
        if not isinstance(encoder, Mapping):
            raise LearnedProbabilityModelError("frozen encoder is missing")
        expected_numeric, expected_categorical = _expected_names(family)  # type: ignore[arg-type]
        numeric = tuple(encoder.get("numeric_names", ()))
        categorical = tuple(encoder.get("categorical_names", ()))
        if numeric != expected_numeric or categorical != expected_categorical:
            raise LearnedProbabilityModelError("frozen encoder feature order mismatch")
        medians = _finite_mapping(encoder.get("medians"), numeric, label="medians")
        means = _finite_mapping(encoder.get("means"), numeric, label="means")
        scales = _finite_mapping(encoder.get("scales"), numeric, label="scales")
        if any(value <= 0 for value in scales.values()):
            raise LearnedProbabilityModelError("encoder scales must be positive")
        raw_categories = encoder.get("categories")
        if not isinstance(raw_categories, Mapping) or set(raw_categories) != set(categorical):
            raise LearnedProbabilityModelError("encoder category keys mismatch")
        categories: dict[str, tuple[str, ...]] = {}
        for name in categorical:
            raw = raw_categories[name]
            if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
                raise LearnedProbabilityModelError("invalid encoder categories")
            values = tuple(raw)
            if values != tuple(sorted(set(values))):
                raise LearnedProbabilityModelError("encoder categories must be sorted and unique")
            categories[name] = values
        raw_observed = encoder.get("observed_numeric")
        if (
            not isinstance(raw_observed, list)
            or any(name not in numeric for name in raw_observed)
            or raw_observed != sorted(set(raw_observed))
        ):
            raise LearnedProbabilityModelError("invalid observed numeric features")
        width = 1 + 2 * len(numeric) + sum(len(values) for values in categories.values())
        raw_coefficients = payload.get("coefficients")
        if not isinstance(raw_coefficients, list) or len(raw_coefficients) != width:
            raise LearnedProbabilityModelError("coefficient vector width mismatch")
        coefficients = tuple(float(value) for value in raw_coefficients)
        if any(not math.isfinite(value) for value in coefficients):
            raise LearnedProbabilityModelError("coefficients must be finite")
        brier = float(payload.get("validation_brier", math.nan))
        error_q90 = float(payload.get("validation_absolute_error_q90", math.nan))
        training_count = int(payload.get("training_count", 0))
        if (
            not 0 <= brier <= 1
            or not 0 <= error_q90 <= 1
            or training_count < 20
        ):
            raise LearnedProbabilityModelError("invalid task validation/training summary")
        return cls(
            task=task,
            model=model,
            family=family,  # type: ignore[arg-type]
            numeric_names=numeric,
            categorical_names=categorical,
            medians=medians,
            means=means,
            scales=scales,
            categories=categories,
            observed_numeric=frozenset(raw_observed),
            coefficients=coefficients,
            validation_brier=brier,
            validation_error_q90=error_q90,
            training_count=training_count,
        )

    def predict(
        self, features: ReceptionFeatures, history: Mapping[str, float]
    ) -> tuple[float, tuple[str, ...]]:
        vector = [1.0]
        missing: list[str] = []
        for name in self.numeric_names:
            value = _runtime_numeric(features, history, name)
            is_missing = value is None
            if is_missing and name in self.observed_numeric:
                missing.append(_runtime_input_name(name))
            filled = self.medians[name] if is_missing else value
            assert filled is not None
            vector.extend(
                (
                    (filled - self.means[name]) / self.scales[name],
                    float(is_missing and name in self.observed_numeric),
                )
            )
        for name in self.categorical_names:
            value = _runtime_categorical(features, name)
            values = self.categories[name]
            if value == "<MISSING>":
                missing.append(_runtime_input_name(name))
            elif value not in values and values:
                missing.append(f"{name}:unseen")
            vector.extend(float(value == category) for category in values)
        if len(vector) != len(self.coefficients):
            raise LearnedProbabilityModelError("runtime feature vector width mismatch")
        linear = sum(value * coefficient for value, coefficient in zip(vector, self.coefficients, strict=True))
        probability = 1.0 / (1.0 + math.exp(-linear)) if linear >= 0 else math.exp(linear) / (1.0 + math.exp(linear))
        return min(1 - 1e-8, max(1e-8, probability)), tuple(sorted(set(missing)))


def _runtime_numeric(
    features: ReceptionFeatures, history: Mapping[str, float], name: str
) -> float | None:
    if name == "max_elevation_deg":
        return features.max_elevation_deg
    if name == "log_duration_seconds":
        return math.log1p(max(0.0, features.duration_seconds))
    if name == "log_tle_age_hours":
        return math.log1p(max(0.0, features.tle_age_hours))
    if name == "rise_azimuth_sin":
        return math.sin(math.radians(features.rise_azimuth_deg)) if features.rise_azimuth_deg is not None else None
    if name == "rise_azimuth_cos":
        return math.cos(math.radians(features.rise_azimuth_deg)) if features.rise_azimuth_deg is not None else None
    if name == "set_azimuth_sin":
        return math.sin(math.radians(features.set_azimuth_deg)) if features.set_azimuth_deg is not None else None
    if name == "set_azimuth_cos":
        return math.cos(math.radians(features.set_azimuth_deg)) if features.set_azimuth_deg is not None else None
    if name == "log_frequency_hz":
        return math.log(features.frequency_hz) if features.frequency_hz is not None and features.frequency_hz > 0 else None
    if name == "log_transmitter_baud":
        return math.log(features.baud) if features.baud is not None and features.baud > 0 else None
    if name in history:
        return math.log1p(history[name]) if name.endswith("_count") else history[name]
    feature_names = {
        "weather_air_temperature_c": "air_temperature_c",
        "weather_relative_humidity_percent": "relative_humidity_percent",
        "weather_surface_pressure_kpa": "surface_pressure_kpa",
        "weather_wind_speed_m_s": "wind_speed_m_s",
        "weather_precipitation_corrected": "precipitation_corrected",
        "space_weather_kp": "space_weather_kp",
        "receive_antenna_gain_dbi": "receive_antenna_gain_dbi",
    }
    if name in feature_names:
        value = getattr(features, feature_names[name])
        return float(value) if value is not None else None
    if name == "log_system_noise_temperature_k":
        value = features.system_noise_temperature_k
        return math.log(value) if value is not None and value > 0 else None
    raise LearnedProbabilityModelError(f"unsupported runtime numeric feature {name}")


def _runtime_categorical(features: ReceptionFeatures, name: str) -> str:
    values: Mapping[str, object | None] = {
        "norad_id": features.norad_id,
        "station_id": features.satnogs_station_id,
        "transmitter_uuid": features.transmitter_uuid,
        "transmitter_mode": features.modulation,
        "transmitter_status": features.transmitter_status,
        "antenna_type": features.antenna_type,
        "antenna_frequency_supported": features.antenna_frequency_supported,
    }
    if name not in values:
        raise LearnedProbabilityModelError(f"unsupported runtime categorical feature {name}")
    value = values[name]
    return "<MISSING>" if value is None else str(value)


def _history_snapshot(
    task: OutcomeTask,
    features: ReceptionFeatures,
    evidence: Sequence[ReceptionEvidence],
) -> tuple[dict[str, float], int]:
    successes: dict[str, dict[object, int]] = {
        name: {} for name in ("sat", "station", "transmitter", "pair")
    }
    counts: dict[str, dict[object, int]] = {
        name: {} for name in ("sat", "station", "transmitter", "pair")
    }
    total_success = 0
    total_count = 0
    for item in evidence:
        outcome = item.signal_present if task == "signal_present" else item.decoded
        if not item.listened or outcome is None:
            continue
        keys: Mapping[str, object] = {
            "sat": item.norad_id,
            "station": item.station_id,
            "transmitter": item.transmitter_uuid,
            "pair": (item.norad_id, item.station_id),
        }
        total_success += int(outcome)
        total_count += 1
        for name, key in keys.items():
            counts[name][key] = counts[name].get(key, 0) + 1
            successes[name][key] = successes[name].get(key, 0) + int(outcome)
    station_key = (
        str(features.satnogs_station_id)
        if features.satnogs_station_id is not None
        else features.station_id
    )
    feature_keys: Mapping[str, object] = {
        "sat": features.norad_id,
        "station": station_key,
        "transmitter": features.transmitter_uuid,
        "pair": (features.norad_id, station_key),
    }
    result = {
        "global_success": float(total_success),
        "global_count": float(total_count),
        "global_rate": (total_success + 1.0) / (total_count + 2.0),
    }
    for name, key in feature_keys.items():
        count = counts[name].get(key, 0)
        success = successes[name].get(key, 0)
        result[f"{name}_success"] = float(success)
        result[f"{name}_count"] = float(count)
        result[f"{name}_rate"] = (success + 1.0) / (count + 2.0)
    return result, total_count


class FrozenLogitProbabilityEstimator:
    """Inference-only estimator loaded from a verified deployment artifact."""

    def __init__(self, payload: Mapping[str, object], *, file_sha256: str) -> None:
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise LearnedProbabilityModelError("unsupported frozen model schema")
        expected = payload.get("artifact_payload_sha256")
        unhashed = dict(payload)
        unhashed.pop("artifact_payload_sha256", None)
        if not isinstance(expected, str) or expected != _canonical_sha256(unhashed):
            raise LearnedProbabilityModelError("frozen model payload SHA-256 mismatch")
        tasks = payload.get("tasks")
        if not isinstance(tasks, Mapping) or set(tasks) != set(TASKS):
            raise LearnedProbabilityModelError("frozen model tasks mismatch")
        self.tasks = {
            task: _FrozenTask.from_payload(task, tasks[task]) for task in TASKS
        }
        training_sha = payload.get("training_dataset_sha256")
        if not isinstance(training_sha, str) or len(training_sha) != 64:
            raise LearnedProbabilityModelError("training dataset SHA-256 is invalid")
        self.training_dataset_sha256 = training_sha
        self.training_data_end = parse_api_datetime(
            payload.get("training_data_end"), name="training_data_end"
        )
        self.artifact_payload_sha256 = expected
        self.artifact_file_sha256 = file_sha256
        self.model_version = f"frozen-logit-v1:{expected[:12]}"

    @classmethod
    def load(
        cls, path: Path, *, history_dataset_path: Path | None = None
    ) -> "FrozenLogitProbabilityEstimator":
        payload = _read_mapping(path, label="frozen probability model")
        estimator = cls(payload, file_sha256=_sha256_file(path))
        if history_dataset_path is not None and _sha256_file(history_dataset_path) != estimator.training_dataset_sha256:
            raise LearnedProbabilityModelError(
                "runtime history dataset does not match frozen model training data"
            )
        return estimator

    def feature_use_contract_template(self) -> Mapping[str, object]:
        """Return the immutable model-derived portion of the runtime contract."""

        active_terms = sorted(
            {
                name
                for task in self.tasks.values()
                for name in (
                    *task.observed_numeric,
                    *(
                        category
                        for category in task.categorical_names
                        if task.categories[category]
                    ),
                )
            }
        )
        active = sorted(
            {
                (
                    "historical_outcome_aggregates"
                    if name.endswith(("_count", "_rate"))
                    else _runtime_input_name(name)
                )
                for name in active_terms
            }
        )
        optional = {
            "air_temperature_c",
            "relative_humidity_percent",
            "surface_pressure_kpa",
            "wind_speed_m_s",
            "precipitation_corrected",
            "space_weather_kp",
            "receive_antenna_gain_dbi",
            "system_noise_temperature_k",
            "antenna_type",
            "antenna_frequency_supported",
        }
        return {
            "schema_version": "probability-feature-use-v1",
            "model_version": self.model_version,
            "model_artifact_payload_sha256": self.artifact_payload_sha256,
            "model_artifact_file_sha256": self.artifact_file_sha256,
            "training_dataset_sha256": self.training_dataset_sha256,
            "active_probability_features": active,
            "evaluation_only_features": sorted(optional - set(active)),
            "evaluation_only_reason": (
                "optional covariates absent here did not pass the bound deployment gate "
                "for either task"
            ),
            "active_model_terms": active_terms,
            "selected_task_models": {
                task: {"model": model.model, "family": model.family}
                for task, model in self.tasks.items()
            },
            "uncertainty_semantics": (
                "empirical validation absolute-error envelope; not a confidence interval"
            ),
            "history_aggregation": "unweighted labeled observations from the bound training dataset",
            "active_feasibility_constraints": [],
        }

    def feature_use_contract(self, features: ReceptionFeatures) -> Mapping[str, object]:
        # ``features`` remains part of the estimator protocol even though the
        # frozen model identity and selected terms are opportunity-independent.
        del features
        return self.feature_use_contract_template()

    def sparse_inference_audit(self) -> Mapping[str, object]:
        """Exercise the frozen estimator with every scientific optional input absent.

        This is an inference-contract check, not a performance estimate.  It proves
        that a publication artifact remains usable for a cold-start opportunity,
        reports which active inputs were missing, and preserves probability
        factorisation and bounds without silently substituting fabricated hardware
        or environmental values.
        """

        sparse = ReceptionFeatures(
            norad_id=2_147_483_647,
            station_id="publication-sparse-input-audit",
            resource_id="publication-sparse-input-audit",
            max_elevation_deg=45.0,
            min_range_km=1_000.0,
            duration_seconds=600.0,
            tle_age_hours=24.0,
            satnogs_station_id=2_147_483_647,
        )
        optional_inputs = {
            "frequency_hz",
            "modulation",
            "baud",
            "transmitter_uuid",
            "transmitter_status",
            "rise_azimuth_deg",
            "set_azimuth_deg",
            "air_temperature_c",
            "relative_humidity_percent",
            "surface_pressure_kpa",
            "wind_speed_m_s",
            "precipitation_corrected",
            "space_weather_kp",
            "receive_antenna_gain_dbi",
            "system_noise_temperature_k",
            "antenna_type",
            "antenna_frequency_supported",
        }
        all_optional_absent = all(
            getattr(sparse, name) is None for name in optional_inputs
        )
        first = self.estimate(sparse, (), transmitter_probability=0.5)
        second = self.estimate(sparse, (), transmitter_probability=0.5)
        expected_missing: set[str] = set()
        for task_name, task in self.tasks.items():
            history, _ = _history_snapshot(task_name, sparse, ())
            _, missing = task.predict(sparse, history)
            expected_missing.update(missing)
        contract = self.feature_use_contract(sparse)
        active = {
            str(name)
            for name in contract.get("active_probability_features", ())
        }
        active_optional = active & optional_inputs
        reported_inputs = {
            name.removesuffix(":unseen") for name in first.missing_features
        }
        probabilities = (
            first.p_signal_present,
            first.p_decode_given_signal,
            first.p_success,
            first.standard_deviation,
            first.lower_90,
            first.upper_90,
        )
        finite = all(math.isfinite(value) for value in probabilities)
        factorized = math.isclose(
            first.p_success,
            first.p_signal_present * first.p_decode_given_signal,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        bounded = (
            0.0 <= first.lower_90 <= first.p_success <= first.upper_90 <= 1.0
            and 0.0 <= first.p_signal_present <= 1.0
            and 0.0 <= first.p_decode_given_signal <= 1.0
            and first.standard_deviation >= 0.0
        )
        deterministic = first == second
        missing_union_valid = set(first.missing_features) == expected_missing
        active_missing_visible = bool(active_optional) and active_optional <= reported_inputs
        passed = (
            all_optional_absent
            and finite
            and factorized
            and bounded
            and deterministic
            and first.evidence_count == 0
            and missing_union_valid
            and active_missing_visible
            and first.model_version == self.model_version
        )
        return {
            "schema_version": "frozen-probability-sparse-inference-audit-v1",
            "passed": passed,
            "all_scientific_optional_inputs_absent": all_optional_absent,
            "active_optional_inputs": sorted(active_optional),
            "reported_missing_features": list(first.missing_features),
            "expected_missing_features": sorted(expected_missing),
            "active_missing_inputs_visible": active_missing_visible,
            "finite": finite,
            "bounded": bounded,
            "factorized": factorized,
            "deterministic": deterministic,
            "evidence_count": first.evidence_count,
            "model_version": first.model_version,
            "p_signal_present": first.p_signal_present,
            "p_decode_given_signal": first.p_decode_given_signal,
            "p_success": first.p_success,
            "lower_90": first.lower_90,
            "upper_90": first.upper_90,
        }

    def estimate(
        self,
        features: ReceptionFeatures,
        evidence: Sequence[ReceptionEvidence],
        *,
        transmitter_probability: float,
    ) -> ProbabilityEstimate:
        if not 0 <= transmitter_probability <= 1:
            raise ValueError("transmitter_probability must be in [0, 1]")
        signal_history, signal_count = _history_snapshot(
            "signal_present", features, evidence
        )
        decode_history, decode_count = _history_snapshot(
            "decode_success_given_signal", features, evidence
        )
        raw_signal, signal_missing = self.tasks["signal_present"].predict(
            features, signal_history
        )
        p_decode, decode_missing = self.tasks[
            "decode_success_given_signal"
        ].predict(features, decode_history)
        p_signal = raw_signal * transmitter_probability
        p_success = p_signal * p_decode
        signal_brier = self.tasks["signal_present"].validation_brier
        decode_brier = self.tasks["decode_success_given_signal"].validation_brier
        standard_deviation = min(
            0.5,
            math.sqrt(
                transmitter_probability**2 * p_decode**2 * signal_brier
                + p_signal**2 * decode_brier
            ),
        )
        signal_error = (
            transmitter_probability
            * self.tasks["signal_present"].validation_error_q90
        )
        decode_error = self.tasks[
            "decode_success_given_signal"
        ].validation_error_q90
        error_envelope = min(
            1.0,
            p_decode * signal_error
            + p_signal * decode_error
            + signal_error * decode_error,
        )
        return ProbabilityEstimate(
            p_transmit=p_signal,
            p_decode_given_transmit=p_decode,
            p_success=p_success,
            standard_deviation=standard_deviation,
            lower_90=max(0.0, p_success - error_envelope),
            upper_90=min(1.0, p_success + error_envelope),
            missing_features=tuple(sorted(set(signal_missing + decode_missing))),
            evidence_count=max(signal_count, decode_count),
            model_version=self.model_version,
        )
