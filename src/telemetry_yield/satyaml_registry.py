"""Fault-isolated discovery of gr-satellites SatYAML profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Literal, Mapping

from .gr_satellites_backend import (
    GrSatellitesProfile,
    GrSatellitesTransmitter,
)
from .models import JsonValue


DiagnosticCode = Literal[
    "dependency_error",
    "path_error",
    "read_error",
    "size_error",
    "yaml_error",
    "schema_error",
    "duplicate_name",
    "duplicate_norad",
]


@dataclass(frozen=True, slots=True)
class SatYamlDiagnostic:
    path: str
    code: DiagnosticCode
    message: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class SatYamlRegistry:
    """Parsed profiles and unambiguous, case-insensitive lookup indexes."""

    profiles: tuple[GrSatellitesProfile, ...]
    by_name: Mapping[str, GrSatellitesProfile]
    by_norad: Mapping[int, GrSatellitesProfile]
    diagnostics: tuple[SatYamlDiagnostic, ...]
    conflicted_names: tuple[str, ...] = ()
    conflicted_norads: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        profiles = tuple(
            sorted(
                self.profiles,
                key=lambda profile: (
                    (profile.name or "").casefold(),
                    profile.norad_id or 0,
                    profile.selector,
                ),
            )
        )
        diagnostics = tuple(
            sorted(
                self.diagnostics,
                key=lambda item: (item.path, item.code, item.message),
            )
        )
        object.__setattr__(self, "profiles", profiles)
        object.__setattr__(self, "by_name", MappingProxyType(dict(sorted(self.by_name.items()))))
        object.__setattr__(self, "by_norad", MappingProxyType(dict(sorted(self.by_norad.items()))))
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(self, "conflicted_names", tuple(sorted(set(self.conflicted_names))))
        object.__setattr__(self, "conflicted_norads", tuple(sorted(set(self.conflicted_norads))))

    def get_by_name(self, name: str) -> GrSatellitesProfile | None:
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        return self.by_name.get(name.casefold())

    def get_by_norad(self, norad_id: int) -> GrSatellitesProfile | None:
        if isinstance(norad_id, bool) or not isinstance(norad_id, int):
            raise TypeError("norad_id must be an integer")
        return self.by_norad.get(norad_id)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "profiles": [profile.to_dict() for profile in self.profiles],
            "name_index": {
                name: profile.selector for name, profile in self.by_name.items()
            },
            "norad_index": {
                str(norad): profile.selector
                for norad, profile in self.by_norad.items()
            },
            "conflicted_names": list(self.conflicted_names),
            "conflicted_norads": list(self.conflicted_norads),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def _paths(
    roots: Iterable[str | Path] | str | Path,
    diagnostics: list[SatYamlDiagnostic],
) -> tuple[Path, ...]:
    discovered: dict[str, Path] = {}
    supplied_roots: Iterable[str | Path]
    if isinstance(roots, (str, Path)):
        supplied_roots = (roots,)
    else:
        supplied_roots = roots
    for supplied in supplied_roots:
        path = Path(supplied)
        if not path.exists():
            diagnostics.append(
                SatYamlDiagnostic(str(path), "path_error", "path does not exist")
            )
            continue
        if path.is_symlink():
            diagnostics.append(
                SatYamlDiagnostic(str(path), "path_error", "symbolic links are not loaded")
            )
            continue
        if path.is_file():
            if path.suffix.casefold() != ".yml":
                diagnostics.append(
                    SatYamlDiagnostic(str(path), "path_error", "expected a .yml file")
                )
                continue
            resolved = path.resolve()
            discovered[str(resolved)] = resolved
            continue
        if not path.is_dir():
            diagnostics.append(
                SatYamlDiagnostic(str(path), "path_error", "path is not a file or directory")
            )
            continue
        for candidate in path.rglob("*.yml"):
            if candidate.is_symlink() or not candidate.is_file():
                if candidate.is_symlink():
                    diagnostics.append(
                        SatYamlDiagnostic(
                            str(candidate),
                            "path_error",
                            "symbolic links are not loaded",
                        )
                    )
                continue
            resolved = candidate.resolve()
            discovered[str(resolved)] = resolved
    return tuple(discovered[key] for key in sorted(discovered))


def _required_string(mapping: Mapping[object, object], key: str, *, path: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}.{key} must be a non-empty string")
    return value


def _explicit_fec(value: object, *, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, list):
        values = tuple(value)
    else:
        raise ValueError(f"{path}.fec must be a string or list of strings")
    if not values or any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{path}.fec values must be non-empty strings")
    if len(set(values)) != len(values):
        raise ValueError(f"{path}.fec values must be unique")
    return values


def _profile(document: object, path: Path) -> GrSatellitesProfile:
    if not isinstance(document, Mapping):
        raise ValueError("SatYAML root must be a mapping")
    name = _required_string(document, "name", path="$")
    norad = document.get("norad")
    if isinstance(norad, bool) or not isinstance(norad, int) or norad <= 0:
        raise ValueError("$.norad must be a positive integer")
    transmitters = document.get("transmitters")
    if not isinstance(transmitters, Mapping) or not transmitters:
        raise ValueError("$.transmitters must be a non-empty mapping")

    parsed: list[GrSatellitesTransmitter] = []
    for transmitter_id, raw in sorted(
        transmitters.items(), key=lambda item: str(item[0]).casefold()
    ):
        if not isinstance(transmitter_id, str) or not transmitter_id.strip():
            raise ValueError("transmitter IDs must be non-empty strings")
        location = f"$.transmitters.{transmitter_id}"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{location} must be a mapping")
        if any(not isinstance(key, str) for key in raw):
            raise ValueError(f"{location} keys must be strings")
        modulation = _required_string(raw, "modulation", path=location)
        framing = _required_string(raw, "framing", path=location)
        fec = _explicit_fec(raw.get("fec"), path=location)
        metadata = {
            key: value
            for key, value in raw.items()
            if key not in {"modulation", "framing", "fec"}
        }
        try:
            parsed.append(
                GrSatellitesTransmitter(
                    transmitter_id=transmitter_id,
                    modulation=modulation,
                    framing=framing,
                    fec=fec,
                    metadata=metadata,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{location} metadata is not JSON-safe: {exc}") from exc

    return GrSatellitesProfile(
        selector=str(path),
        selector_kind="satyaml",
        transmitters=tuple(parsed),
        name=name,
        norad_id=norad,
    )


def build_satyaml_registry(
    paths: Iterable[str | Path] | str | Path,
    *,
    max_file_bytes: int = 1024 * 1024,
) -> SatYamlRegistry:
    """Load every `.yml` independently and build conflict-free indexes.

    The loader describes what each SatYAML requests. It does not assert that a
    particular gr-satellites executable contains every referenced GNU Radio
    block; executable compatibility is a separate deployment check.
    """

    if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int):
        raise TypeError("max_file_bytes must be an integer")
    if max_file_bytes < 1:
        raise ValueError("max_file_bytes must be positive")
    diagnostics: list[SatYamlDiagnostic] = []
    discovered = _paths(paths, diagnostics)
    try:
        import yaml
    except ModuleNotFoundError:
        diagnostics.append(
            SatYamlDiagnostic(
                "<registry>",
                "dependency_error",
                "PyYAML is required to load SatYAML profiles",
            )
        )
        return SatYamlRegistry((), {}, {}, tuple(diagnostics))

    profiles: list[GrSatellitesProfile] = []
    for path in discovered:
        try:
            size = path.stat().st_size
        except OSError as exc:
            diagnostics.append(
                SatYamlDiagnostic(str(path), "read_error", f"stat failed: {exc}")
            )
            continue
        if size > max_file_bytes:
            diagnostics.append(
                SatYamlDiagnostic(
                    str(path),
                    "size_error",
                    f"file exceeds {max_file_bytes} bytes",
                )
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            diagnostics.append(
                SatYamlDiagnostic(str(path), "read_error", f"read failed: {exc}")
            )
            continue
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            diagnostics.append(
                SatYamlDiagnostic(
                    str(path),
                    "yaml_error",
                    f"safe YAML parse failed: {type(exc).__name__}",
                )
            )
            continue
        try:
            profiles.append(_profile(document, path))
        except (TypeError, ValueError) as exc:
            diagnostics.append(
                SatYamlDiagnostic(str(path), "schema_error", str(exc))
            )

    profiles.sort(
        key=lambda profile: (
            (profile.name or "").casefold(),
            profile.norad_id or 0,
            profile.selector,
        )
    )
    names: dict[str, list[GrSatellitesProfile]] = {}
    norads: dict[int, list[GrSatellitesProfile]] = {}
    for profile in profiles:
        assert profile.name is not None
        assert profile.norad_id is not None
        names.setdefault(profile.name.casefold(), []).append(profile)
        norads.setdefault(profile.norad_id, []).append(profile)

    by_name: dict[str, GrSatellitesProfile] = {}
    conflicted_names: list[str] = []
    for key, matches in sorted(names.items()):
        if len(matches) == 1:
            by_name[key] = matches[0]
            continue
        conflicted_names.append(key)
        diagnostics.append(
            SatYamlDiagnostic(
                matches[0].selector,
                "duplicate_name",
                f"profile name {matches[0].name!r} is declared by: "
                + ", ".join(item.selector for item in matches),
            )
        )

    by_norad: dict[int, GrSatellitesProfile] = {}
    conflicted_norads: list[int] = []
    for key, matches in sorted(norads.items()):
        if len(matches) == 1:
            by_norad[key] = matches[0]
            continue
        conflicted_norads.append(key)
        diagnostics.append(
            SatYamlDiagnostic(
                matches[0].selector,
                "duplicate_norad",
                f"NORAD {key} is declared by: "
                + ", ".join(item.selector for item in matches),
            )
        )

    return SatYamlRegistry(
        profiles=tuple(profiles),
        by_name=by_name,
        by_norad=by_norad,
        diagnostics=tuple(diagnostics),
        conflicted_names=tuple(conflicted_names),
        conflicted_norads=tuple(conflicted_norads),
    )
