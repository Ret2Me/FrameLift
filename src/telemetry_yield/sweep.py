"""Deterministic, lazy planning for bounded decoder parameter sweeps."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from telemetry_yield.canonical import canonical_json, config_hash
from telemetry_yield.models import JsonValue


L1_AXIS_ORDER = (
    "baud_rel_error",
    "cfo_hz",
    "bit_polarity",
    "decoder",
)

PARTIAL_SIGNAL_METRICS = (
    "n_syncwords_detected",
    "longest_clean_run_bits",
    "timing_err_variance",
    "preamble_corr_peak",
)


class SweepPlanError(ValueError):
    """Raised when a sweep definition or bounded plan is invalid."""


class SweepBudgetExceeded(SweepPlanError):
    """Raised before iteration when an estimated plan exceeds its budget."""

    def __init__(self, violations: Sequence[str], estimate: PlanEstimate) -> None:
        self.violations = tuple(violations)
        self.estimate = estimate
        super().__init__("sweep budget exceeded: " + ", ".join(self.violations))


def _copy_json(value: Any) -> JsonValue:
    return json.loads(canonical_json(value))


def _decimal(value: Any, *, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SweepPlanError(f"{name} must be numeric")
    return Decimal(str(value))


def _range_parts(
    definition: Mapping[str, Any],
    *,
    resolution: str,
) -> tuple[Decimal, Decimal, Decimal, int]:
    bounds = definition.get("range")
    if (
        not isinstance(bounds, Sequence)
        or isinstance(bounds, (str, bytes, bytearray))
        or len(bounds) != 2
    ):
        raise SweepPlanError("range axis must contain two bounds")
    if resolution not in {"coarse", "fine"}:
        raise SweepPlanError("resolution must be 'coarse' or 'fine'")
    step_key = f"{resolution}_step"
    if step_key not in definition and resolution == "fine":
        step_key = "coarse_step"
    if step_key not in definition:
        raise SweepPlanError(f"range axis is missing {step_key}")
    start = _decimal(bounds[0], name="range start")
    stop = _decimal(bounds[1], name="range stop")
    step = _decimal(definition[step_key], name=step_key)
    if stop < start:
        raise SweepPlanError("range stop must not precede start")
    if step <= 0:
        raise SweepPlanError("range step must be positive")
    quotient, remainder = divmod(stop - start, step)
    if remainder != 0:
        raise SweepPlanError("range span must be divisible by its step")
    return start, stop, step, int(quotient) + 1


def _native_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


@dataclass(frozen=True, slots=True)
class SweepAxis:
    """One preserved sweep axis, either explicit or an inclusive numeric range."""

    name: str
    definition: JsonValue

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise SweepPlanError("axis name must be a non-empty string")
        if isinstance(self.definition, list):
            if not self.definition:
                raise SweepPlanError(f"axis {self.name} must not be empty")
            values = [canonical_json(value) for value in self.definition]
            if len(values) != len(set(values)):
                raise SweepPlanError(f"axis {self.name} contains duplicate values")
        elif isinstance(self.definition, dict):
            _range_parts(self.definition, resolution="coarse")
            if "fine_step" in self.definition:
                _range_parts(self.definition, resolution="fine")
        else:
            raise SweepPlanError(
                f"axis {self.name} must be a list or range definition"
            )

    def cardinality(self, *, resolution: str = "coarse") -> int:
        if isinstance(self.definition, list):
            return len(self.definition)
        if not isinstance(self.definition, dict):
            raise AssertionError("validated range definition changed type")
        return _range_parts(self.definition, resolution=resolution)[3]

    def iter_values(self, *, resolution: str = "coarse") -> Iterator[JsonValue]:
        """Yield values without constructing a range or Cartesian product."""
        if isinstance(self.definition, list):
            for value in self.definition:
                yield _copy_json(value)
            return
        if not isinstance(self.definition, dict):
            raise AssertionError("validated range definition changed type")
        start, _, step, count = _range_parts(
            self.definition,
            resolution=resolution,
        )
        for index in range(count):
            yield _native_number(start + index * step)

    def contains(self, value: JsonValue, *, resolution: str = "coarse") -> bool:
        target = canonical_json(value)
        return any(
            canonical_json(candidate) == target
            for candidate in self.iter_values(resolution=resolution)
        )


@dataclass(frozen=True, slots=True)
class ProtocolSweep:
    """Validated protocol definition with every configured axis preserved."""

    protocol_id: str
    nominal: Mapping[str, JsonValue]
    axes: tuple[SweepAxis, ...]

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> ProtocolSweep:
        protocol_id = document.get("protocol_id")
        nominal = document.get("nominal")
        sweep = document.get("sweep")
        if not isinstance(protocol_id, str) or not protocol_id.strip():
            raise SweepPlanError("protocol_id must be a non-empty string")
        if not isinstance(nominal, Mapping):
            raise SweepPlanError("nominal must be a mapping")
        if not isinstance(sweep, Mapping) or not sweep:
            raise SweepPlanError("sweep must be a non-empty mapping")
        copied_nominal = _copy_json(nominal)
        if not isinstance(copied_nominal, dict):
            raise AssertionError("canonical nominal mapping changed type")
        axes = tuple(
            SweepAxis(str(name), _copy_json(definition))
            for name, definition in sorted(sweep.items())
        )
        return cls(protocol_id, copied_nominal, axes)

    def axis(self, name: str) -> SweepAxis | None:
        return next((axis for axis in self.axes if axis.name == name), None)

    @property
    def l1_axes(self) -> tuple[SweepAxis, ...]:
        by_name = {axis.name: axis for axis in self.axes}
        return tuple(by_name[name] for name in L1_AXIS_ORDER if name in by_name)

    def axis_cardinalities(self, *, resolution: str = "coarse") -> dict[str, int]:
        return {
            axis.name: axis.cardinality(resolution=resolution)
            for axis in self.axes
        }


def load_protocol_sweep(path: Path) -> ProtocolSweep:
    """Load one local YAML protocol definition."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - analysis extra is installed
        raise SweepPlanError("PyYAML is required to load protocol YAML") from exc
    class JsonSafeLoader(yaml.SafeLoader):
        """Use YAML 1.2 boolean words so ``off`` remains the AGC string."""

    JsonSafeLoader.yaml_implicit_resolvers = {
        key: [
            (tag, pattern)
            for tag, pattern in resolvers
            if tag != "tag:yaml.org,2002:bool"
        ]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    JsonSafeLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|false)$", re.IGNORECASE),
        list("tTfF"),
    )
    document = yaml.load(
        path.read_text(encoding="utf-8"),
        Loader=JsonSafeLoader,
    )
    if not isinstance(document, Mapping):
        raise SweepPlanError("protocol YAML must contain a mapping")
    return ProtocolSweep.from_mapping(document)


