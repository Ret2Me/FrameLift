"""Fail-closed contract validation for user-supplied headerless raw IQ.

No field is inferred from a filename, satellite catalog, decoder default, or
file duration.  A recording becomes decode-ready only when its bytes, scalar
encoding, RF/time contract, Doppler state, satellite identity, and waveform
parameters are explicit and internally consistent.  Blind-test freezing is a
separate content-addressed record so references can remain sealed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from .canonical import canonical_json


SCHEMA_VERSION = "raw-iq-input-v1"
FREEZE_SCHEMA_VERSION = "raw-iq-blind-freeze-v1"
ALLOWED_INTERLEAVINGS = frozenset({"iq", "qi", "native_complex"})
ALLOWED_DOPPLER_STATES = frozenset(
    {"pre_correction", "post_correction", "unknown"}
)
ALLOWED_CLIPPING_ACTIONS = frozenset(
    {"require_frozen_ab", "block_decode"}
)

# dtype -> (struct scalar format, scalar bytes, numeric lower, numeric upper,
# kind, required interleaving)
DTYPES: dict[str, tuple[str, int, float | None, float | None, str, str | None]] = {
    "int8": ("b", 1, -128.0, 127.0, "integer", None),
    "uint8": ("B", 1, 0.0, 255.0, "integer", None),
    "int16_le": ("<h", 2, -32_768.0, 32_767.0, "integer", None),
    "int16_be": (">h", 2, -32_768.0, 32_767.0, "integer", None),
    "float32_le": ("<f", 4, None, None, "float", None),
    "float32_be": (">f", 4, None, None, "float", None),
    "complex64_le": ("<f", 4, None, None, "float", "native_complex"),
    "complex64_be": (">f", 4, None, None, "float", "native_complex"),
}

TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "source", "encoding", "capture", "doppler", "satellite", "signal", "clipping"}
)


@dataclass(frozen=True, slots=True)
class ClippingMetrics:
    scalar_count: int
    lower_endpoint_count: int
    upper_endpoint_count: int
    endpoint_fraction: float
    nonfinite_scalar_count: int
    status: str
    ab_window_sha256: str | None


@dataclass(frozen=True, slots=True)
class RawIQValidation:
    manifest_sha256: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    clipping: ClippingMetrics | None
    blind_freeze_blockers: tuple[str, ...]

    @property
    def ready_for_decode(self) -> bool:
        return not self.blockers

    @property
    def ready_for_blind_test(self) -> bool:
        return self.ready_for_decode and not self.blind_freeze_blockers

    def as_dict(self) -> dict[str, object]:
        return {
            "manifest_sha256": self.manifest_sha256,
            "ready_for_decode": self.ready_for_decode,
            "ready_for_blind_test": self.ready_for_blind_test,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "blind_freeze_blockers": list(self.blind_freeze_blockers),
            "clipping": (
                {
                    "scalar_count": self.clipping.scalar_count,
                    "lower_endpoint_count": self.clipping.lower_endpoint_count,
                    "upper_endpoint_count": self.clipping.upper_endpoint_count,
                    "endpoint_fraction": self.clipping.endpoint_fraction,
                    "nonfinite_scalar_count": self.clipping.nonfinite_scalar_count,
                    "status": self.clipping.status,
                    "ab_window_sha256": self.clipping.ab_window_sha256,
                }
                if self.clipping
                else None
            ),
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_range(path: Path, offset: int, count: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        handle.seek(offset)
        remaining = count
        while remaining:
            block = handle.read(min(remaining, 1024 * 1024))
            if not block:
                raise ValueError("short IQ range while hashing A/B window")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_number(value: object, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value):
        return False
    return value > 0 if positive else True


def _is_nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mapping(
    document: Mapping[str, object],
    key: str,
    blockers: list[str],
) -> Mapping[str, object]:
    value = document.get(key)
    if not isinstance(value, Mapping):
        blockers.append(f"missing_or_invalid_{key}")
        return {}
    return value


def _unexpected_keys(
    value: Mapping[str, object], expected: frozenset[str], prefix: str
) -> tuple[str, ...]:
    return tuple(
        f"unexpected_{prefix}_field:{key}" for key in sorted(set(value) - expected)
    )


def _utc_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _scan_endpoints(
    path: Path,
    *,
    offset: int,
    byte_count: int,
    scalar_format: str,
    scalar_bytes: int,
    lower: float,
    upper: float,
) -> tuple[int, int, int, int]:
    lower_count = 0
    upper_count = 0
    nonfinite = 0
    scalar_count = 0
    chunk_bytes = 1024 * 1024
    chunk_bytes -= chunk_bytes % scalar_bytes
    with path.open("rb") as handle:
        handle.seek(offset)
        remaining = byte_count
        while remaining:
            block = handle.read(min(remaining, chunk_bytes))
            if not block or len(block) % scalar_bytes:
                raise ValueError("short or scalar-misaligned IQ body")
            remaining -= len(block)
            for (sample,) in struct.iter_unpack(scalar_format, block):
                scalar_count += 1
                if isinstance(sample, float) and not math.isfinite(sample):
                    nonfinite += 1
                    continue
                if sample == lower:
                    lower_count += 1
                if sample == upper:
                    upper_count += 1
    return scalar_count, lower_count, upper_count, nonfinite


def manifest_sha256(document: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def validate_raw_iq_manifest(
    document: Mapping[str, object],
    *,
    base_dir: Path,
    blind_freeze: Mapping[str, object] | None = None,
    verify_file: bool = True,
) -> RawIQValidation:
    """Validate a raw-IQ contract without supplying any missing defaults."""

    digest = manifest_sha256(document)
    blockers: list[str] = []
    warnings: list[str] = []
    blockers.extend(_unexpected_keys(document, TOP_LEVEL_KEYS, "top_level"))
    if document.get("schema_version") != SCHEMA_VERSION:
        blockers.append("unsupported_schema_version")

    source = _mapping(document, "source", blockers)
    encoding = _mapping(document, "encoding", blockers)
    capture = _mapping(document, "capture", blockers)
    doppler = _mapping(document, "doppler", blockers)
    satellite = _mapping(document, "satellite", blockers)
    signal = _mapping(document, "signal", blockers)
    clipping = _mapping(document, "clipping", blockers)

    blockers.extend(
        _unexpected_keys(
            source, frozenset({"path", "sha256", "size_bytes"}), "source"
        )
    )
    source_path_value = source.get("path")
    source_path = (
        Path(source_path_value)
        if isinstance(source_path_value, str) and source_path_value
        else None
    )
    if source_path is None:
        blockers.append("missing_source_path")
    elif not source_path.is_absolute():
        source_path = base_dir / source_path
    source_sha = source.get("sha256")
    if not _is_sha256(source_sha):
        blockers.append("missing_or_invalid_source_sha256")
    size_bytes = source.get("size_bytes")
    if not _is_positive_integer(size_bytes):
        blockers.append("missing_or_invalid_source_size_bytes")

    blockers.extend(
        _unexpected_keys(
            encoding,
            frozenset(
                {
                    "dtype",
                    "interleaving",
                    "q_sign",
                    "scalar_zero",
                    "iq_scale",
                    "byte_offset",
                    "complex_sample_count",
                    "evidence",
                }
            ),
            "encoding",
        )
    )
    dtype = encoding.get("dtype")
    dtype_contract = DTYPES.get(dtype) if isinstance(dtype, str) else None
    if dtype_contract is None:
        blockers.append("missing_or_unsupported_dtype")
    interleaving = encoding.get("interleaving")
    if interleaving not in ALLOWED_INTERLEAVINGS:
        blockers.append("missing_or_invalid_interleaving")
    if dtype_contract and dtype_contract[5] and interleaving != dtype_contract[5]:
        blockers.append("dtype_interleaving_mismatch")
    if dtype_contract and not dtype_contract[5] and interleaving == "native_complex":
        blockers.append("dtype_interleaving_mismatch")
    if encoding.get("q_sign") not in (-1, 1):
        blockers.append("missing_or_invalid_q_sign")
    if not _is_number(encoding.get("scalar_zero")):
        blockers.append("missing_or_invalid_scalar_zero")
    if not _is_number(encoding.get("iq_scale"), positive=True):
        blockers.append("missing_or_invalid_iq_scale")
    byte_offset = encoding.get("byte_offset")
    if not _is_nonnegative_integer(byte_offset):
        blockers.append("missing_or_invalid_byte_offset")
    complex_samples = encoding.get("complex_sample_count")
    if not _is_positive_integer(complex_samples):
        blockers.append("missing_or_invalid_complex_sample_count")
    if not _text(encoding.get("evidence")):
        blockers.append("missing_encoding_evidence")

    blockers.extend(
        _unexpected_keys(
            capture,
            frozenset(
                {
                    "sample_rate_hz",
                    "center_frequency_hz",
                    "start_utc",
                    "clock_reference",
                    "evidence",
                }
            ),
            "capture",
        )
    )
    for key in ("sample_rate_hz", "center_frequency_hz"):
        if not _is_number(capture.get(key), positive=True):
            blockers.append(f"missing_or_invalid_{key}")
    if not _utc_timestamp(capture.get("start_utc")):
        blockers.append("missing_or_invalid_start_utc")
    if not _text(capture.get("clock_reference")):
        blockers.append("missing_clock_reference")
    if not _text(capture.get("evidence")):
        blockers.append("missing_capture_evidence")

    blockers.extend(
        _unexpected_keys(doppler, frozenset({"state", "evidence"}), "doppler")
    )
    doppler_state = doppler.get("state")
    if doppler_state not in ALLOWED_DOPPLER_STATES:
        blockers.append("missing_or_invalid_doppler_state")
    elif doppler_state == "unknown":
        blockers.append("unknown_doppler_state")
    if not _text(doppler.get("evidence")):
        blockers.append("missing_doppler_evidence")

    blockers.extend(
        _unexpected_keys(
            satellite, frozenset({"norad_id", "evidence"}), "satellite"
        )
    )
    if not _is_positive_integer(satellite.get("norad_id")):
        blockers.append("missing_or_invalid_norad_id")
    if not _text(satellite.get("evidence")):
        blockers.append("missing_satellite_evidence")

    blockers.extend(
        _unexpected_keys(
            signal, frozenset({"modulation", "baud", "evidence"}), "signal"
        )
    )
    modulation = signal.get("modulation")
    if not _text(modulation) or str(modulation).strip().lower() == "unknown":
        blockers.append("missing_or_unknown_modulation")
    if not _is_number(signal.get("baud"), positive=True):
        blockers.append("missing_or_invalid_baud")
    if not _text(signal.get("evidence")):
        blockers.append("missing_signal_evidence")

    blockers.extend(
        _unexpected_keys(
            clipping,
            frozenset(
                {
                    "lower_endpoint",
                    "upper_endpoint",
                    "endpoint_source",
                    "max_endpoint_fraction_for_unclipped_claim",
                    "if_exceeded",
                    "ab",
                }
            ),
            "clipping",
        )
    )
    lower_endpoint = clipping.get("lower_endpoint")
    upper_endpoint = clipping.get("upper_endpoint")
    if not _is_number(lower_endpoint) or not _is_number(upper_endpoint):
        blockers.append("missing_or_invalid_clipping_endpoints")
    elif lower_endpoint >= upper_endpoint:
        blockers.append("invalid_clipping_endpoint_order")
    if not _text(clipping.get("endpoint_source")):
        blockers.append("missing_clipping_endpoint_source")
    limit = clipping.get("max_endpoint_fraction_for_unclipped_claim")
    if not _is_number(limit) or not 0 <= float(limit) <= 1:
        blockers.append("missing_or_invalid_clipping_fraction_limit")
    action = clipping.get("if_exceeded")
    if action not in ALLOWED_CLIPPING_ACTIONS:
        blockers.append("missing_or_invalid_clipping_action")
    ab = clipping.get("ab")
    if not isinstance(ab, Mapping):
        blockers.append("missing_or_invalid_ab_contract")
        ab = {}
    enabled = ab.get("enabled")
    if not isinstance(enabled, bool):
        blockers.append("missing_or_invalid_ab_enabled")
        enabled = False
    ab_window_sha: str | None = None
    if enabled:
        expected_ab_keys = frozenset(
            {
                "enabled",
                "input_sha256_a",
                "input_sha256_b",
                "window_start_complex_sample",
                "window_complex_samples",
                "window_sha256_a",
                "window_sha256_b",
                "branch_a",
                "branch_b",
                "branch_b_config_sha256",
                "shared_downstream_config_sha256",
                "acceptance_rule",
            }
        )
        blockers.extend(_unexpected_keys(ab, expected_ab_keys, "ab"))
        if ab.get("input_sha256_a") != source_sha or ab.get("input_sha256_b") != source_sha:
            blockers.append("ab_inputs_not_identical_to_source")
        if ab.get("branch_a") != "identity":
            blockers.append("ab_branch_a_must_be_identity")
        if not _text(ab.get("branch_b")) or ab.get("branch_b") == "identity":
            blockers.append("missing_or_invalid_ab_branch_b")
        for key in (
            "branch_b_config_sha256",
            "shared_downstream_config_sha256",
            "window_sha256_a",
            "window_sha256_b",
        ):
            if not _is_sha256(ab.get(key)):
                blockers.append(f"missing_or_invalid_ab_{key}")
        if not _text(ab.get("acceptance_rule")):
            blockers.append("missing_ab_acceptance_rule")
        if not _is_nonnegative_integer(ab.get("window_start_complex_sample")):
            blockers.append("missing_or_invalid_ab_window_start")
        if not _is_positive_integer(ab.get("window_complex_samples")):
            blockers.append("missing_or_invalid_ab_window_length")
        if (
            _is_nonnegative_integer(ab.get("window_start_complex_sample"))
            and _is_positive_integer(ab.get("window_complex_samples"))
            and _is_positive_integer(complex_samples)
            and ab["window_start_complex_sample"] + ab["window_complex_samples"]
            > complex_samples
        ):
            blockers.append("ab_window_outside_source")
    else:
        blockers.extend(_unexpected_keys(ab, frozenset({"enabled"}), "ab"))

    metrics: ClippingMetrics | None = None
    shape_valid = (
        source_path is not None
        and dtype_contract is not None
        and _is_nonnegative_integer(byte_offset)
        and _is_positive_integer(complex_samples)
        and _is_positive_integer(size_bytes)
    )
    if verify_file and source_path is not None:
        if not source_path.is_file():
            blockers.append("source_file_missing")
        elif shape_valid:
            scalar_format, scalar_bytes, dtype_lower, dtype_upper, kind, _ = dtype_contract
            expected_size = byte_offset + complex_samples * 2 * scalar_bytes
            if source_path.stat().st_size != size_bytes:
                blockers.append("source_size_mismatch")
            if size_bytes != expected_size:
                blockers.append("source_shape_size_mismatch")
            if _is_sha256(source_sha) and _sha256_file(source_path) != source_sha:
                blockers.append("source_sha256_mismatch")
            if kind == "integer" and (
                lower_endpoint != dtype_lower
                or upper_endpoint != dtype_upper
                or clipping.get("endpoint_source") != "dtype_limits"
            ):
                blockers.append("integer_clipping_endpoints_must_match_dtype_limits")
            endpoints_valid = (
                _is_number(lower_endpoint)
                and _is_number(upper_endpoint)
                and lower_endpoint < upper_endpoint
            )
            if size_bytes == expected_size and endpoints_valid:
                scalar_count, lower_count, upper_count, nonfinite = _scan_endpoints(
                    source_path,
                    offset=byte_offset,
                    byte_count=complex_samples * 2 * scalar_bytes,
                    scalar_format=scalar_format,
                    scalar_bytes=scalar_bytes,
                    lower=float(lower_endpoint),
                    upper=float(upper_endpoint),
                )
                endpoint_fraction = (lower_count + upper_count) / scalar_count
                status = "within_declared_limit"
                if nonfinite:
                    blockers.append("nonfinite_iq_scalars")
                if _is_number(limit) and endpoint_fraction > float(limit):
                    status = "exceeds_declared_limit"
                    if action == "block_decode":
                        blockers.append("clipping_limit_exceeded")
                    elif action == "require_frozen_ab" and not enabled:
                        blockers.append("clipping_requires_enabled_ab")
                if enabled and _is_nonnegative_integer(ab.get("window_start_complex_sample")) and _is_positive_integer(ab.get("window_complex_samples")):
                    window_offset = byte_offset + ab["window_start_complex_sample"] * 2 * scalar_bytes
                    window_bytes = ab["window_complex_samples"] * 2 * scalar_bytes
                    if window_offset + window_bytes <= expected_size:
                        ab_window_sha = _sha256_range(
                            source_path, window_offset, window_bytes
                        )
                        if (
                            ab.get("window_sha256_a") != ab_window_sha
                            or ab.get("window_sha256_b") != ab_window_sha
                        ):
                            blockers.append("ab_window_hash_mismatch")
                metrics = ClippingMetrics(
                    scalar_count=scalar_count,
                    lower_endpoint_count=lower_count,
                    upper_endpoint_count=upper_count,
                    endpoint_fraction=endpoint_fraction,
                    nonfinite_scalar_count=nonfinite,
                    status=status,
                    ab_window_sha256=ab_window_sha,
                )
    elif not verify_file:
        blockers.append("file_bytes_not_verified")
        warnings.append("structural_validation_only")

    freeze_blockers = validate_blind_freeze(document, blind_freeze)
    return RawIQValidation(
        manifest_sha256=digest,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        clipping=metrics,
        blind_freeze_blockers=freeze_blockers,
    )


def validate_blind_freeze(
    manifest: Mapping[str, object], freeze: Mapping[str, object] | None
) -> tuple[str, ...]:
    """Validate a sealed preregistration record against an input manifest."""

    if freeze is None:
        return ("blind_freeze_missing",)
    expected_keys = frozenset(
        {
            "schema_version",
            "frozen_at_utc",
            "input_manifest_sha256",
            "input_file_sha256",
            "candidate_config_sha256",
            "code_sha256",
            "parameter_grid_sha256",
            "reference_commitment_sha256",
            "reference_state",
            "reference_bytes_available_to_decoder",
            "position_blind",
            "acceptance_rule",
            "negative_control_rule",
            "deterministic_repeats",
        }
    )
    blockers = list(_unexpected_keys(freeze, expected_keys, "blind_freeze"))
    if freeze.get("schema_version") != FREEZE_SCHEMA_VERSION:
        blockers.append("unsupported_blind_freeze_schema")
    if not _utc_timestamp(freeze.get("frozen_at_utc")):
        blockers.append("missing_or_invalid_blind_freeze_time")
    if freeze.get("input_manifest_sha256") != manifest_sha256(manifest):
        blockers.append("blind_freeze_manifest_hash_mismatch")
    source = manifest.get("source")
    source_sha = source.get("sha256") if isinstance(source, Mapping) else None
    if freeze.get("input_file_sha256") != source_sha or not _is_sha256(source_sha):
        blockers.append("blind_freeze_input_hash_mismatch")
    for key in (
        "candidate_config_sha256",
        "code_sha256",
        "parameter_grid_sha256",
        "reference_commitment_sha256",
    ):
        if not _is_sha256(freeze.get(key)):
            blockers.append(f"missing_or_invalid_{key}")
    if freeze.get("reference_state") != "sealed":
        blockers.append("reference_not_sealed_at_freeze")
    if freeze.get("reference_bytes_available_to_decoder") is not False:
        blockers.append("reference_bytes_must_be_unavailable_to_decoder")
    if freeze.get("position_blind") is not True:
        blockers.append("blind_test_must_be_position_blind")
    if not _text(freeze.get("acceptance_rule")):
        blockers.append("missing_blind_acceptance_rule")
    if not _text(freeze.get("negative_control_rule")):
        blockers.append("missing_negative_control_rule")
    repeats = freeze.get("deterministic_repeats")
    if not _is_positive_integer(repeats) or repeats < 2:
        blockers.append("deterministic_repeats_must_be_at_least_two")
    return tuple(dict.fromkeys(blockers))


def build_blind_freeze(
    manifest: Mapping[str, object],
    *,
    frozen_at_utc: str,
    candidate_config_sha256: str,
    code_sha256: str,
    parameter_grid_sha256: str,
    reference_commitment_sha256: str,
    acceptance_rule: str,
    negative_control_rule: str,
    deterministic_repeats: int = 2,
) -> dict[str, object]:
    """Build a sealed freeze record; callers should write it immutably."""

    source = manifest.get("source")
    source_sha = source.get("sha256") if isinstance(source, Mapping) else None
    freeze = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "frozen_at_utc": frozen_at_utc,
        "input_manifest_sha256": manifest_sha256(manifest),
        "input_file_sha256": source_sha,
        "candidate_config_sha256": candidate_config_sha256,
        "code_sha256": code_sha256,
        "parameter_grid_sha256": parameter_grid_sha256,
        "reference_commitment_sha256": reference_commitment_sha256,
        "reference_state": "sealed",
        "reference_bytes_available_to_decoder": False,
        "position_blind": True,
        "acceptance_rule": acceptance_rule,
        "negative_control_rule": negative_control_rule,
        "deterministic_repeats": deterministic_repeats,
    }
    blockers = validate_blind_freeze(manifest, freeze)
    if blockers:
        raise ValueError("invalid blind freeze: " + ", ".join(blockers))
    return freeze


def write_immutable_blind_freeze(
    path: Path,
    freeze: Mapping[str, object],
    *,
    manifest: Mapping[str, object],
) -> str:
    """Write one canonical freeze record, rejecting a different rewrite."""

    blockers = validate_blind_freeze(manifest, freeze)
    if blockers:
        raise ValueError("invalid blind freeze: " + ", ".join(blockers))
    payload = (canonical_json(freeze) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(
                f"immutable blind freeze already exists with different content: {path}"
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail-closed validation of a user-supplied raw-IQ manifest"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--blind-freeze", type=Path)
    parser.add_argument(
        "--no-file-verification",
        action="store_true",
        help="validate structure only; result cannot establish verified bytes",
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if not isinstance(manifest, Mapping):
        raise SystemExit("manifest root must be a JSON object")
    freeze = None
    if args.blind_freeze:
        freeze = json.loads(args.blind_freeze.read_text())
        if not isinstance(freeze, Mapping):
            raise SystemExit("blind freeze root must be a JSON object")
    result = validate_raw_iq_manifest(
        manifest,
        base_dir=args.manifest.resolve().parent,
        blind_freeze=freeze,
        verify_file=not args.no_file_verification,
    )
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    ready = result.ready_for_blind_test if args.blind_freeze else result.ready_for_decode
    raise SystemExit(0 if ready else 2)


if __name__ == "__main__":
    main()
