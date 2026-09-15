"""Adapters and provenance helpers for real public golden WAV recordings."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import unicodedata
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class GoldenDecodeError(RuntimeError):
    """Raised when an external golden decoder cannot produce usable output."""


@dataclass(frozen=True)
class GoldenWav:
    path: Path
    sha256: str
    size_bytes: int
    channels: int
    sample_rate_hz: int
    sample_width_bytes: int
    frame_count: int
    compression: str

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.sample_rate_hz


@dataclass(frozen=True)
class SupportedSatellite:
    name: str
    norad_id: int
    downlinks: tuple[str, ...]

    @property
    def direwolf_baud(self) -> int | None:
        for downlink in self.downlinks:
            normalized = downlink.upper()
            if (
                re.match(r"^1K2(?:\s|$)", normalized)
                and "AFSK" in normalized
                and "AX.25" in normalized
            ):
                return 1_200
        return None


@dataclass(frozen=True)
class GoldenMappingOverride:
    satellite: str
    evidence: str


@dataclass(frozen=True)
class GoldenManifestEntry:
    filename: str
    satellite: str | None
    norad_id: int | None
    downlinks: tuple[str, ...]
    match_method: str
    mapping_evidence: str
    direwolf_baud: int | None


GOLDEN_FILENAME_OVERRIDES: Mapping[str, GoldenMappingOverride] = {
    "ao40_uncoded.wav": GoldenMappingOverride(
        "AO-40",
        "git 1d0c93e: Added AO-40 uncoded telemetry recording",
    ),
    "astrocast.wav": GoldenMappingOverride(
        "Astrocast 0.1",
        "git 5fa4dd6: Added Astrocast 0.1 recording",
    ),
    "astrocast_9k6.wav": GoldenMappingOverride(
        "Astrocast 0.1",
        "git ea2bd26: Added Astrocast 0.1 9k6 recording",
    ),
    "astrocast_old.wav": GoldenMappingOverride(
        "Astrocast 0.1",
        "git 5fa4dd6: Added Astrocast 0.1 recording",
    ),
    "az02.wav": GoldenMappingOverride(
        "NSIGHT-1",
        "git b5b7db9: Added QB50 AZ02 nSIGHT recording",
    ),
    "ca03_9k6.wav": GoldenMappingOverride(
        "CA03",
        "git 57112f0: Added QB50 CA03 Ex-Alta 1 9k6 recording",
    ),
    "dsat-image.wav": GoldenMappingOverride(
        "D-SAT",
        "git 4dd6259: Added D-SAT recording with image downlink",
    ),
    "lilacsat1-image.wav": GoldenMappingOverride(
        "LilacSat-1",
        "git f2b47ba: Added LilacSat-1 image downlink recording",
    ),
    "picsat_9k6.wav": GoldenMappingOverride(
        "PicSat",
        "git 6ff899c: Added PicSat 9k6 recording",
    ),
    "sat_3cat_1.wav": GoldenMappingOverride(
        "3CAT-1",
        "git 09ae483: Added 3CAT-1 recording",
    ),
    "sat_3cat_2.wav": GoldenMappingOverride(
        "3CAT-2",
        "git c580800: Added recordings for some satellites",
    ),
    "se01.wav": GoldenMappingOverride(
        "QBEE",
        "git 28f4991: Added QB50 SE01 QBEE recording",
    ),
    "smog_p_5k.wav": GoldenMappingOverride(
        "SMOG-P",
        "git dd27589: Added SMOG-P 5000 baud recording",
    ),
    "smog_p_long.wav": GoldenMappingOverride(
        "SMOG-P",
        "git adcbb53: Added recording of SMOG-P long frames",
    ),
    "swiatowid-ax25.wav": GoldenMappingOverride(
        "Swiatowid",
        "git 8fecf9f: Add Swiatowid AX.25 recording",
    ),
    "tanusha3_pm.wav": GoldenMappingOverride(
        "Tanusha-3",
        "git 6f28cdb: Added TANUSHA-3 FM + PM recording",
    ),
    "ty_4.wav": GoldenMappingOverride(
        "TY 4-01",
        "git 707a757: Added TY 4-01 recording",
    ),
    "us04.wav": GoldenMappingOverride(
        "COLUMBIA",
        "git 9f368cd: Added QB50 US04 COLUMBIA recording",
    ),
}


GOLDEN_FILENAME_BLOCKERS: Mapping[str, str] = {
    "dstar_one.wav": (
        "git 93fad2a names both D-STAR ONE Sparrow and iSat; catalog identity "
        "is ambiguous"
    ),
    "equisat.wav": "git 61c68cf names EQUiSat, absent from the 5.9.0 catalog",
    "fr05.wav": (
        "git 4a8e8c1 names QB50 FR05 SpaceCube, absent from the 5.9.0 catalog"
    ),
    "koyo.wav": "git 952ddfe names Koyo, absent from the 5.9.0 catalog",
    "nusat.wav": (
        "git 76a80a9 names Nusat-2; only NuSat 1 is present in the catalog"
    ),
}


def sha256_file(path: Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    """Hash a file without loading the recording into memory."""
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_golden_wav(path: Path) -> GoldenWav:
    """Read and validate the audio contract of satellite-recordings."""
    resolved = path.resolve(strict=True)
    with wave.open(str(resolved), "rb") as recording:
        metadata = GoldenWav(
            path=resolved,
            sha256=sha256_file(resolved),
            size_bytes=resolved.stat().st_size,
            channels=recording.getnchannels(),
            sample_rate_hz=recording.getframerate(),
            sample_width_bytes=recording.getsampwidth(),
            frame_count=recording.getnframes(),
            compression=recording.getcomptype(),
        )
    if metadata.channels != 1:
        raise GoldenDecodeError("golden WAV must be mono")
    if metadata.sample_rate_hz != 48_000:
        raise GoldenDecodeError("golden WAV must have a 48 kHz sample rate")
    if metadata.compression != "NONE":
        raise GoldenDecodeError("golden WAV must contain uncompressed PCM")
    return metadata


_SATELLITE_HEADER = re.compile(r"^\* (.+?) \(NORAD (\d+)\)$")


def parse_supported_satellites(output: str) -> tuple[SupportedSatellite, ...]:
    """Parse the stable human-readable output of ``--list_satellites``."""
    parsed: list[SupportedSatellite] = []
    name: str | None = None
    norad_id: int | None = None
    downlinks: list[str] = []

    def finish() -> None:
        nonlocal name, norad_id, downlinks
        if name is not None and norad_id is not None:
            parsed.append(SupportedSatellite(name, norad_id, tuple(downlinks)))
        name = None
        norad_id = None
        downlinks = []

    for line in output.splitlines():
        header = _SATELLITE_HEADER.match(line)
        if header is not None:
            finish()
            name = header.group(1)
            norad_id = int(header.group(2))
        elif name is not None and line.startswith("    ") and line.strip():
            downlinks.append(line.strip())
    finish()
    if not parsed:
        raise GoldenDecodeError("gr_satellites returned no supported satellites")
    names = [satellite.name for satellite in parsed]
    if len(names) != len(set(names)):
        raise GoldenDecodeError("gr_satellites returned duplicate satellite names")
    return tuple(parsed)


def normalize_satellite_key(value: str) -> str:
    """Normalize spelling only; this intentionally performs no fuzzy matching."""
    ascii_value = unicodedata.normalize("NFKD", value).encode(
        "ascii",
        "ignore",
    ).decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_value.casefold())


def build_golden_manifest(
    recording_dir: Path,
    supported: Sequence[SupportedSatellite],
    *,
    overrides: Mapping[str, GoldenMappingOverride] = GOLDEN_FILENAME_OVERRIDES,
    blockers: Mapping[str, str] = GOLDEN_FILENAME_BLOCKERS,
) -> tuple[GoldenManifestEntry, ...]:
    """Map WAV names using exact normalization or auditable explicit overrides."""
    by_name = {satellite.name: satellite for satellite in supported}
    by_normalized: dict[str, list[SupportedSatellite]] = {}
    for satellite in supported:
        by_normalized.setdefault(
            normalize_satellite_key(satellite.name),
            [],
        ).append(satellite)

    entries: list[GoldenManifestEntry] = []
    for recording in sorted(recording_dir.glob("*.wav")):
        filename = recording.name
        target: SupportedSatellite | None = None
        method: str
        evidence: str
        if filename in blockers:
            method = "blocked"
            evidence = blockers[filename]
        elif filename in overrides:
            override = overrides[filename]
            target = by_name.get(override.satellite)
            if target is None:
                raise GoldenDecodeError(
                    f"override target is absent from catalog: {override.satellite}"
                )
            method = "explicit_override"
            evidence = override.evidence
        else:
            candidates = by_normalized.get(
                normalize_satellite_key(recording.stem),
                [],
            )
            if len(candidates) == 1:
                target = candidates[0]
                method = "exact_normalized"
                evidence = "normalized filename stem equals normalized catalog name"
            elif len(candidates) > 1:
                method = "blocked"
                evidence = "normalized filename matches multiple catalog names"
            else:
                method = "blocked"
                evidence = "no exact normalized match and no explicit override"
        entries.append(
            GoldenManifestEntry(
                filename=filename,
                satellite=target.name if target else None,
                norad_id=target.norad_id if target else None,
                downlinks=target.downlinks if target else (),
                match_method=method,
                mapping_evidence=evidence,
                direwolf_baud=target.direwolf_baud if target else None,
            )
        )
    return tuple(entries)


_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_HEX_LINE = re.compile(r"^\s*([0-9a-fA-F]{3,8}):\s+(.*)$")
_HEX_BYTE = re.compile(r"^[0-9a-fA-F]{2}$")


def parse_hex_dump(output: str) -> tuple[bytes, ...]:
    """Parse frame blocks printed by gr_satellites or Dire Wolf ``atest``."""
    clean = _ANSI_ESCAPE.sub("", output)
    frames: list[bytes] = []
    current: bytearray | None = None
    for line in clean.splitlines():
        match = _HEX_LINE.match(line)
        if match is None:
            continue
        offset = int(match.group(1), 16)
        octets: list[int] = []
        for token in match.group(2).split():
            if _HEX_BYTE.fullmatch(token) is None:
                break
            octets.append(int(token, 16))
        if not octets:
            continue
        if offset == 0:
            if current:
                frames.append(bytes(current))
            current = bytearray()
        if current is None or offset != len(current):
            raise GoldenDecodeError(
                f"non-contiguous decoder hexdump at offset {offset}"
            )
        current.extend(octets)
    if current:
        frames.append(bytes(current))
    return tuple(frames)


def ordered_frame_digest(frames: Sequence[bytes]) -> str:
    """Hash an ordered frame sequence with unambiguous length prefixes."""
    digest = hashlib.sha256()
    for frame in frames:
        value = bytes(frame)
        digest.update(len(value).to_bytes(8, byteorder="big"))
        digest.update(value)
    return digest.hexdigest()


def unique_frame_sha256(frames: Sequence[bytes]) -> tuple[str, ...]:
    """Return sorted hashes for comparing decoders that retain duplicates."""
    return tuple(sorted({hashlib.sha256(bytes(frame)).hexdigest() for frame in frames}))


def unique_frame_set_digest(frames: Sequence[bytes]) -> str:
    """Compactly hash the sorted set of unique per-frame SHA-256 values."""
    hashes = unique_frame_sha256(frames)
    return ordered_frame_digest(tuple(bytes.fromhex(value) for value in hashes))


def _result_summary(result: Any) -> dict[str, Any]:
    raw_frames = tuple(bytes.fromhex(value) for value in result.raw_frames_hex)
    return {
        "status": result.status,
        "raw_frame_count": result.n_frames_raw,
        "ordered_frame_digest": ordered_frame_digest(raw_frames),
        "unique_frame_sha256": list(unique_frame_sha256(raw_frames)),
        "unique_frame_set_digest": unique_frame_set_digest(raw_frames),
        "cpu_seconds": result.cpu_seconds,
        "wall_seconds": result.wall_seconds,
        "peak_rss_mb": result.peak_rss_mb,
        "error_class": result.error_class,
        "error_message": result.error_message,
    }


def _run_pair(
    *,
    stores: Sequence[Any],
    adapter: Any,
    recording_id: str,
    recording_path: Path,
    protocol_id: str,
    parameters: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    from .harness import run_attempt

    if len(stores) != 2:
        raise ValueError("exactly two independent attempt stores are required")
    summaries: list[dict[str, Any]] = []
    for store in stores:
        result = run_attempt(
            store=store,
            adapter=adapter,
            recording_id=recording_id,
            recording_path=recording_path,
            protocol_id=protocol_id,
            parameters=parameters,
            seed=0,
            timeout_s=timeout_s,
        )
        if result is None:
            raise GoldenDecodeError(
                "matrix store already contains this terminal attempt"
            )
        summaries.append(_result_summary(result))
    ordered_equal = (
        summaries[0]["status"] == summaries[1]["status"]
        and summaries[0]["error_class"] == summaries[1]["error_class"]
        and summaries[0]["error_message"] == summaries[1]["error_message"]
        and summaries[0]["ordered_frame_digest"]
        == summaries[1]["ordered_frame_digest"]
    )
    unique_equal = (
        summaries[0]["status"] == summaries[1]["status"]
        and summaries[0]["unique_frame_sha256"]
        == summaries[1]["unique_frame_sha256"]
    )
    return {
        "runs": summaries,
        "ordered_deterministic": ordered_equal,
        "unique_deterministic": unique_equal,
        "decoded_any_frames": any(
            summary["raw_frame_count"] > 0 for summary in summaries
        ),
    }


def run_golden_corpus_matrix(
    *,
    recording_dir: Path,
    supported: Sequence[SupportedSatellite],
    gr_adapter: GrSatellitesAdapter,
    gr_stores: Sequence[Any],
    dump_path: Path,
    timeout_s: float,
    direwolf_adapter: DireWolfAtestAdapter | None = None,
    direwolf_stores: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    """Run every conservatively mapped recording twice, strictly sequentially."""
    dump_directory = dump_path.resolve(strict=True)
    if not dump_directory.is_dir():
        raise ValueError("dump_path must be an existing directory")
    manifest = build_golden_manifest(recording_dir, supported)
    matrix: list[dict[str, Any]] = []
    for entry in manifest:
        recording = recording_dir / entry.filename
        row: dict[str, Any] = asdict(entry)
        row["recording_sha256"] = sha256_file(recording)
        row["gr_satellites"] = None
        row["direwolf"] = None
        row["cross_decoder_unique_comparison"] = "not_attempted"
        if entry.satellite is None:
            matrix.append(row)
            continue
        recording_id = f"satellite-recordings:{entry.filename}"
        row["gr_satellites"] = _run_pair(
            stores=gr_stores,
            adapter=gr_adapter,
            recording_id=recording_id,
            recording_path=recording,
            protocol_id="golden_audio_catalog",
            parameters={
                "satellite": entry.satellite,
                "sample_rate_hz": 48_000,
                "dump_path": str(dump_directory),
            },
            timeout_s=timeout_s,
        )
        if entry.direwolf_baud is not None and direwolf_adapter is not None:
            row["direwolf"] = _run_pair(
                stores=direwolf_stores,
                adapter=direwolf_adapter,
                recording_id=recording_id,
                recording_path=recording,
                protocol_id="golden_audio_catalog_afsk_ax25",
                parameters={
                    "baud": entry.direwolf_baud,
                    "profile": "E+",
                },
                timeout_s=timeout_s,
            )
            gr_unique = row["gr_satellites"]["runs"][0][
                "unique_frame_sha256"
            ]
            direwolf_unique = row["direwolf"]["runs"][0][
                "unique_frame_sha256"
            ]
            if gr_unique and direwolf_unique:
                if gr_unique == direwolf_unique:
                    comparison = "equal"
                elif set(gr_unique) & set(direwolf_unique):
                    comparison = "partial_overlap"
                else:
                    comparison = "disjoint"
                row["cross_decoder_unique_comparison"] = comparison
            else:
                row["cross_decoder_unique_comparison"] = (
                    "not_comparable_missing_frames"
                )
        matrix.append(row)
    return matrix


def _decoder_environment(
    seed: int,
    *,
    gr_scheduler: str | None = None,
) -> dict[str, str]:
    """Freeze child process knobs and prohibit telemetry submission.

    GNU Radio 3.10.12 only registers the TPB scheduler.  Rejecting any other
    requested value prevents GNU Radio from silently falling back to TPB while
    an experiment is incorrectly labelled as using a different scheduler.
    """
    if gr_scheduler not in {None, "TPB"}:
        raise ValueError("gr_scheduler must be 'TPB' when specified")
    environment = os.environ.copy()
    environment.update(
        {
            "GR_SATELLITES_SUBMIT_TLM": "0",
            "LANG": "C",
            "LC_ALL": "C",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "PYTHONHASHSEED": str(seed),
            "TZ": "UTC",
        }
    )
    if gr_scheduler is not None:
        environment["GR_SCHEDULER"] = gr_scheduler
    return environment


def _run_decoder(
    command: list[str],
    timeout_s: float,
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[bytes, ...]:
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(environment) if environment is not None else None,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"decoder exceeded {timeout_s:.3f}s") from exc
    if completed.returncode != 0:
        tail = completed.stdout[-2_000:].strip()
        raise GoldenDecodeError(
            f"decoder exited with status {completed.returncode}: {tail}"
        )
    return parse_hex_dump(completed.stdout)


@dataclass(frozen=True)
class GrSatellitesAdapter:
    """Decode 48 kHz mono WAV files with the gr_satellites command."""

    executable: Path
    version: str
    name: str = "gr-satellites"

    def decode(
        self,
        recording: Path,
        protocol_id: str,
        config: dict[str, Any],
        timeout_s: float,
        *,
        seed: int,
    ) -> Sequence[bytes]:
        del protocol_id
        satellite = config.get("satellite")
        if not isinstance(satellite, str) or not satellite.strip():
            raise ValueError("config.satellite must be a non-empty string")
        sample_rate_hz = int(config.get("sample_rate_hz", 48_000))
        if sample_rate_hz <= 0:
            raise ValueError("config.sample_rate_hz must be positive")
        throttle = config.get("throttle", False)
        if not isinstance(throttle, bool):
            raise ValueError("config.throttle must be a boolean")
        scheduler = config.get("scheduler", "TPB")
        if scheduler != "TPB":
            raise ValueError("config.scheduler must be 'TPB'")
        inspect_golden_wav(recording)
        command = [
            str(self.executable.resolve(strict=True)),
            satellite,
            "--wavfile",
            str(recording.resolve(strict=True)),
            "--samp_rate",
            str(sample_rate_hz),
            "--hexdump",
        ]
        if throttle:
            command.append("--throttle")
        dump_path = config.get("dump_path")
        if dump_path is not None:
            dump_directory = Path(str(dump_path)).resolve(strict=True)
            if not dump_directory.is_dir():
                raise ValueError("config.dump_path must name an existing directory")
            command.extend(["--dump_path", str(dump_directory)])
        return _run_decoder(
            command,
            timeout_s,
            environment=_decoder_environment(seed, gr_scheduler=scheduler),
        )


@dataclass(frozen=True)
class DireWolfAtestAdapter:
    """Decode an AX.25 golden WAV with Dire Wolf's offline ``atest`` tool."""

    executable: Path
    version: str
    name: str = "direwolf-atest"

    def decode(
        self,
        recording: Path,
        protocol_id: str,
        config: dict[str, Any],
        timeout_s: float,
        *,
        seed: int,
    ) -> Sequence[bytes]:
        del protocol_id
        baud = int(config.get("baud", 1_200))
        profile = str(config.get("profile", "E+"))
        if baud <= 0:
            raise ValueError("config.baud must be positive")
        if re.fullmatch(r"[A-Za-z0-9+_-]+", profile) is None:
            raise ValueError("config.profile contains unsupported characters")
        inspect_golden_wav(recording)
        return _run_decoder(
            [
                str(self.executable.resolve(strict=True)),
                "-B",
                str(baud),
                "-P",
                profile,
                "-h",
                str(recording.resolve(strict=True)),
            ],
            timeout_s,
            environment=_decoder_environment(seed),
        )
