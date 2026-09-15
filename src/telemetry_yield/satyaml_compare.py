"""Reproducible SatNOGS DB to gr-satellites SatYAML comparison.

The SatYAML reader intentionally uses only the standard library.  It parses the
small, stable metadata subset used by gr-satellites instead of pretending to be
a general YAML implementation.  Every supported SatYAML transmitter must have
the five scalar fields consumed here; an incomplete record fails closed.
"""

from __future__ import annotations

import json
import math
import re
import tarfile
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SATYAML_MEMBER_PATTERN = re.compile(r"/python/satyaml/[^/]+\.yml$")
SATYAML_MAX_MEMBER_BYTES = 1_000_000
FREQUENCY_TOLERANCE_HZ = 1_000.0
DESCRIPTION_MIN_SCORE = 0.25
DESCRIPTION_MIN_MARGIN = 0.08


@dataclass(frozen=True)
class SatYamlTransmitter:
    source_file: str
    satellite_name: str
    norad_id: int
    description: str
    frequency_hz: float
    modulation: str
    baudrate: float
    framing: str
    scrambler: str | None


@dataclass(frozen=True)
class SatnogsTransmitter:
    uuid: str
    norad_id: int
    description: str
    mode: str | None
    baud: float | None
    downlink_low_hz: float | None
    downlink_high_hz: float | None
    framing: str | None
    scrambler: str | None
    status: str | None
    updated: str | None


@dataclass(frozen=True)
class TransmitterMatch:
    satyaml: SatYamlTransmitter
    satnogs: SatnogsTransmitter | None
    status: str
    description_score: float | None
    equivalent_candidates: int = 0


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            continue
        if character == "#" and quote is None:
            return value[:index].rstrip()
    return value.strip()


def _yaml_scalar(value: str) -> str:
    value = _strip_inline_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        if value[0] == "'":
            return value[1:-1].replace("''", "'")
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid quoted YAML scalar: {value!r}") from exc
        if not isinstance(decoded, str):
            raise ValueError(f"quoted YAML scalar is not text: {value!r}")
        return decoded
    return value


