"""Generic narrowband/CW candidate extraction without mission trust claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Protocol


MORSE_TABLE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z", "-----": "0", ".----": "1", "..---": "2",
    "...--": "3", "....-": "4", ".....": "5", "-....": "6",
    "--...": "7", "---..": "8", "----.": "9",
}


@dataclass(frozen=True, slots=True)
class CandidateExtractorCapabilities:
    plugin_id: str
    waveform_families: tuple[str, ...]
    output_kind: str
    validation_level: str


@dataclass(frozen=True, slots=True)
class CwProbeConfig:
    sample_rate_hz: float = 57_600.0
    window_seconds: float = 1.0
    preview_fft_size: int = 8192
    preview_slices_per_window: int = 4
    carrier_track_candidates: int = 12
    carrier_track_transition_scale_hz: float = 200.0
    carrier_half_bandwidth_hz: float = 250.0
    envelope_bin_seconds: float = 0.01
    padding_windows: int = 1
    active_power_ratio: float = 1.5
    active_peak_ratio: float = 20.0
    wpm_min: int = 5
    wpm_max: int = 40

    def __post_init__(self) -> None:
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be finite and positive")
        if not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError("window_seconds must be finite and positive")
        if self.preview_fft_size <= 0 or self.preview_fft_size & (self.preview_fft_size - 1):
            raise ValueError("preview_fft_size must be a positive power of two")
        if (
            self.preview_slices_per_window <= 0
            or self.carrier_track_candidates <= 0
            or self.padding_windows < 0
        ):
            raise ValueError("slice count must be positive and padding nonnegative")
        if self.carrier_track_transition_scale_hz <= 0:
            raise ValueError("carrier track transition scale must be positive")
        if self.carrier_half_bandwidth_hz <= 0 or self.envelope_bin_seconds <= 0:
            raise ValueError("bandwidth and envelope bin must be positive")
        if not 5 <= self.wpm_min <= self.wpm_max <= 40:
            raise ValueError("Morse WPM bank must lie within 5..40")


@dataclass(frozen=True, slots=True)
class MorseCandidate:
    text: str
    wpm: int
    timing_score: float
    valid_character_fraction: float
    decoded_character_count: int
    region_start_seconds: float
    region_end_seconds: float
    repeat_count: int
    candidate_validation: str = "pending"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CwProbeResult:
    classification: str
    active_window_count: int
    selected_window_count_with_padding: int
    analyzed_region_count: int
    median_carrier_offset_hz: float | None
    carrier_offset_p10_hz: float | None
    carrier_offset_p90_hz: float | None
    median_peak_to_noise_ratio: float
    envelope_low_center: float | None
    envelope_high_center: float | None
    envelope_high_to_low_ratio: float | None
    keying_duty_cycle: float | None
    keying_transition_count: int
    keying_evidence_score: float
    candidates: tuple[MorseCandidate, ...]
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class NarrowbandCandidateExtractor(Protocol):
    @property
    def capabilities(self) -> CandidateExtractorCapabilities: ...

    def extract(self, path: str | Path) -> CwProbeResult: ...


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("CW candidate extraction requires numpy") from exc
    return np


def _lower_envelope(values, fraction: float, np) -> float:
    count = max(1, math.ceil(values.size * fraction))
    return float(np.median(np.partition(values, count - 1)[:count]))


def _regions(mask) -> list[tuple[int, int]]:
    output = []
    start = None
    for index, value in enumerate(mask):
        if value and start is None:
            start = index
        if start is not None and (not value or index == len(mask) - 1):
            end = index + 1 if value and index == len(mask) - 1 else index
            output.append((start, end))
            start = None
    return output


def _kmeans_two(values, np) -> tuple[float, float, object]:
    centers = np.quantile(values, [0.20, 0.80]).astype(np.float64)
    labels = np.zeros(values.size, dtype=np.int8)
    for _ in range(24):
        labels = np.argmin(np.abs(values[:, None] - centers[None, :]), axis=1)
        updated = np.asarray(
            [
                np.mean(values[labels == index])
                if np.any(labels == index)
                else centers[index]
                for index in range(2)
            ]
        )
        if np.allclose(updated, centers):
            break
        centers = updated
    order = np.argsort(centers)
    low_index, high_index = int(order[0]), int(order[1])
    return float(centers[low_index]), float(centers[high_index]), labels == high_index


def _runs(bits, bin_seconds: float) -> list[tuple[bool, float]]:
    if len(bits) == 0:
        return []
    output = []
    value = bool(bits[0])
    count = 1
    for item in bits[1:]:
        item_value = bool(item)
        if item_value == value:
            count += 1
        else:
            output.append((value, count * bin_seconds))
            value = item_value
            count = 1
    output.append((value, count * bin_seconds))
    return output


def _decode_runs(
    runs: list[tuple[bool, float]], *, wpm: int
) -> tuple[str, float, float, int]:
    unit = 1.2 / wpm
    symbols: list[str] = []
    words: list[str] = []
    current_word: list[str] = []
    timing_scores: list[float] = []

    def finish_character() -> None:
        if not symbols:
            return
        code = "".join(symbols)
        current_word.append(MORSE_TABLE.get(code, "?"))
        symbols.clear()

    for index, (on, duration) in enumerate(runs):
        ratio = duration / unit
        if on:
            dot_error = abs(ratio - 1.0)
            dash_error = abs(ratio - 3.0) / 3.0
            if dot_error <= dash_error:
                symbols.append(".")
                timing_scores.append(math.exp(-dot_error))
            else:
                symbols.append("-")
                timing_scores.append(math.exp(-dash_error))
            continue
        if index == 0 or index == len(runs) - 1:
            continue
        if ratio < 2.0:
            timing_scores.append(math.exp(-abs(ratio - 1.0)))
        elif ratio < 5.0:
            finish_character()
            timing_scores.append(math.exp(-abs(ratio - 3.0) / 3.0))
        else:
            finish_character()
            if current_word:
                words.append("".join(current_word))
                current_word.clear()
            timing_scores.append(math.exp(-abs(ratio - 7.0) / 7.0))
    finish_character()
    if current_word:
        words.append("".join(current_word))
    text = " ".join(words)
    characters = [item for item in text if item != " "]
    valid = sum(item != "?" for item in characters)
    valid_fraction = valid / len(characters) if characters else 0.0
    timing = sum(timing_scores) / len(timing_scores) if timing_scores else 0.0
    return text, timing, valid_fraction, len(characters)


@dataclass(frozen=True, slots=True)
class NarrowbandCwExtractor:
    config: CwProbeConfig = CwProbeConfig()

    @property
    def capabilities(self) -> CandidateExtractorCapabilities:
        return CandidateExtractorCapabilities(
            plugin_id="narrowband_cw_candidate_extractor",
            waveform_families=("continuous_carrier", "cw_on_off_keying", "unknown_narrowband"),
            output_kind="untrusted_text_candidates",
            validation_level="pending",
        )

    def extract(self, path: str | Path) -> CwProbeResult:
        np = _require_numpy()
        path_value = Path(path)
        size = path_value.stat().st_size
        if size <= 0 or size % 4:
            raise ValueError("CI16-LE IQ size must be a positive multiple of four")
        selected = self.config
        raw = np.memmap(path_value, dtype="<i2", mode="r").reshape(-1, 2)
        window_samples = round(selected.sample_rate_hz * selected.window_seconds)
        window_count = raw.shape[0] // window_samples
        if window_count == 0 or selected.preview_fft_size > window_samples:
            raise ValueError("recording must contain one complete analysis window")
        preview_window = np.hanning(selected.preview_fft_size).astype(np.float32)
        preview_offsets = np.linspace(
            0,
            window_samples - selected.preview_fft_size,
            num=selected.preview_slices_per_window,
            dtype=np.int64,
        )
        frequencies = np.fft.fftshift(
            np.fft.fftfreq(selected.preview_fft_size, d=1.0 / selected.sample_rate_hz)
        )
        powers = np.empty(window_count, dtype=np.float64)
        candidate_frequencies = []
        candidate_ratios = []
        for index in range(window_count):
            start = index * window_samples
            components = np.asarray(raw[start : start + window_samples], dtype=np.float32)
            powers[index] = float(np.mean(components[:, 0] ** 2 + components[:, 1] ** 2))
            combined_spectrum = np.zeros(selected.preview_fft_size, dtype=np.float64)
            for offset in preview_offsets:
                segment = components[int(offset) : int(offset) + selected.preview_fft_size]
                samples = segment[:, 0] + 1j * segment[:, 1]
                samples -= np.mean(samples)
                spectrum = np.abs(np.fft.fftshift(np.fft.fft(samples * preview_window))) ** 2
                combined_spectrum = np.maximum(combined_spectrum, spectrum)
            floor = float(np.median(combined_spectrum))
            local = np.flatnonzero(
                (combined_spectrum[1:-1] > combined_spectrum[:-2])
                & (combined_spectrum[1:-1] >= combined_spectrum[2:])
            ) + 1
            if local.size == 0:
                local = np.asarray([int(np.argmax(combined_spectrum))])
            keep = min(selected.carrier_track_candidates, local.size)
            strongest = local[
                np.argpartition(combined_spectrum[local], -keep)[-keep:]
            ]
            strongest = strongest[np.argsort(-combined_spectrum[strongest], kind="stable")]
            candidate_frequencies.append(frequencies[strongest].astype(np.float64))
            candidate_ratios.append(
                combined_spectrum[strongest] / max(floor, 1e-20)
            )

        # Viterbi-like smooth carrier track.  A persistent Doppler ridge wins
        # over unrelated per-window noise peaks and other intermittent tones.
        previous_scores = np.log1p(candidate_ratios[0])
        backpointers = []
        for index in range(1, window_count):
            previous_frequencies = candidate_frequencies[index - 1]
            current_frequencies = candidate_frequencies[index]
            transition = np.abs(
                current_frequencies[:, None] - previous_frequencies[None, :]
            ) / selected.carrier_track_transition_scale_hz
            choices = previous_scores[None, :] - transition
            best_previous = np.argmax(choices, axis=1)
            current_scores = np.log1p(candidate_ratios[index]) + choices[
                np.arange(current_frequencies.size), best_previous
            ]
            backpointers.append(best_previous)
            previous_scores = current_scores
        states = [int(np.argmax(previous_scores))]
        for pointer in reversed(backpointers):
            states.append(int(pointer[states[-1]]))
        states.reverse()
        carrier_offsets = np.asarray(
            [candidate_frequencies[index][state] for index, state in enumerate(states)],
            dtype=np.float64,
        )
        peak_ratios = np.asarray(
            [candidate_ratios[index][state] for index, state in enumerate(states)],
            dtype=np.float64,
        )

        noise_power = _lower_envelope(powers, 0.20, np)
        active = (powers >= noise_power * selected.active_power_ratio) | (
            peak_ratios >= selected.active_peak_ratio
        )
        active_count = int(np.count_nonzero(active))
        padded = active.copy()
        for shift in range(1, selected.padding_windows + 1):
            padded[shift:] |= active[:-shift]
            padded[:-shift] |= active[shift:]
        regions = _regions(padded)
        if not regions:
            return CwProbeResult(
                classification="unknown",
                active_window_count=0,
                selected_window_count_with_padding=0,
                analyzed_region_count=0,
                median_carrier_offset_hz=None,
                carrier_offset_p10_hz=None,
                carrier_offset_p90_hz=None,
                median_peak_to_noise_ratio=float(np.median(peak_ratios)),
                envelope_low_center=None,
                envelope_high_center=None,
                envelope_high_to_low_ratio=None,
                keying_duty_cycle=None,
                keying_transition_count=0,
                keying_evidence_score=0.0,
                candidates=(),
                evidence=("no 1 s window passed the high-recall energy/spectral gate",),
            )

        # Padding-only windows inherit the nearest active carrier estimate.
        active_indices = np.flatnonzero(active)
        for index in np.flatnonzero(padded & ~active):
            nearest = int(active_indices[np.argmin(np.abs(active_indices - index))])
            carrier_offsets[index] = carrier_offsets[nearest]

        bin_samples = max(1, round(selected.envelope_bin_seconds * selected.sample_rate_hz))
        region_envelopes = []
        region_times = []
        used_offsets = []
        for region_start, region_end in regions:
            values = []
            for index in range(region_start, region_end):
                start = index * window_samples
                components = np.asarray(raw[start : start + window_samples], dtype=np.float32)
                samples = components[:, 0] + 1j * components[:, 1]
                spectrum = np.fft.fft(samples)
                bins = np.fft.fftfreq(window_samples, d=1.0 / selected.sample_rate_hz)
                center = carrier_offsets[index]
                mask = np.abs(bins - center) <= selected.carrier_half_bandwidth_hz
                filtered = np.fft.ifft(spectrum * mask)
                complete = filtered.size // bin_samples
                envelope = np.mean(
                    np.abs(filtered[: complete * bin_samples]).reshape(complete, bin_samples),
                    axis=1,
                )
                values.append(envelope)
                used_offsets.append(center)
            region_envelopes.append(np.concatenate(values))
            region_times.append(
                (region_start * selected.window_seconds, region_end * selected.window_seconds)
            )

        all_envelope = np.concatenate(region_envelopes)
        low, high, high_mask = _kmeans_two(all_envelope, np)
        ratio = high / max(low, 1e-12)
        duty = float(np.mean(high_mask))
        transitions = int(np.count_nonzero(high_mask[1:] != high_mask[:-1]))
        within = []
        for center, label in ((low, ~high_mask), (high, high_mask)):
            if np.any(label):
                within.extend(abs(all_envelope[label] - center))
        scatter = float(np.median(within)) if within else 0.0
        separation = (high - low) / max(scatter * 1.4826, 1e-12)
        evidence_score = min(1.0, max(0.0, (ratio - 1.2) / 2.5)) * min(
            1.0, separation / 6.0
        )
        strong_peak = float(np.median(peak_ratios[active])) if active_count else 0.0
        reliable_track = strong_peak >= selected.active_peak_ratio * 0.60
        keyed = bool(
            ratio >= 1.8
            and separation >= 3.0
            and 0.03 <= duty <= 0.90
            and transitions >= 4
            and evidence_score >= 0.50
            and reliable_track
        )

        raw_candidates = []
        if keyed:
            threshold = (low + high) / 2.0
            for envelope, (start, end) in zip(region_envelopes, region_times, strict=True):
                bits = envelope >= threshold
                runs = _runs(bits, selected.envelope_bin_seconds)
                scored = []
                for wpm in range(selected.wpm_min, selected.wpm_max + 1):
                    text, timing, valid_fraction, character_count = _decode_runs(runs, wpm=wpm)
                    score = timing * (0.25 + 0.75 * valid_fraction)
                    if (
                        character_count >= 2
                        and text
                        and score >= 0.75
                        and valid_fraction >= 0.80
                    ):
                        scored.append((score, valid_fraction, character_count, wpm, text))
                scored.sort(key=lambda item: (-item[0], -item[2], item[3], item[4]))
                seen_text = set()
                for score, valid_fraction, character_count, wpm, text in scored:
                    if text in seen_text:
                        continue
                    seen_text.add(text)
                    raw_candidates.append(
                        MorseCandidate(
                            text=text,
                            wpm=wpm,
                            timing_score=score,
                            valid_character_fraction=valid_fraction,
                            decoded_character_count=character_count,
                            region_start_seconds=start,
                            region_end_seconds=end,
                            repeat_count=1,
                        )
                    )
                    if len(seen_text) >= 3:
                        break

        repetitions: dict[str, int] = {}
        for item in raw_candidates:
            repetitions[item.text] = repetitions.get(item.text, 0) + 1
        candidates = tuple(
            sorted(
                (
                    MorseCandidate(
                        text=item.text,
                        wpm=item.wpm,
                        timing_score=item.timing_score,
                        valid_character_fraction=item.valid_character_fraction,
                        decoded_character_count=item.decoded_character_count,
                        region_start_seconds=item.region_start_seconds,
                        region_end_seconds=item.region_end_seconds,
                        repeat_count=repetitions[item.text],
                    )
                    for item in raw_candidates
                ),
                key=lambda item: (
                    -item.repeat_count,
                    -item.timing_score,
                    -item.decoded_character_count,
                    item.region_start_seconds,
                    item.text,
                ),
            )[:10]
        )
        classification = (
            "keyed" if keyed else "non_keyed" if reliable_track else "unknown"
        )
        offsets = np.asarray(used_offsets, dtype=np.float64)
        return CwProbeResult(
            classification=classification,
            active_window_count=active_count,
            selected_window_count_with_padding=int(np.count_nonzero(padded)),
            analyzed_region_count=len(regions),
            median_carrier_offset_hz=float(np.median(offsets)),
            carrier_offset_p10_hz=float(np.quantile(offsets, 0.10)),
            carrier_offset_p90_hz=float(np.quantile(offsets, 0.90)),
            median_peak_to_noise_ratio=strong_peak,
            envelope_low_center=low,
            envelope_high_center=high,
            envelope_high_to_low_ratio=ratio,
            keying_duty_cycle=duty,
            keying_transition_count=transitions,
            keying_evidence_score=evidence_score,
            candidates=candidates,
            evidence=(
                f"high_to_low_envelope_ratio={ratio:.4g}",
                f"two_level_separation={separation:.4g}",
                f"duty_cycle={duty:.4g}",
                f"transition_count={transitions}",
                f"median_active_peak_to_noise={strong_peak:.4g}",
                "keyed requires ratio>=1.8, separation>=3, duty 0.03..0.90, transitions>=4, evidence>=0.50, reliable carrier track",
            ),
        )
