"""Frozen probability baselines and leakage-resistant evaluation utilities."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Literal, Mapping, Sequence

from .dataset import (
    DatasetIntegrityError,
    NormalizedObservation,
    satnogs_mode_is_packet_capable,
)


OutcomeTask = Literal["signal_present", "decode_success_given_signal"]
ModelName = Literal["global_rate", "group_rate", "geometry_logit", "full_logit"]
FeatureFamily = Literal["geometry", "operational", "weather", "hardware", "full"]


@dataclass(frozen=True, slots=True)
class FeatureExample:
    row: NormalizedObservation
    outcome: int
    history: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class PredictionRecord:
    observation_id: int
    task: str
    split: str
    fold: str
    model: str
    outcome: int
    probability: float
    norad_id: int
    station_id: int
    start: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ComposedPredictionRecord:
    """End-to-end reception prediction with auditable probability factors."""

    observation_id: int
    task: str
    split: str
    fold: str
    model: str
    outcome: int
    probability: float
    signal_probability: float
    decode_probability_given_signal: float
    norad_id: int
    station_id: int
    start: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProbabilityMetrics:
    count: int
    positives: int
    brier: float
    log_loss: float
    auroc: float | None
    calibration_intercept: float | None
    calibration_slope: float | None
    expected_calibration_error: float
    interval_coverage_90: float | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    task: str
    split: str
    predictions: tuple[PredictionRecord, ...]
    metrics: Mapping[str, ProbabilityMetrics]
    selected_regularization: Mapping[str, float]
    train_count: int
    test_count: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True, slots=True)
class BootstrapDifference:
    model: str
    baseline: str
    aligned_prediction_count: int
    cluster_count: int
    estimate: float
    lower_95: float
    upper_95: float
    relative_brier_reduction: float
    replicates: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CovariateAblationResult:
    task: str
    cutoff: str
    train_count: int
    test_count: int
    available_families: tuple[str, ...]
    unavailable_reasons: Mapping[str, str]
    selected_regularization: Mapping[str, float]
    predictions: tuple[PredictionRecord, ...]
    metrics: Mapping[str, ProbabilityMetrics]


@dataclass(frozen=True, slots=True)
class ComposedReceptionResult:
    """Evaluation of P(signal) * P(decode | signal) on one held-out set."""

    split: str
    predictions: tuple[ComposedPredictionRecord, ...]
    metrics: Mapping[str, ProbabilityMetrics]
    decision_metrics: Mapping[str, Mapping[str, float | int | None]]
    selected_regularization: Mapping[str, float]
    signal_train_count: int
    decode_train_count: int
    test_count: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str


class _HistoryAccumulator:
    def __init__(self) -> None:
        self.total_success = 0
        self.total_count = 0
        self.groups: dict[str, defaultdict[object, list[int]]] = {
            "sat": defaultdict(lambda: [0, 0]),
            "station": defaultdict(lambda: [0, 0]),
            "transmitter": defaultdict(lambda: [0, 0]),
            "pair": defaultdict(lambda: [0, 0]),
        }

    @staticmethod
    def _keys(row: NormalizedObservation) -> Mapping[str, object]:
        return {
            "sat": row.norad_id,
            "station": row.station_id,
            "transmitter": row.transmitter_uuid,
            "pair": (row.norad_id, row.station_id),
        }

    def update(self, row: NormalizedObservation, outcome: int) -> None:
        self.total_success += outcome
        self.total_count += 1
        for name, key in self._keys(row).items():
            values = self.groups[name][key]
            values[0] += outcome
            values[1] += 1

    def snapshot(self, row: NormalizedObservation) -> dict[str, float]:
        result: dict[str, float] = {
            "global_success": float(self.total_success),
            "global_count": float(self.total_count),
            "global_rate": (self.total_success + 1.0) / (self.total_count + 2.0),
        }
        for name, key in self._keys(row).items():
            success, count = self.groups[name].get(key, [0, 0])
            result[f"{name}_success"] = float(success)
            result[f"{name}_count"] = float(count)
            result[f"{name}_rate"] = (success + 1.0) / (count + 2.0)
        return result


def _outcome(row: NormalizedObservation, task: OutcomeTask) -> int | None:
    value = getattr(row, task)
    return int(value) if value is not None else None


def reception_success_outcome(row: NormalizedObservation) -> int | None:
    """Return the end-to-end packet reception label when it is observable.

    A reviewed no-signal packet-mode observation is a definite failed
    reception.  For a reviewed positive signal, the demodulation artifact
    label determines success.  Non-packet and unreviewed observations are not
    silently converted to failures.
    """

    if not satnogs_mode_is_packet_capable(row.transmitter_mode):
        return None
    if row.signal_present == 0:
        return 0
    if row.signal_present == 1 and row.decode_success_given_signal in {0, 1}:
        return int(row.decode_success_given_signal)
    return None


def task_rows(
    rows: Sequence[NormalizedObservation], task: OutcomeTask
) -> tuple[NormalizedObservation, ...]:
    return tuple(
        sorted(
            (row for row in rows if _outcome(row, task) is not None),
            key=lambda row: (row.start, row.observation_id),
        )
    )


def _temporal_split_index(
    rows: Sequence[NormalizedObservation],
    test_fraction: float,
    *,
    cutoff: datetime | None = None,
) -> int:
    """Choose the nearest viable split without dividing equal timestamps."""

    if len(rows) < 11:
        raise DatasetIntegrityError("temporal split requires at least 11 labeled rows")
    if cutoff is not None:
        if cutoff.tzinfo is None:
            raise ValueError("temporal cutoff must include a timezone")
        cutoff = cutoff.astimezone(UTC)
        boundary = next(
            (index for index, row in enumerate(rows) if row.start >= cutoff),
            len(rows),
        )
        if boundary < 10 or boundary >= len(rows):
            raise DatasetIntegrityError(
                "predeclared temporal cutoff requires at least 10 training rows "
                "and one test row"
            )
        return boundary
    nominal = max(10, min(len(rows) - 1, int(len(rows) * (1.0 - test_fraction))))
    candidates = [
        index
        for index in range(10, len(rows))
        if rows[index - 1].start < rows[index].start
    ]
    if not candidates:
        raise DatasetIntegrityError(
            "temporal split requires a timestamp boundary after at least 10 rows"
        )
    return min(candidates, key=lambda index: (abs(index - nominal), index))


def _time_separated_rows(
    rows: Sequence[NormalizedObservation],
    test_fraction: float,
    *,
    cutoff: datetime | None = None,
) -> tuple[
    tuple[NormalizedObservation, ...],
    tuple[NormalizedObservation, ...],
    datetime,
]:
    """Split by event time and discard observations crossing the boundary.

    A training label is not available until its observation has ended.  Merely
    comparing start timestamps can therefore put the outcome of an overlapping
    pass on the training side of a future prediction.
    """

    boundary = _temporal_split_index(rows, test_fraction, cutoff=cutoff)
    split_cutoff = (
        cutoff.astimezone(UTC)
        if cutoff is not None
        else rows[boundary].start
    )
    training = tuple(
        row for row in rows if row.start < split_cutoff and row.end < split_cutoff
    )
    test = tuple(row for row in rows if row.start >= split_cutoff)
    if len(training) < 10 or not test:
        raise DatasetIntegrityError(
            "time-separated split requires at least 10 completed training rows "
            "and one non-overlapping test row"
        )
    return training, test, split_cutoff


def fold_examples(
    training_rows: Sequence[NormalizedObservation],
    test_rows: Sequence[NormalizedObservation],
    task: OutcomeTask,
) -> tuple[tuple[FeatureExample, ...], tuple[FeatureExample, ...]]:
    """Build histories from eligible training outcomes only and strictly in time."""

    training = task_rows(training_rows, task)
    test = task_rows(test_rows, task)
    accumulator = _HistoryAccumulator()
    train_examples: list[FeatureExample] = []
    completed_training = tuple(
        sorted(training, key=lambda row: (row.end, row.start, row.observation_id))
    )
    completed_index = 0
    for row in training:
        # Only labels from observations completed strictly before this pass
        # may enter its history. Held-open or simultaneous receivers cannot
        # reveal their eventual outcomes to one another.
        while (
            completed_index < len(completed_training)
            and completed_training[completed_index].end < row.start
        ):
            prior = completed_training[completed_index]
            outcome = _outcome(prior, task)
            assert outcome is not None
            accumulator.update(prior, outcome)
            completed_index += 1
        outcome = _outcome(row, task)
        assert outcome is not None
        train_examples.append(FeatureExample(row, outcome, accumulator.snapshot(row)))

    # Rebuild for the held-out rows.  A test row may use only training outcomes
    # that happened before its start; held-out outcomes are never fed back.
    accumulator = _HistoryAccumulator()
    completed_index = 0
    test_examples: list[FeatureExample] = []
    for row in test:
        while (
            completed_index < len(completed_training)
            and completed_training[completed_index].end < row.start
        ):
            prior = completed_training[completed_index]
            outcome = _outcome(prior, task)
            assert outcome is not None
            accumulator.update(prior, outcome)
            completed_index += 1
        outcome = _outcome(row, task)
        assert outcome is not None
        test_examples.append(FeatureExample(row, outcome, accumulator.snapshot(row)))
    return tuple(train_examples), tuple(test_examples)


def _history_conditioned_target_examples(
    training_rows: Sequence[NormalizedObservation],
    target_rows: Sequence[NormalizedObservation],
    *,
    history_task: OutcomeTask,
) -> tuple[FeatureExample, ...]:
    """Build target features from completed training labels only.

    This differs from ``fold_examples`` because an end-to-end target with no
    detected signal has no observed conditional-decode label.  It still needs
    a conditional-decode probability in order to evaluate the chain rule, but
    its outcome must never enter the conditional model's history.
    """

    training = task_rows(training_rows, history_task)
    targets = tuple(
        sorted(
            target_rows,
            key=lambda row: (row.start, row.observation_id),
        )
    )
    completed_training = tuple(
        sorted(training, key=lambda row: (row.end, row.start, row.observation_id))
    )
    accumulator = _HistoryAccumulator()
    completed_index = 0
    examples: list[FeatureExample] = []
    for row in targets:
        while (
            completed_index < len(completed_training)
            and completed_training[completed_index].end < row.start
        ):
            prior = completed_training[completed_index]
            outcome = _outcome(prior, history_task)
            assert outcome is not None
            accumulator.update(prior, outcome)
            completed_index += 1
        endpoint = reception_success_outcome(row)
        if endpoint is None:
            raise DatasetIntegrityError(
                "composed reception target has no observable endpoint"
            )
        examples.append(FeatureExample(row, endpoint, accumulator.snapshot(row)))
    return tuple(examples)


def _numeric_value(example: FeatureExample, name: str) -> float | None:
    row = example.row
    if name == "max_elevation_deg":
        return row.max_elevation_deg
    if name == "log_min_range_km":
        return math.log1p(max(0.0, row.min_range_km))
    if name == "log_duration_seconds":
        return math.log1p(max(0.0, row.duration_seconds))
    if name == "log_tle_age_hours":
        return math.log1p(max(0.0, row.tle_age_hours))
    if name == "log_max_abs_doppler_hz":
        return (
            math.log1p(row.max_abs_doppler_hz)
            if row.max_abs_doppler_hz is not None and row.max_abs_doppler_hz >= 0
            else None
        )
    if name == "rise_azimuth_sin":
        return math.sin(math.radians(row.rise_azimuth_deg))
    if name == "rise_azimuth_cos":
        return math.cos(math.radians(row.rise_azimuth_deg))
    if name == "set_azimuth_sin":
        return math.sin(math.radians(row.set_azimuth_deg))
    if name == "set_azimuth_cos":
        return math.cos(math.radians(row.set_azimuth_deg))
    if name == "station_latitude_deg":
        return row.station_latitude_deg
    if name == "station_longitude_sin":
        return math.sin(math.radians(row.station_longitude_deg))
    if name == "station_longitude_cos":
        return math.cos(math.radians(row.station_longitude_deg))
    if name == "log_station_altitude_m":
        return math.copysign(math.log1p(abs(row.station_altitude_m)), row.station_altitude_m)
    if name == "log_frequency_hz":
        return math.log(row.frequency_hz) if row.frequency_hz is not None and row.frequency_hz > 0 else None
    if name == "log_transmitter_baud":
        return (
            math.log(row.transmitter_baud)
            if row.transmitter_baud is not None and row.transmitter_baud > 0
            else None
        )
    if name in {
        "weather_air_temperature_c",
        "weather_relative_humidity_percent",
        "weather_surface_pressure_kpa",
        "weather_wind_speed_m_s",
        "weather_precipitation_corrected",
        "space_weather_kp",
        "receive_antenna_gain_dbi",
    }:
        value = getattr(row, name)
        return float(value) if value is not None else None
    if name == "log_system_noise_temperature_k":
        return (
            math.log(row.system_noise_temperature_k)
            if row.system_noise_temperature_k is not None
            and row.system_noise_temperature_k > 0
            else None
        )
    if name.endswith("_count") and name in example.history:
        return math.log1p(example.history[name])
    if name.endswith("_rate") and name in example.history:
        return example.history[name]
    raise KeyError(name)


GEOMETRY_NUMERIC = (
    "max_elevation_deg",
    "log_duration_seconds",
    "log_tle_age_hours",
    "rise_azimuth_sin",
    "rise_azimuth_cos",
    "set_azimuth_sin",
    "set_azimuth_cos",
)
OPERATIONAL_NUMERIC = GEOMETRY_NUMERIC + (
    "log_frequency_hz",
    "log_transmitter_baud",
    "sat_count",
    "sat_rate",
    "station_count",
    "station_rate",
    "transmitter_count",
    "transmitter_rate",
    "pair_count",
    "pair_rate",
)
WEATHER_NUMERIC = (
    "weather_air_temperature_c",
    "weather_relative_humidity_percent",
    "weather_surface_pressure_kpa",
    "weather_wind_speed_m_s",
    "weather_precipitation_corrected",
    "space_weather_kp",
)
HARDWARE_NUMERIC = (
    "receive_antenna_gain_dbi",
    "log_system_noise_temperature_k",
)
OPERATIONAL_CATEGORICAL = (
    "norad_id",
    "station_id",
    "transmitter_uuid",
    "transmitter_mode",
    "transmitter_status",
)
HARDWARE_CATEGORICAL = (
    "antenna_type",
    "antenna_frequency_supported",
)
FULL_NUMERIC = OPERATIONAL_NUMERIC + WEATHER_NUMERIC + HARDWARE_NUMERIC
FULL_CATEGORICAL = OPERATIONAL_CATEGORICAL + HARDWARE_CATEGORICAL


def _categorical_value(example: FeatureExample, name: str) -> str:
    value = getattr(example.row, name)
    return "<MISSING>" if value is None else str(value)


class _MatrixEncoder:
    def __init__(self, family: FeatureFamily) -> None:
        self.family = family
        self.numeric_names = {
            "geometry": GEOMETRY_NUMERIC,
            "operational": OPERATIONAL_NUMERIC,
            "weather": OPERATIONAL_NUMERIC + WEATHER_NUMERIC,
            "hardware": OPERATIONAL_NUMERIC + HARDWARE_NUMERIC,
            "full": FULL_NUMERIC,
        }[family]
        self.categorical_names = {
            "geometry": (),
            "operational": OPERATIONAL_CATEGORICAL,
            "weather": OPERATIONAL_CATEGORICAL,
            "hardware": OPERATIONAL_CATEGORICAL + HARDWARE_CATEGORICAL,
            "full": FULL_CATEGORICAL,
        }[family]
        self.medians: dict[str, float] = {}
        self.means: dict[str, float] = {}
        self.scales: dict[str, float] = {}
        self.categories: dict[str, tuple[str, ...]] = {}
        self.observed_numeric: set[str] = set()

    def fit(self, examples: Sequence[FeatureExample]) -> "_MatrixEncoder":
        if not examples:
            raise DatasetIntegrityError("cannot fit preprocessing on an empty fold")
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("publication evaluation requires NumPy") from exc
        for name in self.numeric_names:
            observed = [
                value
                for example in examples
                if (value := _numeric_value(example, name)) is not None
            ]
            if observed:
                self.observed_numeric.add(name)
            median = float(np.median(observed)) if observed else 0.0
            filled = [
                value if (value := _numeric_value(example, name)) is not None else median
                for example in examples
            ]
            self.medians[name] = median
            self.means[name] = float(np.mean(filled))
            scale = float(np.std(filled))
            self.scales[name] = scale if scale > 1e-12 else 1.0
        for name in self.categorical_names:
            counts = Counter(_categorical_value(example, name) for example in examples)
            self.categories[name] = (
                () if set(counts) == {"<MISSING>"} else tuple(sorted(counts))
            )
        return self

    def transform(self, examples: Sequence[FeatureExample]):
        import numpy as np

        width = 1 + 2 * len(self.numeric_names) + sum(
            len(self.categories[name]) for name in self.categorical_names
        )
        matrix = np.zeros((len(examples), width), dtype=float)
        matrix[:, 0] = 1.0
        for row_index, example in enumerate(examples):
            column = 1
            for name in self.numeric_names:
                value = _numeric_value(example, name)
                missing = value is None
                filled = self.medians[name] if missing else value
                assert filled is not None
                matrix[row_index, column] = (filled - self.means[name]) / self.scales[name]
                # An entirely unavailable training feature must be a zero
                # column, not a duplicate intercept made of all-one missing
                # flags.  This preserves old-model predictions when a new
                # optional covariate has not yet been collected.
                matrix[row_index, column + 1] = float(
                    missing and name in self.observed_numeric
                )
                column += 2
            for name in self.categorical_names:
                value = _categorical_value(example, name)
                categories = self.categories[name]
                try:
                    offset = categories.index(value)
                except ValueError:
                    offset = -1
                if offset >= 0:
                    matrix[row_index, column + offset] = 1.0
                column += len(categories)
        return matrix


@dataclass(slots=True)
class _FittedLogit:
    encoder: _MatrixEncoder
    coefficients: object

    def predict(self, examples: Sequence[FeatureExample]):
        import numpy as np
        from scipy.special import expit

        return np.clip(expit(self.encoder.transform(examples) @ self.coefficients), 1e-8, 1 - 1e-8)


def _fit_logit(
    examples: Sequence[FeatureExample],
    family: FeatureFamily,
    regularization: float,
) -> _FittedLogit:
    import numpy as np
    from scipy.optimize import minimize
    from scipy.special import expit

    encoder = _MatrixEncoder(family).fit(examples)
    matrix = encoder.transform(examples)
    outcomes = np.asarray([example.outcome for example in examples], dtype=float)
    initial = np.zeros(matrix.shape[1], dtype=float)
    rate = (float(outcomes.sum()) + 1.0) / (len(outcomes) + 2.0)
    initial[0] = math.log(rate / (1.0 - rate))

    def objective(coefficients):
        linear = matrix @ coefficients
        loss = float(np.mean(np.logaddexp(0.0, linear) - outcomes * linear))
        loss += 0.5 * regularization * float(coefficients[1:] @ coefficients[1:])
        probabilities = expit(linear)
        gradient = matrix.T @ (probabilities - outcomes) / len(outcomes)
        gradient[1:] += regularization * coefficients[1:]
        return loss, gradient

    fitted = minimize(
        objective,
        initial,
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 500, "ftol": 1e-11},
    )
    if not fitted.success or not math.isfinite(float(fitted.fun)):
        raise RuntimeError(f"logistic regression failed: {fitted.message}")
    return _FittedLogit(encoder, fitted.x)


def _forward_regularization(
    examples: Sequence[FeatureExample],
    family: FeatureFamily,
    grid: Sequence[float],
    task: OutcomeTask,
) -> float:
    import numpy as np

    if len(examples) < 40:
        return float(grid[0])
    time_groups: list[list[int]] = []
    for index, example in enumerate(examples):
        if (
            not time_groups
            or examples[time_groups[-1][0]].row.start != example.row.start
        ):
            time_groups.append([])
        time_groups[-1].append(index)
    grouped_blocks = np.array_split(
        np.arange(len(time_groups)), min(4, len(time_groups))
    )
    blocks = [
        np.asarray(
            [index for group_index in group_block for index in time_groups[int(group_index)]],
            dtype=int,
        )
        for group_block in grouped_blocks
        if len(group_block)
    ]
    scores: dict[float, list[float]] = {float(value): [] for value in grid}
    for validation_block in range(1, len(blocks)):
        train_indices = np.concatenate(blocks[:validation_block])
        validation_indices = blocks[validation_block]
        validation_rows = [examples[int(index)].row for index in validation_indices]
        validation_start = min(row.start for row in validation_rows)
        training_rows = [
            examples[int(index)].row
            for index in train_indices
            if examples[int(index)].row.end < validation_start
        ]
        training, validation = fold_examples(training_rows, validation_rows, task)
        if len(training) < 10 or not validation:
            continue
        outcomes = np.asarray([example.outcome for example in validation], dtype=float)
        for value in scores:
            probabilities = _fit_logit(training, family, value).predict(validation)
            scores[value].append(float(np.mean((outcomes - probabilities) ** 2)))
    return min(scores, key=lambda value: (sum(scores[value]) / len(scores[value]) if scores[value] else math.inf, value))


def _global_probability(training: Sequence[FeatureExample]) -> float:
    return (sum(example.outcome for example in training) + 1.0) / (len(training) + 2.0)


def _group_probability(example: FeatureExample, global_rate: float) -> float:
    # Empirical-Bayes shrinkage.  Sparse pair/satellite/station histories fall
    # back smoothly to the training-fold global event rate.
    components = [(global_rate, 1.0)]
    for name, strength in (("sat", 10.0), ("station", 10.0), ("pair", 5.0)):
        count = example.history[f"{name}_count"]
        success = example.history[f"{name}_success"]
        posterior = (success + strength * global_rate) / (count + strength)
        components.append((posterior, math.log1p(count)))
    denominator = sum(weight for _, weight in components)
    return sum(value * weight for value, weight in components) / denominator


def _rank_auc(outcomes, probabilities) -> float | None:
    import numpy as np
    from scipy.stats import rankdata

    outcomes = np.asarray(outcomes, dtype=int)
    positives = int(outcomes.sum())
    negatives = len(outcomes) - positives
    if positives == 0 or negatives == 0:
        return None
    ranks = rankdata(probabilities, method="average")
    return float((ranks[outcomes == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def _calibration(outcomes, probabilities) -> tuple[float | None, float | None]:
    import numpy as np
    from scipy.optimize import minimize
    from scipy.special import expit

    outcomes = np.asarray(outcomes, dtype=float)
    if len(set(outcomes.tolist())) < 2:
        return None, None
    clipped = np.clip(np.asarray(probabilities, dtype=float), 1e-8, 1 - 1e-8)
    logits = np.log(clipped / (1 - clipped))
    matrix = np.column_stack((np.ones(len(logits)), logits))

    def objective(coefficients):
        linear = matrix @ coefficients
        loss = float(np.mean(np.logaddexp(0, linear) - outcomes * linear))
        gradient = matrix.T @ (expit(linear) - outcomes) / len(outcomes)
        return loss, gradient

    fitted = minimize(objective, np.array([0.0, 1.0]), method="L-BFGS-B", jac=True)
    if not fitted.success:
        return None, None
    return float(fitted.x[0]), float(fitted.x[1])


def probability_metrics(outcomes, probabilities) -> ProbabilityMetrics:
    import numpy as np

    outcomes = np.asarray(outcomes, dtype=float)
    probabilities = np.asarray(probabilities, dtype=float)
    if len(outcomes) == 0 or len(outcomes) != len(probabilities):
        raise DatasetIntegrityError("metrics require aligned non-empty arrays")
    if not np.all(np.isfinite(outcomes)) or not np.all(
        np.isin(outcomes, (0.0, 1.0))
    ):
        raise DatasetIntegrityError("metric outcomes must be binary and finite")
    if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0) or np.any(
        probabilities > 1
    ):
        raise DatasetIntegrityError(
            "metric probabilities must be finite and inside [0, 1]"
        )
    probabilities = np.clip(probabilities, 1e-8, 1 - 1e-8)
    order = np.argsort(probabilities, kind="stable")
    bins = [part for part in np.array_split(order, min(10, len(order))) if len(part)]
    ece = sum(
        len(part) / len(outcomes) * abs(float(outcomes[part].mean() - probabilities[part].mean()))
        for part in bins
    )
    intercept, slope = _calibration(outcomes, probabilities)
    return ProbabilityMetrics(
        count=len(outcomes),
        positives=int(outcomes.sum()),
        brier=float(np.mean((outcomes - probabilities) ** 2)),
        log_loss=float(-np.mean(outcomes * np.log(probabilities) + (1 - outcomes) * np.log(1 - probabilities))),
        auroc=_rank_auc(outcomes, probabilities),
        calibration_intercept=intercept,
        calibration_slope=slope,
        expected_calibration_error=float(ece),
        interval_coverage_90=None,
    )


def binary_decision_metrics(
    outcomes, probabilities, *, threshold: float = 0.5
) -> dict[str, float | int | None]:
    """Report transparent yes/no performance for a fixed probability cutoff."""

    import numpy as np

    if not 0 < threshold < 1:
        raise ValueError("decision threshold must be in (0, 1)")
    raw_observed = np.asarray(outcomes, dtype=float)
    predicted_probability = np.asarray(probabilities, dtype=float)
    if len(raw_observed) == 0 or len(raw_observed) != len(predicted_probability):
        raise DatasetIntegrityError("decision metrics require aligned non-empty arrays")
    if not np.all(np.isfinite(raw_observed)) or not np.all(
        np.isin(raw_observed, (0.0, 1.0))
    ):
        raise DatasetIntegrityError("decision outcomes must be binary and finite")
    if not np.all(np.isfinite(predicted_probability)) or np.any(
        predicted_probability < 0
    ) or np.any(predicted_probability > 1):
        raise DatasetIntegrityError(
            "decision probabilities must be finite and inside [0, 1]"
        )
    observed = raw_observed.astype(int)
    predicted = predicted_probability >= threshold
    positive = observed == 1
    negative = observed == 0
    true_positive = int(np.sum(predicted & positive))
    true_negative = int(np.sum(~predicted & negative))
    false_positive = int(np.sum(predicted & negative))
    false_negative = int(np.sum(~predicted & positive))

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    return {
        "threshold": threshold,
        "count": len(observed),
        "true_positive": true_positive,
        "true_negative": true_negative,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "accuracy": (true_positive + true_negative) / len(observed),
        "sensitivity": ratio(true_positive, true_positive + false_negative),
        "specificity": ratio(true_negative, true_negative + false_positive),
        "positive_predictive_value": ratio(
            true_positive, true_positive + false_positive
        ),
        "negative_predictive_value": ratio(
            true_negative, true_negative + false_negative
        ),
    }


def _prediction_records(
    examples: Sequence[FeatureExample],
    probabilities,
    *,
    task: OutcomeTask,
    split: str,
    fold: str,
    model: ModelName,
) -> tuple[PredictionRecord, ...]:
    return tuple(
        PredictionRecord(
            observation_id=example.row.observation_id,
            task=task,
            split=split,
            fold=fold,
            model=model,
            outcome=example.outcome,
            probability=float(probability),
            norad_id=example.row.norad_id,
            station_id=example.row.station_id,
            start=example.row.start.isoformat().replace("+00:00", "Z"),
        )
        for example, probability in zip(examples, probabilities, strict=True)
    )


def evaluate_fold(
    training_rows: Sequence[NormalizedObservation],
    test_rows: Sequence[NormalizedObservation],
    *,
    task: OutcomeTask,
    split: str,
    fold: str,
    regularization_grid: Sequence[float],
) -> tuple[tuple[PredictionRecord, ...], Mapping[str, float]]:
    import numpy as np

    training, test = fold_examples(training_rows, test_rows, task)
    if len(training) < 10 or not test:
        raise DatasetIntegrityError("evaluation fold is too small")
    global_rate = _global_probability(training)
    probabilities: dict[ModelName, object] = {
        "global_rate": np.full(len(test), global_rate),
        "group_rate": np.asarray([_group_probability(example, global_rate) for example in test]),
    }
    selected: dict[str, float] = {}
    for family, model in (("geometry", "geometry_logit"), ("full", "full_logit")):
        value = _forward_regularization(
            training, family, regularization_grid, task
        )  # type: ignore[arg-type]
        selected[model] = value
        probabilities[model] = _fit_logit(training, family, value).predict(test)  # type: ignore[arg-type]
    records: list[PredictionRecord] = []
    for model in ("global_rate", "group_rate", "geometry_logit", "full_logit"):
        records.extend(
            _prediction_records(
                test,
                probabilities[model],
                task=task,
                split=split,
                fold=fold,
                model=model,
            )
        )
    return tuple(records), selected


def _evaluate_composed_reception_fold(
    signal_training_rows: Sequence[NormalizedObservation],
    decode_training_rows: Sequence[NormalizedObservation],
    test_rows: Sequence[NormalizedObservation],
    *,
    split: str,
    fold: str,
    regularization_grid: Sequence[float],
) -> ComposedReceptionResult:
    """Fit the two conditional models and score their product on one fold."""

    import numpy as np

    signal_training, _ = fold_examples(
        signal_training_rows, (), "signal_present"
    )
    decode_training, _ = fold_examples(
        decode_training_rows, (), "decode_success_given_signal"
    )
    targets = tuple(
        sorted(
            (row for row in test_rows if reception_success_outcome(row) is not None),
            key=lambda row: (row.start, row.observation_id),
        )
    )
    if len(signal_training) < 10 or len(decode_training) < 10 or not targets:
        raise DatasetIntegrityError("composed reception evaluation fold is too small")
    if {example.outcome for example in signal_training} != {0, 1}:
        raise DatasetIntegrityError(
            "composed reception signal training requires both outcome classes"
        )
    if {example.outcome for example in decode_training} != {0, 1}:
        raise DatasetIntegrityError(
            "composed reception decode training requires both outcome classes"
        )

    signal_targets = _history_conditioned_target_examples(
        signal_training_rows,
        targets,
        history_task="signal_present",
    )
    decode_targets = _history_conditioned_target_examples(
        decode_training_rows,
        targets,
        history_task="decode_success_given_signal",
    )
    if [item.row.observation_id for item in signal_targets] != [
        item.row.observation_id for item in decode_targets
    ]:
        raise DatasetIntegrityError("composed reception targets are not aligned")

    signal_global = _global_probability(signal_training)
    decode_global = _global_probability(decode_training)
    signal_probabilities: dict[ModelName, object] = {
        "global_rate": np.full(len(signal_targets), signal_global),
        "group_rate": np.asarray(
            [
                _group_probability(example, signal_global)
                for example in signal_targets
            ]
        ),
    }
    decode_probabilities: dict[ModelName, object] = {
        "global_rate": np.full(len(decode_targets), decode_global),
        "group_rate": np.asarray(
            [
                _group_probability(example, decode_global)
                for example in decode_targets
            ]
        ),
    }
    selected: dict[str, float] = {}
    for family, model in (("geometry", "geometry_logit"), ("full", "full_logit")):
        signal_regularization = _forward_regularization(
            signal_training,
            family,  # type: ignore[arg-type]
            regularization_grid,
            "signal_present",
        )
        decode_regularization = _forward_regularization(
            decode_training,
            family,  # type: ignore[arg-type]
            regularization_grid,
            "decode_success_given_signal",
        )
        selected[f"signal_present.{model}"] = signal_regularization
        selected[
            f"decode_success_given_signal.{model}"
        ] = decode_regularization
        signal_probabilities[model] = _fit_logit(
            signal_training,
            family,  # type: ignore[arg-type]
            signal_regularization,
        ).predict(signal_targets)
        decode_probabilities[model] = _fit_logit(
            decode_training,
            family,  # type: ignore[arg-type]
            decode_regularization,
        ).predict(decode_targets)

    records: list[ComposedPredictionRecord] = []
    metrics: dict[str, ProbabilityMetrics] = {}
    decisions: dict[str, Mapping[str, float | int | None]] = {}
    outcomes = np.asarray([example.outcome for example in signal_targets], dtype=int)
    for model in ("global_rate", "group_rate", "geometry_logit", "full_logit"):
        signal_values = np.asarray(signal_probabilities[model], dtype=float)
        decode_values = np.asarray(decode_probabilities[model], dtype=float)
        composed_values = signal_values * decode_values
        model_records = tuple(
            ComposedPredictionRecord(
                observation_id=example.row.observation_id,
                task="reception_success",
                split=split,
                fold=fold,
                model=model,
                outcome=example.outcome,
                probability=float(probability),
                signal_probability=float(signal_probability),
                decode_probability_given_signal=float(decode_probability),
                norad_id=example.row.norad_id,
                station_id=example.row.station_id,
                start=example.row.start.isoformat().replace("+00:00", "Z"),
            )
            for example, probability, signal_probability, decode_probability in zip(
                signal_targets,
                composed_values,
                signal_values,
                decode_values,
                strict=True,
            )
        )
        records.extend(model_records)
        metrics[model] = probability_metrics(outcomes, composed_values)
        decisions[model] = binary_decision_metrics(outcomes, composed_values)

    training_rows = tuple(signal_training_rows) + tuple(decode_training_rows)
    return ComposedReceptionResult(
        split=split,
        predictions=tuple(records),
        metrics=metrics,
        decision_metrics=decisions,
        selected_regularization=selected,
        signal_train_count=len(signal_training),
        decode_train_count=len(decode_training),
        test_count=len(targets),
        train_start=min(row.start for row in training_rows)
        .isoformat()
        .replace("+00:00", "Z"),
        train_end=max(row.end for row in training_rows)
        .isoformat()
        .replace("+00:00", "Z"),
        test_start=targets[0].start.isoformat().replace("+00:00", "Z"),
        test_end=max(row.end for row in targets).isoformat().replace("+00:00", "Z"),
    )


def evaluate_composed_reception_temporal_holdout(
    rows: Sequence[NormalizedObservation],
    *,
    test_fraction: float,
    regularization_grid: Sequence[float],
    cutoff: datetime | None = None,
) -> ComposedReceptionResult:
    """Evaluate the end-to-end packet-reception probability on a future tail."""

    endpoint_rows = tuple(
        sorted(
            (row for row in rows if reception_success_outcome(row) is not None),
            key=lambda row: (row.start, row.observation_id),
        )
    )
    if len(endpoint_rows) < 20:
        raise DatasetIntegrityError(
            "composed reception evaluation requires at least 20 labeled rows"
        )
    boundary = _temporal_split_index(endpoint_rows, test_fraction, cutoff=cutoff)
    split_cutoff = cutoff.astimezone(UTC) if cutoff is not None else endpoint_rows[
        boundary
    ].start
    signal_training_rows = tuple(
        row
        for row in rows
        if row.signal_present in {0, 1} and row.end < split_cutoff
    )
    decode_training_rows = tuple(
        row
        for row in rows
        if row.decode_success_given_signal in {0, 1} and row.end < split_cutoff
    )
    test_rows = tuple(row for row in endpoint_rows if row.start >= split_cutoff)
    return _evaluate_composed_reception_fold(
        signal_training_rows,
        decode_training_rows,
        test_rows,
        split="temporal-composed",
        fold=(
            "predeclared-final-panel" if cutoff is not None else "final-20-percent"
        ),
        regularization_grid=regularization_grid,
    )


def evaluate_composed_reception_group_holdout(
    rows: Sequence[NormalizedObservation],
    *,
    group: Literal["norad_id", "station_id"],
    held_out_values: Sequence[int],
    regularization_grid: Sequence[float],
    test_fraction: float,
    minimum_test_rows: int,
    minimum_test_groups: int,
    cutoff: datetime | None = None,
) -> ComposedReceptionResult:
    """Evaluate end-to-end reception on future rows from unseen groups."""

    endpoint_rows = tuple(
        sorted(
            (row for row in rows if reception_success_outcome(row) is not None),
            key=lambda row: (row.start, row.observation_id),
        )
    )
    if len(endpoint_rows) < 20:
        raise DatasetIntegrityError(
            "composed reception external validation requires 20 labeled rows"
        )
    held_out = {int(value) for value in held_out_values}
    if not held_out:
        raise DatasetIntegrityError(
            "composed reception validation requires held-out groups"
        )
    boundary = _temporal_split_index(endpoint_rows, test_fraction, cutoff=cutoff)
    split_cutoff = cutoff.astimezone(UTC) if cutoff is not None else endpoint_rows[
        boundary
    ].start

    def is_held_out(row: NormalizedObservation) -> bool:
        return int(getattr(row, group)) in held_out

    signal_training_rows = tuple(
        row
        for row in rows
        if row.signal_present in {0, 1}
        and row.end < split_cutoff
        and not is_held_out(row)
    )
    decode_training_rows = tuple(
        row
        for row in rows
        if row.decode_success_given_signal in {0, 1}
        and row.end < split_cutoff
        and not is_held_out(row)
    )
    test_rows = tuple(
        row
        for row in endpoint_rows
        if row.start >= split_cutoff and is_held_out(row)
    )
    represented_groups = {int(getattr(row, group)) for row in test_rows}
    if len(test_rows) < minimum_test_rows:
        raise DatasetIntegrityError(
            "composed reception external test set is below the frozen row minimum"
        )
    if len(represented_groups) < minimum_test_groups:
        raise DatasetIntegrityError(
            "composed reception external test set is below the frozen group minimum"
        )
    return _evaluate_composed_reception_fold(
        signal_training_rows,
        decode_training_rows,
        test_rows,
        split=f"external-{group}-composed",
        fold="predeclared-held-out-groups",
        regularization_grid=regularization_grid,
    )


def evaluate_temporal_holdout(
    rows: Sequence[NormalizedObservation],
    *,
    task: OutcomeTask,
    test_fraction: float,
    regularization_grid: Sequence[float],
    cutoff: datetime | None = None,
) -> EvaluationResult:
    eligible = task_rows(rows, task)
    if len(eligible) < 20:
        raise DatasetIntegrityError("temporal evaluation requires at least 20 labeled rows")
    training_rows, test_rows, _split_cutoff = _time_separated_rows(
        eligible, test_fraction, cutoff=cutoff
    )
    predictions, selected = evaluate_fold(
        training_rows,
        test_rows,
        task=task,
        split="temporal",
        fold="predeclared-final-panel" if cutoff is not None else "final-20-percent",
        regularization_grid=regularization_grid,
    )
    metrics: dict[str, ProbabilityMetrics] = {}
    for model in ("global_rate", "group_rate", "geometry_logit", "full_logit"):
        subset = [record for record in predictions if record.model == model]
        metrics[model] = probability_metrics(
            [record.outcome for record in subset],
            [record.probability for record in subset],
        )
    return EvaluationResult(
        task=task,
        split="temporal",
        predictions=predictions,
        metrics=metrics,
        selected_regularization=selected,
        train_count=len(training_rows),
        test_count=len(test_rows),
        train_start=training_rows[0].start.isoformat().replace("+00:00", "Z"),
        train_end=max(row.end for row in training_rows)
        .isoformat()
        .replace("+00:00", "Z"),
        test_start=test_rows[0].start.isoformat().replace("+00:00", "Z"),
        test_end=max(row.end for row in test_rows).isoformat().replace("+00:00", "Z"),
    )


def evaluate_leave_one_group_out(
    rows: Sequence[NormalizedObservation],
    *,
    task: OutcomeTask,
    group: Literal["norad_id", "station_id"],
    regularization_grid: Sequence[float],
    minimum_test_rows: int = 1,
    temporal_test_fraction: float = 0.2,
    cutoff: datetime | None = None,
) -> tuple[PredictionRecord, ...]:
    eligible = task_rows(rows, task)
    if len(eligible) < 20:
        raise DatasetIntegrityError("leave-one-group-out evaluation requires 20 labeled rows")
    training_pool, test_pool, _split_cutoff = _time_separated_rows(
        eligible, temporal_test_fraction, cutoff=cutoff
    )
    groups: defaultdict[int, list[NormalizedObservation]] = defaultdict(list)
    for row in test_pool:
        groups[int(getattr(row, group))].append(row)
    predictions: list[PredictionRecord] = []
    split = "loso-satellite" if group == "norad_id" else "loso-station"
    for held_out, test in sorted(groups.items()):
        if len(test) < minimum_test_rows:
            continue
        training = [
            row
            for row in training_pool
            if int(getattr(row, group)) != held_out
        ]
        if len(training) < 10:
            continue
        fold_predictions, _ = evaluate_fold(
            training,
            test,
            task=task,
            split=split,
            fold=str(held_out),
            regularization_grid=regularization_grid,
        )
        predictions.extend(fold_predictions)
    return tuple(predictions)


def deterministic_group_holdout(
    values: Iterable[int], *, fraction: float, seed: int
) -> tuple[int, ...]:
    """Choose a frozen group split from identifiers only, never outcomes."""

    unique = sorted(set(int(value) for value in values))
    if not unique:
        return ()
    if not 0 < fraction < 0.5:
        raise ValueError("holdout fraction must be in (0, 0.5)")
    count = max(1, min(len(unique) - 1, round(len(unique) * fraction)))
    ranked = sorted(
        unique,
        key=lambda value: hashlib.sha256(f"{seed}:{value}".encode()).hexdigest(),
    )
    return tuple(sorted(ranked[:count]))


def evaluate_predeclared_group_holdout(
    rows: Sequence[NormalizedObservation],
    *,
    task: OutcomeTask,
    group: Literal["norad_id", "station_id"],
    held_out_values: Sequence[int],
    regularization_grid: Sequence[float],
    temporal_test_fraction: float = 0.2,
    minimum_test_rows: int = 10,
    minimum_test_groups: int = 1,
    cutoff: datetime | None = None,
) -> tuple[PredictionRecord, ...]:
    """Evaluate a single group-disjoint, future-only external test cohort."""

    eligible = task_rows(rows, task)
    if len(eligible) < 20:
        raise DatasetIntegrityError("external validation requires 20 labeled rows")
    held_out = {int(value) for value in held_out_values}
    if not held_out:
        raise DatasetIntegrityError("external validation requires held-out groups")
    if isinstance(minimum_test_groups, bool) or minimum_test_groups <= 0:
        raise ValueError("minimum_test_groups must be a positive integer")
    training_pool, test_pool, _split_cutoff = _time_separated_rows(
        eligible, temporal_test_fraction, cutoff=cutoff
    )
    training = [
        row
        for row in training_pool
        if int(getattr(row, group)) not in held_out
    ]
    test = [
        row
        for row in test_pool
        if int(getattr(row, group)) in held_out
    ]
    if len(training) < 10 or len(test) < minimum_test_rows:
        raise DatasetIntegrityError(
            "external validation split does not meet its predeclared row minimum"
        )
    evaluated_groups = {int(getattr(row, group)) for row in test}
    if len(evaluated_groups) < minimum_test_groups:
        raise DatasetIntegrityError(
            "external validation split does not meet its predeclared group minimum"
        )
    if {int(getattr(row, group)) for row in training} & {
        int(getattr(row, group)) for row in test
    }:
        raise AssertionError("external validation group leakage")
    split = "external-satellite" if group == "norad_id" else "external-station"
    predictions, _ = evaluate_fold(
        training,
        test,
        task=task,
        split=split,
        fold="predeclared-group-cohort",
        regularization_grid=regularization_grid,
    )
    return predictions


def _family_has_observations(
    rows: Sequence[NormalizedObservation], family: FeatureFamily
) -> bool:
    if family == "weather":
        names = (
            "weather_air_temperature_c",
            "weather_relative_humidity_percent",
            "weather_surface_pressure_kpa",
            "weather_wind_speed_m_s",
            "weather_precipitation_corrected",
            "space_weather_kp",
        )
    elif family == "hardware":
        names = (
            "antenna_type",
            "antenna_frequency_supported",
            "receive_antenna_gain_dbi",
            "system_noise_temperature_k",
        )
    else:
        return True
    return any(any(getattr(row, name) is not None for name in names) for row in rows)


def evaluate_covariate_ablation(
    rows: Sequence[NormalizedObservation],
    *,
    task: OutcomeTask,
    test_fraction: float,
    regularization_grid: Sequence[float],
    cutoff: datetime | None = None,
) -> CovariateAblationResult:
    """Quantify weather/hardware increments on one untouched temporal tail."""

    eligible = task_rows(rows, task)
    if len(eligible) < 20:
        raise DatasetIntegrityError("covariate ablation requires 20 labeled rows")
    training_rows, test_rows, split_cutoff = _time_separated_rows(
        eligible, test_fraction, cutoff=cutoff
    )
    training, test = fold_examples(training_rows, test_rows, task)
    candidates: tuple[tuple[str, FeatureFamily], ...] = (
        ("operational_logit", "operational"),
        ("operational_weather_logit", "weather"),
        ("operational_hardware_logit", "hardware"),
        ("full_covariate_logit", "full"),
    )
    availability = {
        "weather": _family_has_observations(training_rows, "weather")
        and _family_has_observations(test_rows, "weather"),
        "hardware": _family_has_observations(training_rows, "hardware")
        and _family_has_observations(test_rows, "hardware"),
    }
    predictions: list[PredictionRecord] = []
    selected: dict[str, float] = {}
    metrics: dict[str, ProbabilityMetrics] = {}
    unavailable: dict[str, str] = {}
    for model, family in candidates:
        required = (
            ("weather", "hardware")
            if family == "full"
            else (family,)
            if family in {"weather", "hardware"}
            else ()
        )
        missing = [name for name in required if not availability[name]]
        if missing:
            unavailable[model] = (
                "no non-missing " + " and ".join(missing) + " covariates in both folds"
            )
            continue
        regularization = _forward_regularization(
            training, family, regularization_grid, task
        )
        selected[model] = regularization
        probabilities = _fit_logit(training, family, regularization).predict(test)
        model_records = tuple(
            PredictionRecord(
                observation_id=example.row.observation_id,
                task=task,
                split="temporal-covariate-ablation",
                fold=(
                    "predeclared-final-panel"
                    if cutoff is not None
                    else "final-tail"
                ),
                model=model,
                outcome=example.outcome,
                probability=float(probability),
                norad_id=example.row.norad_id,
                station_id=example.row.station_id,
                start=example.row.start.isoformat().replace("+00:00", "Z"),
            )
            for example, probability in zip(test, probabilities, strict=True)
        )
        predictions.extend(model_records)
        metrics[model] = probability_metrics(
            [record.outcome for record in model_records],
            [record.probability for record in model_records],
        )
    return CovariateAblationResult(
        task=task,
        cutoff=split_cutoff.isoformat().replace("+00:00", "Z"),
        train_count=len(training_rows),
        test_count=len(test_rows),
        available_families=tuple(model for model, _ in candidates if model in metrics),
        unavailable_reasons=unavailable,
        selected_regularization=selected,
        predictions=tuple(predictions),
        metrics=metrics,
    )


def write_covariate_ablation_report(
    dataset_path: Path,
    output_dir: Path,
    *,
    test_fraction: float = 0.2,
    regularization_grid: Sequence[float] = (0.01, 0.1, 1.0, 10.0),
    bootstrap_replicates: int = 2_000,
    seed: int = 7538,
) -> Mapping[str, object]:
    """Write an explicitly exploratory weather/hardware increment analysis."""

    from .dataset import read_normalized_jsonl

    rows = read_normalized_jsonl(dataset_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "schema_version": "observation-planning-covariate-ablation-v1",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "dataset_path": str(dataset_path),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "analysis_status": "exploratory-not-preregistered-for-v3",
        "test_fraction": test_fraction,
        "regularization_grid": list(regularization_grid),
        "bootstrap_replicates": bootstrap_replicates,
        "tasks": {},
    }
    for task in ("signal_present", "decode_success_given_signal"):
        result = evaluate_covariate_ablation(
            rows,
            task=task,  # type: ignore[arg-type]
            test_fraction=test_fraction,
            regularization_grid=regularization_grid,
        )
        predictions_path = output_dir / f"{task}-predictions.jsonl"
        with predictions_path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in result.predictions:
                handle.write(
                    json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
        comparisons: dict[str, object] = {}
        for model in result.available_families:
            if model == "operational_logit":
                continue
            for cluster in ("norad_id", "station_id"):
                difference = paired_cluster_bootstrap_brier(
                    result.predictions,
                    model=model,
                    baseline="operational_logit",
                    cluster=cluster,  # type: ignore[arg-type]
                    replicates=bootstrap_replicates,
                    seed=seed,
                )
                comparisons[f"{model}_vs_operational_by_{cluster}"] = {
                    key: value
                    for key, value in asdict(difference).items()
                    if key != "replicates"
                }
        report["tasks"][task] = {  # type: ignore[index]
            "cutoff": result.cutoff,
            "train_count": result.train_count,
            "test_count": result.test_count,
            "available_families": list(result.available_families),
            "unavailable_reasons": dict(result.unavailable_reasons),
            "selected_regularization": dict(result.selected_regularization),
            "metrics": {
                name: metric.as_dict() for name, metric in result.metrics.items()
            },
            "paired_cluster_bootstrap": comparisons,
            "predictions_path": str(predictions_path),
            "predictions_sha256": hashlib.sha256(
                predictions_path.read_bytes()
            ).hexdigest(),
        }
    output_path = output_dir / "covariate-ablation.json"
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def paired_cluster_bootstrap_brier(
    predictions: Sequence[PredictionRecord],
    *,
    model: str,
    baseline: str = "global_rate",
    cluster: Literal["norad_id", "station_id"] = "norad_id",
    replicates: int = 2000,
    seed: int = 7538,
) -> BootstrapDifference:
    import numpy as np

    by_model = {
        (record.observation_id, record.fold): record
        for record in predictions
        if record.model == model
    }
    by_baseline = {
        (record.observation_id, record.fold): record
        for record in predictions
        if record.model == baseline
    }
    keys = sorted(set(by_model) & set(by_baseline))
    if not keys:
        raise DatasetIntegrityError("no aligned predictions for paired bootstrap")
    differences: defaultdict[int, list[float]] = defaultdict(list)
    model_errors: list[float] = []
    baseline_errors: list[float] = []
    for key in keys:
        candidate, reference = by_model[key], by_baseline[key]
        if candidate.outcome != reference.outcome:
            raise DatasetIntegrityError("paired predictions disagree on the outcome")
        candidate_error = (candidate.outcome - candidate.probability) ** 2
        reference_error = (reference.outcome - reference.probability) ** 2
        cluster_id = int(getattr(candidate, cluster))
        differences[cluster_id].append(candidate_error - reference_error)
        model_errors.append(candidate_error)
        baseline_errors.append(reference_error)
    cluster_ids = sorted(differences)
    rng = np.random.default_rng(seed)
    samples: list[float] = []
    # A cluster bootstrap repeats every row from a sampled cluster. Computing
    # each draw from per-cluster sums/counts is algebraically identical to
    # repeatedly materializing all row errors, but scales to the expanded v4
    # cohort without allocating O(replicates * rows) Python objects.
    cluster_sums = np.asarray(
        [sum(differences[cluster_id]) for cluster_id in cluster_ids], dtype=float
    )
    cluster_counts = np.asarray(
        [len(differences[cluster_id]) for cluster_id in cluster_ids], dtype=float
    )
    batch_size = max(1, min(256, replicates))
    for start in range(0, replicates, batch_size):
        batch = min(batch_size, replicates - start)
        selected = rng.integers(
            0, len(cluster_ids), size=(batch, len(cluster_ids))
        )
        draw_sums = cluster_sums[selected].sum(axis=1)
        draw_counts = cluster_counts[selected].sum(axis=1)
        samples.extend(float(value) for value in draw_sums / draw_counts)
    estimate = float(np.mean(model_errors) - np.mean(baseline_errors))
    lower, upper = np.quantile(samples, [0.025, 0.975])
    baseline_brier = float(np.mean(baseline_errors))
    return BootstrapDifference(
        model=model,
        baseline=baseline,
        aligned_prediction_count=len(keys),
        cluster_count=len(cluster_ids),
        estimate=estimate,
        lower_95=float(lower),
        upper_95=float(upper),
        relative_brier_reduction=(
            baseline_brier - float(np.mean(model_errors))
        )
        / max(baseline_brier, 1e-15),
        replicates=tuple(samples),
    )
