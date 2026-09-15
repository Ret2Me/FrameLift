"""CAMRAS index parsing and raw complex-int16 ingest helpers.

The CAMRAS SatNOGS recordings are headerless, interleaved signed int16
samples in explicit little-endian I/Q order.  This module deliberately does
not guess missing metadata and never opens a source recording for writing.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator, Sequence
from urllib.parse import unquote, urljoin, urlparse


CAMRAS_INDEX_URL = "https://data.camras.nl/satnogs/"
CAMRAS_SIGMF_DATATYPE = "ci16_le"
PIPELINE_VERSION = "0.1.0"
BYTES_PER_COMPLEX_SAMPLE = 4

_RAW_FILENAME_RE = re.compile(r"^iq_(\d+)\.raw$")
_SIZE_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*([kmgt]?i?b)?\s*$", re.IGNORECASE
)


class CamrasError(ValueError):
    """Base error for malformed CAMRAS metadata or recordings."""


class CamrasIndexError(CamrasError):
    """Raised when a CAMRAS index row is internally inconsistent."""


class TruncatedC16LEError(CamrasError):
    """Raised when a raw file ends part-way through a complex sample."""


@dataclass(frozen=True)
class CamrasIndexEntry:
    filename: str
    observation_id: int
    recording_url: str
    size_text: str
    size_bytes_estimate: int | None
    start_utc: str
    satellite: str
    status: str
    related_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class SigMFConversionResult:
    data_path: Path
    metadata_path: Path
    sample_count: int
    source_sha256: str
    data_sha512: str


@dataclass(frozen=True)
class _Cell:
    text: str
    links: tuple[str, ...]


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[_Cell, ...]] = []
        self._in_row = False
        self._in_cell = False
        self._cells: list[_Cell] = []
        self._text: list[str] = []
        self._links: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: Sequence[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._in_row = True
            self._cells = []
        elif tag in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._text = []
            self._links = []
        elif tag == "a" and self._in_cell:
            href = dict(attrs).get("href")
            if href:
                self._links.append(href)
        elif tag == "br" and self._in_cell:
            self._text.append(" ")

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            text = " ".join("".join(self._text).split())
            self._cells.append(_Cell(text=text, links=tuple(self._links)))
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._cells:
                self.rows.append(tuple(self._cells))
            self._in_row = False


def parse_size_estimate(size_text: str) -> int | None:
    """Convert a human-readable index size to an approximate byte count.

    CAMRAS publishes rounded sizes, so this value must not be used as an
    integrity check.  Decimal SI units are used to match labels such as MB.
    """

    match = _SIZE_RE.match(size_text.replace("\u00a0", " "))
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "B").upper()
    powers = {
        "B": 0,
        "KB": 1,
        "MB": 2,
        "GB": 3,
        "TB": 4,
        "KIB": 1,
        "MIB": 2,
        "GIB": 3,
        "TIB": 4,
    }
    power = powers.get(unit)
    if power is None:
        return None
    base = 1024 if "I" in unit else 1000
    return round(value * (base**power))


def parse_camras_index(
    html: str | bytes, *, base_url: str = CAMRAS_INDEX_URL
) -> list[CamrasIndexEntry]:
    """Parse the CAMRAS HTML table without making a network request."""

    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="strict")
    parser = _TableParser()
    parser.feed(html)
    entries: list[CamrasIndexEntry] = []
    base = urlparse(base_url)
    if base.scheme != "https" or not base.netloc:
        raise CamrasIndexError("base_url must be an absolute HTTPS URL")

    for cells in parser.rows:
        if len(cells) < 6:
            continue
        filename = cells[0].text.strip()
        raw_link = next(
            (
                link
                for link in cells[0].links
                if _RAW_FILENAME_RE.match(
                    Path(unquote(urlparse(link).path)).name
                )
            ),
            None,
        )
        if raw_link:
            linked_name = Path(unquote(urlparse(raw_link).path)).name
            if not _RAW_FILENAME_RE.match(filename):
                filename = linked_name
            elif linked_name != filename:
                raise CamrasIndexError(
                    f"filename/link mismatch: {filename!r} != {linked_name!r}"
                )

        filename_match = _RAW_FILENAME_RE.match(filename)
        if not filename_match:
            continue
        observation_id = int(filename_match.group(1))
        observation_text = cells[2].text.strip()
        if observation_text:
            try:
                listed_observation_id = int(observation_text)
            except ValueError as exc:
                raise CamrasIndexError(
                    f"invalid observation id {observation_text!r} for {filename}"
                ) from exc
            if listed_observation_id != observation_id:
                raise CamrasIndexError(
                    "observation id mismatch for "
                    f"{filename}: {listed_observation_id} != {observation_id}"
                )

        recording_url = urljoin(base_url, raw_link or filename)
        recording_origin = urlparse(recording_url)
        if (recording_origin.scheme, recording_origin.netloc) != (
            base.scheme,
            base.netloc,
        ):
            raise CamrasIndexError("recording link leaves the CAMRAS origin")
        related_links = tuple(
            urljoin(base_url, link)
            for cell in cells[6:]
            for link in cell.links
        )
        entries.append(
            CamrasIndexEntry(
                filename=filename,
                observation_id=observation_id,
                recording_url=recording_url,
                size_text=cells[1].text.strip(),
                size_bytes_estimate=parse_size_estimate(cells[1].text),
                start_utc=cells[3].text.strip(),
                satellite=cells[4].text.strip(),
                status=cells[5].text.strip().lower(),
                related_urls=related_links,
            )
        )
    if not entries:
        raise CamrasIndexError("CAMRAS index contains no valid recording rows")
    return entries


def c16le_sample_count(path: str | os.PathLike[str]) -> int:
    """Return the number of complete I/Q samples, rejecting truncation."""

    size = Path(path).stat().st_size
    if size == 0:
        raise TruncatedC16LEError(f"{path!s} is empty")
    if size % BYTES_PER_COMPLEX_SAMPLE:
        raise TruncatedC16LEError(
            f"{path!s} has {size} bytes; ci16_le requires a multiple of 4"
        )
    return size // BYTES_PER_COMPLEX_SAMPLE


def iter_c16le_chunks(
    path: str | os.PathLike[str],
    *,
    chunk_samples: int = 65_536,
    normalize: bool = True,
) -> Iterator[tuple[complex, ...]]:
    """Stream explicit little-endian interleaved int16 I/Q sample chunks."""

    if chunk_samples <= 0:
        raise ValueError("chunk_samples must be positive")
    c16le_sample_count(path)
    scale = 32_768.0 if normalize else 1.0
    chunk_bytes = chunk_samples * BYTES_PER_COMPLEX_SAMPLE
    with Path(path).open("rb") as source:
        while block := source.read(chunk_bytes):
            if len(block) % BYTES_PER_COMPLEX_SAMPLE:
                raise TruncatedC16LEError(
                    f"short final ci16_le sample in {path!s}"
                )
            yield tuple(
                complex(i_value / scale, q_value / scale)
                for i_value, q_value in struct.iter_unpack("<hh", block)
            )


def c16le_rms(
    path: str | os.PathLike[str], *, chunk_samples: int = 65_536
) -> float:
    """Calculate RMS magnitude in a bounded-memory pass over a recording."""

    sum_squared = 0.0
    count = 0
    for chunk in iter_c16le_chunks(
        path, chunk_samples=chunk_samples, normalize=True
    ):
        sum_squared += sum((sample.real**2 + sample.imag**2) for sample in chunk)
        count += len(chunk)
    return math.sqrt(sum_squared / count) if count else 0.0


def _sigmf_paths(output_base: str | os.PathLike[str]) -> tuple[Path, Path]:
    value = str(output_base)
    for suffix in (".sigmf-data", ".sigmf-meta"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return Path(value + ".sigmf-data"), Path(value + ".sigmf-meta")


def _format_start_utc(value: str | datetime) -> str:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CamrasError("start_utc must include an explicit UTC offset")
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def convert_camras_raw_to_sigmf(
    raw_path: str | os.PathLike[str],
    output_base: str | os.PathLike[str],
    *,
    sample_rate_hz: float,
    sample_rate_basis: str,
    doppler_state: str,
    center_frequency_hz: float | None = None,
    start_utc: str | datetime | None = None,
    observation_id: int | None = None,
    overwrite: bool = False,
    confirm_ci16_le: bool = False,
    source_url: str | None = None,
    source_accessed_at: str | datetime | None = None,
    chunk_bytes: int = 1_048_576,
) -> SigMFConversionResult:
    """Copy a CAMRAS raw recording into a new, provenance-rich SigMF pair.

    The input file is opened read-only.  The copied data remain byte-for-byte
    identical; no scaling, filtering, or in-place metadata modification occurs.
    """

    source_path = Path(raw_path)
    if not confirm_ci16_le:
        raise CamrasError(
            "ci16_le must be explicitly confirmed for this source before conversion"
        )
    if overwrite:
        raise CamrasError("overwrite is disabled; create a new versioned artifact")
    sample_count = c16le_sample_count(source_path)
    if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise CamrasError("sample_rate_hz must be finite and positive")
    if not sample_rate_basis.strip():
        raise CamrasError("sample_rate_basis must be non-empty")
    if doppler_state not in {"pre_correction", "post_correction", "unknown"}:
        raise CamrasError(
            "doppler_state must be pre_correction, post_correction, or unknown"
        )
    if center_frequency_hz is not None and (
        not math.isfinite(center_frequency_hz) or center_frequency_hz <= 0
    ):
        raise CamrasError("center_frequency_hz must be finite and positive")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")

    data_path, metadata_path = _sigmf_paths(output_base)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() in {data_path.resolve(), metadata_path.resolve()}:
        raise CamrasError("SigMF output must not replace the raw source")
    if data_path.exists() or metadata_path.exists():
        raise FileExistsError("SigMF destination already exists")

    before = source_path.stat()
    source_sha256 = hashlib.sha256()
    data_sha512 = hashlib.sha512()
    data_tmp: Path | None = None
    metadata_tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=data_path.parent, prefix=".sigmf-data-", delete=False
        ) as destination, source_path.open("rb") as source:
            data_tmp = Path(destination.name)
            while block := source.read(chunk_bytes):
                source_sha256.update(block)
                data_sha512.update(block)
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())

        after = source_path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise CamrasError("raw source changed during conversion")

        accessed_at = _format_start_utc(
            source_accessed_at or datetime.now(timezone.utc)
        )
        effective_source_url = source_url
        if effective_source_url is None:
            effective_source_url = (
                urljoin(CAMRAS_INDEX_URL, source_path.name)
                if _RAW_FILENAME_RE.match(source_path.name)
                else source_path.resolve().as_uri()
            )
        transform_document = json.dumps(
            {
                "datatype": CAMRAS_SIGMF_DATATYPE,
                "sample_rate_hz": sample_rate_hz,
                "sample_rate_basis": sample_rate_basis,
                "doppler_state": doppler_state,
                "center_frequency_hz": center_frequency_hz,
                "start_utc": _format_start_utc(start_utc) if start_utc else None,
                "observation_id": observation_id,
                "pipeline_version": PIPELINE_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        global_metadata: dict[str, object] = {
            "core:datatype": CAMRAS_SIGMF_DATATYPE,
            "core:sample_rate": sample_rate_hz,
            "core:version": "1.0.0",
            "core:recorder": "CAMRAS Dwingeloo Radio Telescope",
            "core:dataset": data_path.name,
            "core:sha512": data_sha512.hexdigest(),
            "telemetry_yield:source_path": source_path.name,
            "telemetry_yield:source_url": effective_source_url,
            "telemetry_yield:source_accessed_at": accessed_at,
            "telemetry_yield:source_size_bytes": before.st_size,
            "telemetry_yield:source_sha256": source_sha256.hexdigest(),
            "telemetry_yield:source_immutable": True,
            "telemetry_yield:datatype_verified": True,
            "telemetry_yield:sample_rate_basis": sample_rate_basis,
            "telemetry_yield:doppler_state": doppler_state,
            "telemetry_yield:pipeline_version": PIPELINE_VERSION,
            "telemetry_yield:transform_config_hash": hashlib.sha256(
                transform_document
            ).hexdigest(),
        }
        if observation_id is not None:
            global_metadata["telemetry_yield:observation_id"] = observation_id
        capture: dict[str, object] = {"core:sample_start": 0}
        if center_frequency_hz is not None:
            capture["core:frequency"] = center_frequency_hz
        if start_utc is not None:
            capture["core:datetime"] = _format_start_utc(start_utc)
        metadata = {
            "global": global_metadata,
            "captures": [capture],
            "annotations": [],
        }
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=metadata_path.parent,
            prefix=".sigmf-meta-",
            delete=False,
        ) as metadata_file:
            metadata_tmp = Path(metadata_file.name)
            json.dump(metadata, metadata_file, indent=2, sort_keys=True)
            metadata_file.write("\n")
            metadata_file.flush()
            os.fsync(metadata_file.fileno())

        published_data = False
        try:
            os.replace(data_tmp, data_path)
            data_tmp = None
            published_data = True
            os.replace(metadata_tmp, metadata_path)
            metadata_tmp = None
        except Exception:
            if published_data:
                data_path.unlink(missing_ok=True)
            raise
    finally:
        for temporary in (data_tmp, metadata_tmp):
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    return SigMFConversionResult(
        data_path=data_path,
        metadata_path=metadata_path,
        sample_count=sample_count,
        source_sha256=source_sha256.hexdigest(),
        data_sha512=data_sha512.hexdigest(),
    )