def _number(value: Any, *, field: str, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric, not boolean")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def parse_satyaml_text(text: str, *, source_file: str) -> list[SatYamlTransmitter]:
    """Parse the metadata subset common to all current SatYAML definitions."""

    satellite_name: str | None = None
    norad_id: int | None = None
    in_transmitters = False
    transmitter_indent: int | None = None
    transmitter_name: str | None = None
    transmitter_values: dict[str, str] = {}
    parsed: list[tuple[str, dict[str, str]]] = []

    def finish_transmitter() -> None:
        nonlocal transmitter_name, transmitter_values
        if transmitter_name is not None:
            parsed.append((transmitter_name, transmitter_values))
        transmitter_name = None
        transmitter_values = {}

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        leading = raw_line[: len(raw_line) - len(raw_line.lstrip())]
        if "\t" in leading:
            raise ValueError(f"{source_file}:{line_number}: tabs are unsupported")
        indent = len(leading)
        stripped = raw_line.strip()

        if indent == 0:
            if in_transmitters:
                finish_transmitter()
                in_transmitters = False
            if stripped.startswith("name:"):
                satellite_name = _yaml_scalar(stripped.split(":", 1)[1])
            elif stripped.startswith("norad:"):
                raw_norad = _yaml_scalar(stripped.split(":", 1)[1])
                try:
                    norad_id = int(raw_norad)
                except ValueError as exc:
                    raise ValueError(
                        f"{source_file}:{line_number}: invalid NORAD ID"
                    ) from exc
            elif stripped == "transmitters:":
                in_transmitters = True
                transmitter_indent = None
            continue

        if not in_transmitters:
            continue
        if transmitter_indent is None and stripped.endswith(":"):
            transmitter_indent = indent
        if indent == transmitter_indent and stripped.endswith(":"):
            finish_transmitter()
            transmitter_name = _yaml_scalar(stripped[:-1])
            continue
        if (
            transmitter_indent is not None
            and indent > transmitter_indent
            and transmitter_name is not None
            and ":" in stripped
        ):
            key, value = stripped.split(":", 1)
            if key in {"frequency", "modulation", "baudrate", "framing", "scrambler"}:
                transmitter_values[key] = _yaml_scalar(value)

    if in_transmitters:
        finish_transmitter()
    if not satellite_name or norad_id is None:
        raise ValueError(f"{source_file}: missing satellite name or NORAD ID")
    if not parsed:
        raise ValueError(f"{source_file}: no transmitters found")

    result: list[SatYamlTransmitter] = []
    required = {"frequency", "modulation", "baudrate", "framing"}
    for description, values in parsed:
        missing = sorted(required - values.keys())
        if missing:
            raise ValueError(
                f"{source_file}: transmitter {description!r} missing {missing}"
            )
        frequency = _number(values["frequency"], field="frequency")
        baudrate = _number(values["baudrate"], field="baudrate")
        assert frequency is not None and baudrate is not None
        result.append(
            SatYamlTransmitter(
                source_file=source_file,
                satellite_name=satellite_name,
                norad_id=norad_id,
                description=description,
                frequency_hz=frequency,
                modulation=values["modulation"],
                baudrate=baudrate,
                framing=values["framing"],
                scrambler=values.get("scrambler"),
            )
        )
    return result


def load_satyaml_archive(path: str | Path) -> list[SatYamlTransmitter]:
    """Read every SatYAML definition directly from a bounded tar archive."""

    result: list[SatYamlTransmitter] = []
    seen: set[str] = set()
    with tarfile.open(path, "r:*") as archive:
        members = sorted(archive.getmembers(), key=lambda member: member.name)
        for member in members:
            if not member.isfile() or not SATYAML_MEMBER_PATTERN.search(member.name):
                continue
            if member.name in seen:
                raise ValueError(f"duplicate SatYAML archive member: {member.name}")
            seen.add(member.name)
            if member.size > SATYAML_MAX_MEMBER_BYTES:
                raise ValueError(f"oversized SatYAML archive member: {member.name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"cannot read SatYAML archive member: {member.name}")
            payload = stream.read(SATYAML_MAX_MEMBER_BYTES + 1)
            if len(payload) > SATYAML_MAX_MEMBER_BYTES:
                raise ValueError(f"oversized SatYAML payload: {member.name}")
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"non-UTF-8 SatYAML file: {member.name}") from exc
            result.extend(parse_satyaml_text(text, source_file=member.name))
    if not seen:
        raise ValueError("archive contains no SatYAML definitions")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parameter_string(params: Any, names: Iterable[str]) -> str | None:
    if not isinstance(params, Mapping):
        return None
    casefolded = {str(key).casefold(): value for key, value in params.items()}
    for name in names:
        value = casefolded.get(name.casefold())
        if value is not None:
            return _optional_string(value)
    return None


def parse_satnogs_json(payload: bytes | str) -> list[SatnogsTransmitter]:
    """Parse an unmodified SatNOGS ``/api/transmitters/`` JSON response."""

    decoded = json.loads(payload)
    if not isinstance(decoded, list):
        raise ValueError("SatNOGS transmitter snapshot must be a JSON list")
    result: list[SatnogsTransmitter] = []
    seen: set[str] = set()
    for index, record in enumerate(decoded):
        if not isinstance(record, Mapping):
            raise ValueError(f"SatNOGS record {index} is not an object")
        uuid = _optional_string(record.get("uuid"))
        if not uuid:
            raise ValueError(f"SatNOGS record {index} has no UUID")
        if uuid in seen:
            raise ValueError(f"duplicate SatNOGS transmitter UUID: {uuid}")
        seen.add(uuid)
        try:
            norad_id = int(record["norad_cat_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"SatNOGS record {uuid} has invalid NORAD ID") from exc
        params = record.get("params")
        result.append(
            SatnogsTransmitter(
                uuid=uuid,
                norad_id=norad_id,
                description=_optional_string(record.get("description")) or "",
                mode=_optional_string(record.get("mode")),
                baud=_number(record.get("baud"), field="baud", allow_none=True),
                downlink_low_hz=_number(
                    record.get("downlink_low"),
                    field="downlink_low",
                    allow_none=True,
                ),
                downlink_high_hz=_number(
                    record.get("downlink_high"),
                    field="downlink_high",
                    allow_none=True,
                ),
                framing=_parameter_string(params, ("framing", "frame")),
                scrambler=_parameter_string(params, ("scrambler", "randomizer")),
                status=_optional_string(record.get("status")),
                updated=_optional_string(record.get("updated")),
            )
        )
    return result


def load_satnogs_snapshot(path: str | Path) -> list[SatnogsTransmitter]:
    return parse_satnogs_json(Path(path).read_bytes())


_GENERIC_DESCRIPTION_TOKENS = {
    "beacon",
    "data",
    "downlink",
    "link",
    "mode",
    "radio",
    "telemetry",
    "transceiver",
    "transmitter",
    "uhf",
    "vhf",
}


def _expand_k_notation(match: re.Match[str]) -> str:
    whole = int(match.group(1))
    fraction = match.group(2)
    return str(whole * 1000 + int((fraction + "00")[:3]))


def description_features(description: str) -> tuple[str, ...]:
    """Return auditable features used solely for description matching."""

    normalized = unicodedata.normalize("NFKD", description).casefold()
    normalized = re.sub(r"(\d+)k(\d+)", _expand_k_notation, normalized)
    raw_tokens = re.findall(r"[a-z]+|\d+", normalized)
    aliases = {"gfsk": "fsk", "gmsk": "fsk", "msk": "fsk"}
    tokens = [aliases.get(token, token) for token in raw_tokens]
    return tuple(
        sorted(
            token
            for token in tokens
            if len(token) > 1 and token not in _GENERIC_DESCRIPTION_TOKENS
        )
    )


def description_match_score(left: str, right: str) -> float:
    left_features = description_features(left)
    right_features = description_features(right)
    if not left_features or not right_features:
        return 0.0
    left_set = set(left_features)
    right_set = set(right_features)
    dice = 2 * len(left_set & right_set) / (len(left_set) + len(right_set))
    sequence = SequenceMatcher(
        None,
        " ".join(left_features),
        " ".join(right_features),
        autojunk=False,
    ).ratio()
    return round(0.75 * dice + 0.25 * sequence, 6)


def _candidate_signature(candidate: SatnogsTransmitter) -> tuple[Any, ...]:
    return (
        candidate.mode,
        candidate.baud,
        candidate.downlink_low_hz,
        candidate.downlink_high_hz,
        candidate.framing,
        candidate.scrambler,
    )


def _frequency_is_compatible(
    definition: SatYamlTransmitter,
    candidate: SatnogsTransmitter,
    tolerance_hz: float = FREQUENCY_TOLERANCE_HZ,
) -> bool:
    low = candidate.downlink_low_hz
    high = candidate.downlink_high_hz
    if low is None and high is None:
        return False
    low = low if low is not None else high
    high = high if high is not None else low
    assert low is not None and high is not None
    lower, upper = sorted((low, high))
    return lower - tolerance_hz <= definition.frequency_hz <= upper + tolerance_hz


def _baud_is_compatible(
    definition: SatYamlTransmitter, candidate: SatnogsTransmitter
) -> bool:
    return candidate.baud is not None and math.isclose(
        definition.baudrate, candidate.baud, rel_tol=1e-9
    )


def match_transmitters(
    satyaml: Sequence[SatYamlTransmitter],
    satnogs: Sequence[SatnogsTransmitter],
    *,
    minimum_score: float = DESCRIPTION_MIN_SCORE,
    minimum_margin: float = DESCRIPTION_MIN_MARGIN,
) -> list[TransmitterMatch]:
    """Match by required NORAD equality and deterministic description similarity."""

    by_norad: dict[int, list[SatnogsTransmitter]] = {}
    for transmitter in satnogs:
        by_norad.setdefault(transmitter.norad_id, []).append(transmitter)

    result: list[TransmitterMatch] = []
    for definition in satyaml:
        candidates = sorted(by_norad.get(definition.norad_id, []), key=lambda item: item.uuid)
        if not candidates:
            result.append(
                TransmitterMatch(definition, None, "no_norad_candidate", None)
            )
            continue
        if len(candidates) == 1:
            score = description_match_score(
                definition.description, candidates[0].description
            )
            result.append(
                TransmitterMatch(
                    definition,
                    candidates[0],
                    "unique_norad_candidate",
                    score,
                    1,
                )
            )
            continue

        ranked = sorted(
            (
                (
                    description_match_score(definition.description, candidate.description),
                    candidate,
                )
                for candidate in candidates
            ),
            key=lambda item: (-item[0], item[1].uuid),
        )
        best_score, best = ranked[0]
        if best_score < minimum_score:
            result.append(
                TransmitterMatch(
                    definition,
                    None,
                    "description_below_threshold",
                    best_score,
                )
            )
            continue
        contenders = [item for item in ranked if best_score - item[0] < minimum_margin]
        signatures = {_candidate_signature(item[1]) for item in contenders}
        if len(contenders) > 1 and len(signatures) > 1:
            result.append(
                TransmitterMatch(
                    definition,
                    None,
                    "ambiguous_description",
                    best_score,
                    len(contenders),
                )
            )
            continue
        status = "description_match"
        if len(contenders) > 1:
            status = "equivalent_description_tie"
        frequency_alternatives = [
            candidate
            for candidate in candidates
            if _frequency_is_compatible(definition, candidate)
        ]
        baud_alternatives = [
            candidate for candidate in candidates if _baud_is_compatible(definition, candidate)
        ]
        conflicts = []
        if frequency_alternatives and not _frequency_is_compatible(definition, best):
            conflicts.append("frequency")
        if baud_alternatives and not _baud_is_compatible(definition, best):
            conflicts.append("baudrate")
        if conflicts:
            result.append(
                TransmitterMatch(
                    definition,
                    None,
                    "description_parameter_conflict_" + "_".join(conflicts),
                    best_score,
                    len(contenders),
                )
            )
            continue
        result.append(
            TransmitterMatch(
                definition,
                best,
                status,
                best_score,
                len(contenders),
            )
        )
    return result


def _normalized_modulation(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.casefold().replace("-", " ").split())
    families = (
        "dbpsk",
        "bpsk",
        "qpsk",
        "gfsk",
        "gmsk",
        "afsk",
        "fsk",
        "msk",
        "ook",
        "psk",
    )
    for family in families:
        if normalized == family or normalized.startswith(family + " "):
            return family
    return normalized


def _text_comparison(left: str | None, right: str | None) -> str:
    if left is None:
        return "unavailable_in_satyaml"
    if right is None:
        return "unavailable_in_satnogs"
    return "match" if left.casefold() == right.casefold() else "mismatch"


def comparison_rows(
    matches: Sequence[TransmitterMatch],
    *,
    frequency_tolerance_hz: float = FREQUENCY_TOLERANCE_HZ,
) -> list[dict[str, Any]]:
    """Create one auditable row for each match and comparison field."""

    rows: list[dict[str, Any]] = []
    for match in matches:
        definition = match.satyaml
        candidate = match.satnogs
        common: dict[str, Any] = {
            "norad_id": definition.norad_id,
            "satellite_name": definition.satellite_name,
            "satyaml_source_file": definition.source_file,
            "satyaml_description": definition.description,
            "satnogs_uuid": candidate.uuid if candidate else None,
            "satnogs_description": candidate.description if candidate else None,
            "match_status": match.status,
            "description_score": match.description_score,
            "equivalent_candidates": match.equivalent_candidates,
        }
        if candidate is None:
            for field, value in (
                ("modulation", definition.modulation),
                ("baudrate", str(definition.baudrate)),
                ("frequency_downlink_hz", str(definition.frequency_hz)),
                ("framing", definition.framing),
                ("scrambler", definition.scrambler),
            ):
                rows.append(
                    common
                    | {
                        "field": field,
                        "satyaml_value": value,
                        "satnogs_value": None,
                        "comparison_status": "not_compared_unmatched",
                        "numeric_difference": None,
                    }
                )
            continue

        modulation_status = _text_comparison(
            _normalized_modulation(definition.modulation),
            _normalized_modulation(candidate.mode),
        )
        rows.append(
            common
            | {
                "field": "modulation",
                "satyaml_value": definition.modulation,
                "satnogs_value": candidate.mode,
                "comparison_status": modulation_status,
                "numeric_difference": None,
            }
        )

        baud_status = "unavailable_in_satnogs"
        baud_difference: float | None = None
        if candidate.baud is not None:
            baud_difference = definition.baudrate - candidate.baud
            baud_status = (
                "match"
                if math.isclose(definition.baudrate, candidate.baud, rel_tol=1e-9)
                else "mismatch"
            )
        rows.append(
            common
            | {
                "field": "baudrate",
                "satyaml_value": str(definition.baudrate),
                "satnogs_value": (
                    str(candidate.baud) if candidate.baud is not None else None
                ),
                "comparison_status": baud_status,
                "numeric_difference": baud_difference,
            }
        )

        low = candidate.downlink_low_hz
        high = candidate.downlink_high_hz
        frequency_status = "unavailable_in_satnogs"
        frequency_difference: float | None = None
        satnogs_frequency_value: str | None = None
        if low is not None or high is not None:
            low = low if low is not None else high
            high = high if high is not None else low
            assert low is not None and high is not None
            lower, upper = sorted((low, high))
            center = (lower + upper) / 2
            frequency_difference = definition.frequency_hz - center
            satnogs_frequency_value = (
                str(center) if lower == upper else f"{lower}..{upper}"
            )
            frequency_status = (
                "match"
                if lower - frequency_tolerance_hz
                <= definition.frequency_hz
                <= upper + frequency_tolerance_hz
                else "mismatch"
            )
        rows.append(
            common
            | {
                "field": "frequency_downlink_hz",
                "satyaml_value": str(definition.frequency_hz),
                "satnogs_value": satnogs_frequency_value,
                "comparison_status": frequency_status,
                "numeric_difference": frequency_difference,
            }
        )

        for field, left, right in (
            ("framing", definition.framing, candidate.framing),
            ("scrambler", definition.scrambler, candidate.scrambler),
        ):
            rows.append(
                common
                | {
                    "field": field,
                    "satyaml_value": left,
                    "satnogs_value": right,
                    "comparison_status": _text_comparison(left, right),
                    "numeric_difference": None,
                }
            )
    return rows


def summarize_comparison(
    matches: Sequence[TransmitterMatch], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    by_match_status: dict[str, int] = {}
    for match in matches:
        by_match_status[match.status] = by_match_status.get(match.status, 0) + 1
    by_field: dict[str, dict[str, int]] = {}
    for row in rows:
        field = str(row["field"])
        status = str(row["comparison_status"])
        field_counts = by_field.setdefault(field, {})
        field_counts[status] = field_counts.get(status, 0) + 1
    matched = sum(1 for match in matches if match.satnogs is not None)
    return {
        "satyaml_transmitters": len(matches),
        "matched_transmitters": matched,
        "unmatched_transmitters": len(matches) - matched,
        "match_status_counts": dict(sorted(by_match_status.items())),
        "field_status_counts": {
            field: dict(sorted(counts.items())) for field, counts in sorted(by_field.items())
        },
    }


def dataclass_records(items: Iterable[Any]) -> list[dict[str, Any]]:
    """Convert dataclass instances to JSON-ready records."""

    return [asdict(item) for item in items]