def l0_l1_attempt_count(protocol: ProtocolSweep) -> int:
    """Count one L0 and every single-axis L1 variation, excluding baselines."""
    return 1 + sum(axis.cardinality() - 1 for axis in protocol.l1_axes)


def cartesian_count(
    protocol: ProtocolSweep,
    *,
    resolution: str = "coarse",
) -> int:
    """Count a full grid arithmetically; never materialize the product."""
    total = 1
    for axis in protocol.axes:
        total *= axis.cardinality(resolution=resolution)
    return total


def l2_pairwise_upper_bound(protocol: ProtocolSweep) -> int:
    """Count all L1-axis pairs without selecting or materializing any pair grid."""
    variants = [axis.cardinality() - 1 for axis in protocol.l1_axes]
    return sum(
        variants[left] * variants[right]
        for left in range(len(variants))
        for right in range(left + 1, len(variants))
    )


@dataclass(frozen=True, slots=True)
class AttemptCost:
    cpu_seconds: float
    disk_bytes: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.cpu_seconds, bool)
            or not isinstance(self.cpu_seconds, (int, float))
            or not math.isfinite(self.cpu_seconds)
            or self.cpu_seconds < 0
        ):
            raise SweepPlanError("cpu_seconds must not be negative")
        if (
            isinstance(self.disk_bytes, bool)
            or not isinstance(self.disk_bytes, int)
            or self.disk_bytes < 0
        ):
            raise SweepPlanError("disk_bytes must not be negative")


@dataclass(frozen=True, slots=True)
class SweepBudget:
    max_attempts: int
    max_cpu_seconds: float
    max_disk_bytes: int

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts <= 0:
            raise SweepPlanError("max_attempts must be positive")
        if (
            isinstance(self.max_cpu_seconds, bool)
            or not isinstance(self.max_cpu_seconds, (int, float))
            or not math.isfinite(self.max_cpu_seconds)
            or self.max_cpu_seconds <= 0
        ):
            raise SweepPlanError("max_cpu_seconds must be positive")
        if (
            isinstance(self.max_disk_bytes, bool)
            or not isinstance(self.max_disk_bytes, int)
            or self.max_disk_bytes <= 0
        ):
            raise SweepPlanError("max_disk_bytes must be positive")


@dataclass(frozen=True, slots=True)
class PlanEstimate:
    attempts: int
    cpu_seconds: float
    disk_bytes: int
    protocol_attempts: Mapping[str, int]

    def violations(self, budget: SweepBudget) -> tuple[str, ...]:
        failures: list[str] = []
        if self.attempts > budget.max_attempts:
            failures.append("attempts")
        if self.cpu_seconds > budget.max_cpu_seconds:
            failures.append("cpu_seconds")
        if self.disk_bytes > budget.max_disk_bytes:
            failures.append("disk_bytes")
        return tuple(failures)

    def require_fits(self, budget: SweepBudget) -> None:
        failures = self.violations(budget)
        if failures:
            raise SweepBudgetExceeded(failures, self)


def estimate_l0_l1(
    protocols: Sequence[ProtocolSweep],
    *,
    recording_count: int,
    attempt_cost: AttemptCost,
) -> PlanEstimate:
    """Estimate a bounded L0/L1 plan before yielding any attempt."""
    if isinstance(recording_count, bool) or recording_count <= 0:
        raise SweepPlanError("recording_count must be positive")
    protocol_attempts = {
        protocol.protocol_id: l0_l1_attempt_count(protocol) * recording_count
        for protocol in protocols
    }
    if len(protocol_attempts) != len(protocols):
        raise SweepPlanError("protocol_id values must be unique")
    attempts = sum(protocol_attempts.values())
    return PlanEstimate(
        attempts=attempts,
        cpu_seconds=attempts * attempt_cost.cpu_seconds,
        disk_bytes=attempts * attempt_cost.disk_bytes,
        protocol_attempts=protocol_attempts,
    )


@dataclass(frozen=True, slots=True)
class SweepAttempt:
    recording_id: str
    protocol_id: str
    cascade_level: int
    varied_axis: str | None
    decoder: str
    decoder_version: str
    seed: int
    config: Mapping[str, JsonValue]
    config_hash: str


def _effective_attempt(
    *,
    recording_id: str,
    protocol: ProtocolSweep,
    cascade_level: int,
    varied_axis: str | None,
    parameters: Mapping[str, JsonValue],
    decoder_versions: Mapping[str, str],
    seed: int,
) -> SweepAttempt:
    decoder = parameters.get("decoder")
    if not isinstance(decoder, str) or not decoder.strip():
        raise SweepPlanError("live_config.decoder must be a non-empty string")
    decoder_version = decoder_versions.get(decoder)
    if not isinstance(decoder_version, str) or not decoder_version.strip():
        raise SweepPlanError(f"decoder version is missing for {decoder}")
    copied = _copy_json(parameters)
    if not isinstance(copied, dict):
        raise AssertionError("canonical effective config changed type")
    fingerprint = config_hash(
        protocol_id=protocol.protocol_id,
        decoder=decoder,
        decoder_version=decoder_version,
        seed=seed,
        config=copied,
    )
    return SweepAttempt(
        recording_id=recording_id,
        protocol_id=protocol.protocol_id,
        cascade_level=cascade_level,
        varied_axis=varied_axis,
        decoder=decoder,
        decoder_version=decoder_version,
        seed=seed,
        config=copied,
        config_hash=fingerprint,
    )


def iter_bounded_l0_l1(
    *,
    protocol: ProtocolSweep,
    recording_id: str,
    live_config: Mapping[str, JsonValue],
    decoder_versions: Mapping[str, str],
    seed: int,
    max_level: int,
    budget: SweepBudget,
    attempt_cost: AttemptCost,
) -> Iterator[SweepAttempt]:
    """Yield L0/L1 attempts lazily after a fail-closed budget check.

    L2 needs observed partial-signal rankings and L3 needs an L2 winner. They
    are intentionally rejected here instead of being approximated by a large
    Cartesian product.
    """
    if max_level not in {0, 1}:
        raise SweepPlanError("only explicit L0/L1 planning is supported")
    if not isinstance(recording_id, str) or not recording_id.strip():
        raise SweepPlanError("recording_id must be a non-empty string")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SweepPlanError("seed must be an integer")
    if not isinstance(live_config, Mapping):
        raise SweepPlanError("live_config must be a mapping")

    level_count = 1 if max_level == 0 else l0_l1_attempt_count(protocol)
    estimate = PlanEstimate(
        attempts=level_count,
        cpu_seconds=level_count * attempt_cost.cpu_seconds,
        disk_bytes=level_count * attempt_cost.disk_bytes,
        protocol_attempts={protocol.protocol_id: level_count},
    )
    estimate.require_fits(budget)

    parameters = dict(protocol.nominal)
    parameters.update(live_config)
    for axis in protocol.axes:
        if axis.name not in live_config:
            raise SweepPlanError(f"live_config is missing configured axis {axis.name}")
        if not axis.contains(live_config[axis.name]):
            raise SweepPlanError(
                f"live_config value is outside axis {axis.name}"
            )

    yield _effective_attempt(
        recording_id=recording_id,
        protocol=protocol,
        cascade_level=0,
        varied_axis=None,
        parameters=parameters,
        decoder_versions=decoder_versions,
        seed=seed,
    )
    if max_level == 0:
        return

    for axis in protocol.l1_axes:
        baseline = canonical_json(live_config[axis.name])
        for value in axis.iter_values():
            if canonical_json(value) == baseline:
                continue
            varied = dict(parameters)
            varied[axis.name] = value
            yield _effective_attempt(
                recording_id=recording_id,
                protocol=protocol,
                cascade_level=1,
                varied_axis=axis.name,
                parameters=varied,
                decoder_versions=decoder_versions,
                seed=seed,
            )
