"""Explicit real-audio entry points for the native generic receiver.

The file container does not identify the signal representation.  In particular,
SatNOGS FSK and AFSK OGG recordings can contain *already FM-demodulated* audio.
Such audio must not undergo another FM discriminator.  A separate, explicitly
selected USB real-passband path forms an analytic audio signal with a Hilbert
transform.  That mathematical representation does not restore original RF IQ,
lost bandwidth, or information removed by lossy audio compression.

This module accepts bounded mono PCM windows, not files.  Acquisition, channel
selection, file decoding and overlapping-window aggregation belong to callers.
Both paths reuse the existing generic receiver's clock bank and strict protocol
validation.  No external satellite decoder is called.
"""

from __future__ import annotations

from dataclasses import replace
import math
from typing import Literal, Sequence

import numpy as np

from .afsk1200_plugin import _moving_tone_energy
from .generic_receiver import (
    BINARY_SOFT_SYMBOLS,
    DemodulatedSignal,
    FrameCandidate,
    GenericReceiver,
    ReceiverHypothesis,
    ReceiverResult,
    WaveformHypothesis,
    default_receiver,
)


AudioRepresentation = Literal["fm_demodulated", "usb_real_passband"]
MAX_PCM_WINDOW_SAMPLES = 4_194_304


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value, (int, float, np.integer, np.floating)
    ):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _pcm_input(pcm: Sequence[float], sample_rate_hz: float) -> tuple[np.ndarray, int]:
    rate = _finite_number(sample_rate_hz, "sample_rate_hz")
    if rate <= 0 or not rate.is_integer():
        raise ValueError("sample_rate_hz must be a positive integer")
    samples = np.asarray(pcm)
    if samples.ndim != 1 or samples.dtype.kind not in "iuf":
        raise ValueError("PCM must be a one-dimensional real numeric mono array")
    if not 8_192 <= samples.size <= MAX_PCM_WINDOW_SAMPLES:
        raise ValueError(
            f"PCM window must contain 8192..{MAX_PCM_WINDOW_SAMPLES} samples"
        )
    if not np.all(np.isfinite(samples)):
        raise ValueError("PCM samples must be finite")
    # Mono PCM need not have an even sample count: it is not interleaved I/Q.
    return np.asarray(samples, dtype=np.float64), int(rate)


def _normalise(values: np.ndarray) -> np.ndarray:
    centered = values - float(np.median(values))
    scale = float(np.quantile(np.abs(centered), 0.90))
    if not math.isfinite(scale) or scale <= 1e-12:
        raise ValueError("degenerate real-audio symbol stream")
    return centered / scale


def _pcm_fsk(
    pcm: np.ndarray, sample_rate_hz: int, waveform: WaveformHypothesis
) -> DemodulatedSignal:
    """Condition discriminator audio; do not differentiate its phase again.

    Uses the same 115-tap post-discriminator filter, 512-symbol moving DC
    blocker and quantile normalization as the native phase-first FSK frontend.
    The representation boundary is after RF phase discrimination, not before.
    """

    from scipy.signal import firwin, lfilter

    cutoff = _finite_number(
        waveform.parameters.get(
            "post_discriminator_cutoff_hz", 0.75 * waveform.symbol_rate
        ),
        "post_discriminator_cutoff_hz",
    )
    output_rate = sample_rate_hz / waveform.decimation
    if not 0 < cutoff < min(sample_rate_hz, output_rate) / 2:
        raise ValueError("post-discriminator cutoff must be below output Nyquist")
    taps = firwin(115, cutoff=cutoff, fs=sample_rate_hz, window="hamming")
    filtered = lfilter(taps, [1.0], pcm)[114 :: waveform.decimation]
    samples_per_symbol = output_rate / waveform.symbol_rate
    blocker_length = max(32, int(round(512 * samples_per_symbol)))
    if filtered.size <= blocker_length:
        raise ValueError("PCM window is too short for the adaptive DC blocker")
    cumulative = np.concatenate((np.zeros(1), np.cumsum(filtered, dtype=np.float64)))
    trend = (cumulative[blocker_length:] - cumulative[:-blocker_length]) / blocker_length
    soft = _normalise(filtered[blocker_length - 1 :] - trend)
    return DemodulatedSignal(
        samples=tuple(float(value) for value in soft),
        samples_per_symbol=samples_per_symbol,
        metrics={
            "input_representation": "fm_demodulated",
            "frontend": "pcm_fsk_post_discriminator_filter_dc_block",
            "input_real_samples": pcm.size,
            "output_real_samples": soft.size,
            "post_discriminator_cutoff_hz": cutoff,
        },
    )


def _pcm_bell202(
    pcm: np.ndarray, sample_rate_hz: int, waveform: WaveformHypothesis
) -> DemodulatedSignal:
    """Apply the native Bell-202 tone-energy frontend to recovered FM audio."""

    from scipy.signal import firwin, lfilter

    output_rate = sample_rate_hz / waveform.decimation
    if not output_rate.is_integer():
        raise ValueError("Bell-202 decimation must divide the PCM sample rate")
    mark = _finite_number(waveform.parameters.get("mark_hz", 1_200.0), "mark_hz")
    space = _finite_number(waveform.parameters.get("space_hz", 2_200.0), "space_hz")
    if not (0 < mark < output_rate / 2 and 0 < space < output_rate / 2):
        raise ValueError("Bell-202 tones must be positive and below output Nyquist")
    if mark == space:
        raise ValueError("Bell-202 tones must differ")
    if sample_rate_hz <= 6_000 or output_rate <= 6_000:
        raise ValueError("Bell-202 frontend requires more than 6000 samples/s")
    taps = firwin(
        129, (500.0, 3_000.0), pass_zero=False, fs=sample_rate_hz, window="hamming"
    )
    audio = lfilter(taps, [1.0], pcm)[128 :: waveform.decimation]
    if audio.size < 2_048:
        raise ValueError("not enough Bell-202 audio after filtering")
    audio = _normalise(audio)
    tone_length = max(4, int(round(output_rate / waveform.symbol_rate)))
    mark_energy = _moving_tone_energy(audio, mark, int(output_rate), tone_length)
    space_energy = _moving_tone_energy(audio, space, int(output_rate), tone_length)
    soft = _normalise((mark_energy - space_energy)[2 * tone_length :])
    return DemodulatedSignal(
        samples=tuple(float(value) for value in soft),
        samples_per_symbol=output_rate / waveform.symbol_rate,
        metrics={
            "input_representation": "fm_demodulated",
            "frontend": "pcm_noncoherent_bell202_energy",
            "input_real_samples": pcm.size,
            "output_real_samples": soft.size,
            "mark_hz": mark,
            "space_hz": space,
        },
    )


def decode_audio_pcm(
    pcm: Sequence[float],
    *,
    sample_rate_hz: float,
    representation: AudioRepresentation,
    plan: Sequence[ReceiverHypothesis],
    carrier_hz: float | None = None,
    receiver: GenericReceiver | None = None,
) -> ReceiverResult:
    """Decode a bounded PCM window with an explicit physical representation.

    ``fm_demodulated`` requires waveform IDs ``pcm_fsk`` or ``pcm_bell202``;
    their parameters describe real conditioning and timing, not RF demodulation.
    ``usb_real_passband`` requires an explicit audio ``carrier_hz`` and uses
    registered IQ demodulators after analytic-audio formation/downconversion.
    Carrier metadata is rejected for FM-demodulated audio.  Invalid contracts
    raise ``ValueError``; individual signal-dependent decode failures are kept
    in ``ReceiverResult.failures``.  Constant/silent PCM is a normal zero yield.

    Returned AX.25 payloads retain their verified two-byte FCS, exactly as in
    ``GenericReceiver``.  The caller must explicitly remove it when comparing
    against archive PDUs without FCS.  Frame provenance includes representation.
    """

    values, rate = _pcm_input(pcm, sample_rate_hz)
    if representation not in ("fm_demodulated", "usb_real_passband"):
        raise ValueError("an explicit supported audio representation is required")
    if not plan:
        raise ValueError("plan must not be empty")
    receiver = default_receiver() if receiver is None else receiver
    capabilities = receiver.capabilities
    names: set[tuple[str, str]] = set()
    for item in plan:
        waveform = item.waveform
        symbol_rate = _finite_number(waveform.symbol_rate, "symbol_rate")
        if (
            symbol_rate <= 0
            or isinstance(waveform.decimation, bool)
            or not isinstance(waveform.decimation, int)
            or waveform.decimation < 1
            or rate / (waveform.decimation * symbol_rate) <= 1.1
        ):
            raise ValueError("waveform must retain more than 1.1 samples per symbol")
        for name, minimum in (("phase_bins", 4), ("top_timing_hypotheses", 1)):
            budget = getattr(waveform, name)
            if isinstance(budget, bool) or not isinstance(budget, int) or budget < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        samples_per_symbol = rate / (waveform.decimation * symbol_rate)
        for value in waveform.rate_errors_ppm:
            error = _finite_number(value, "rate_error_ppm")
            if samples_per_symbol * (1 + error * 1e-6) <= 1:
                raise ValueError("rate errors must retain more than one sample per symbol")
        if item.protocol_id not in capabilities.protocols:
            raise ValueError(f"unknown protocol adapter: {item.protocol_id}")
        key = (waveform.hypothesis_id, item.protocol_id)
        if key in names:
            raise ValueError("duplicate waveform/protocol hypothesis")
        names.add(key)
        if representation == "fm_demodulated":
            allowed = {
                "pcm_fsk": ("fsk", "gfsk", "gmsk"),
                "pcm_bell202": ("afsk",),
            }
            if waveform.demodulator_id not in allowed:
                raise ValueError(
                    "FM audio requires pcm_fsk or pcm_bell202; not an IQ demodulator"
                )
            if (
                waveform.modulation_family is not None
                and waveform.modulation_family.casefold()
                not in allowed[waveform.demodulator_id]
            ):
                raise ValueError("waveform modulation family is incompatible with PCM frontend")
            protocol = capabilities.protocols[item.protocol_id]
            if BINARY_SOFT_SYMBOLS not in protocol.accepted_symbol_kinds:
                raise ValueError("PCM frontend emits binary soft symbols")
            if protocol.required_demodulator_features:
                raise ValueError("PCM frontend does not advertise required protocol features")
            output_rate = rate / waveform.decimation
            if waveform.demodulator_id == "pcm_fsk":
                cutoff = _finite_number(
                    waveform.parameters.get(
                        "post_discriminator_cutoff_hz", 0.75 * symbol_rate
                    ),
                    "post_discriminator_cutoff_hz",
                )
                if not 0 < cutoff < output_rate / 2:
                    raise ValueError("post-discriminator cutoff must be below output Nyquist")
            else:
                if not output_rate.is_integer() or output_rate <= 6_000:
                    raise ValueError("Bell-202 decimation requires integer rate above 6000 Hz")
                tones = tuple(
                    _finite_number(waveform.parameters.get(name, default), name)
                    for name, default in (("mark_hz", 1_200.0), ("space_hz", 2_200.0))
                )
                if tones[0] == tones[1] or any(
                    not 500 < tone < min(3_000, output_rate / 2) for tone in tones
                ):
                    raise ValueError("tones must differ and lie inside the 500..3000 Hz passband")
    if representation == "fm_demodulated" and carrier_hz is not None:
        raise ValueError("carrier_hz is not meaningful for already FM-demodulated PCM")
    if representation == "usb_real_passband":
        carrier = _finite_number(carrier_hz, "carrier_hz")
        if not 0 < carrier < rate / 2:
            raise ValueError("USB audio carrier must be positive and below Nyquist")
        receiver.validate_plan(plan)
    if float(np.ptp(values)) <= 1e-12:
        return ReceiverResult((), len(plan), 0, ())
    provenance_plan = tuple(
        replace(
            item,
            waveform=replace(
                item.waveform,
                hypothesis_id=f"{representation}:{item.waveform.hypothesis_id}",
            ),
        )
        for item in plan
    )
    if representation == "usb_real_passband":
        from scipy.signal import hilbert

        analytic_audio = hilbert(values - float(np.mean(values)))
        indices = np.arange(values.size, dtype=np.float64)
        analytic_audio *= np.exp(-2j * np.pi * carrier * indices / rate)
        return receiver.decode_iq(
            analytic_audio, sample_rate_hz=rate, plan=provenance_plan
        )
    frames: list[FrameCandidate] = []
    seen: set[tuple[bytes, str]] = set()
    failures: list[str] = []
    timing_attempts = 0
    for item in provenance_plan:
        try:
            frontend = (
                _pcm_fsk if item.waveform.demodulator_id == "pcm_fsk" else _pcm_bell202
            )
            signal = frontend(values, rate, item.waveform)
            result = receiver.decode_demodulated(
                signal, waveform=item.waveform, protocol_id=item.protocol_id
            )
        except (ValueError, KeyError) as error:
            failures.append(f"{item.waveform.hypothesis_id}: {error}")
            continue
        timing_attempts += result.attempted_timing_hypotheses
        for frame in result.frames:
            key = (frame.payload, frame.protocol_id)
            if key not in seen:
                seen.add(key)
                frames.append(frame)
    return ReceiverResult(tuple(frames), len(plan), timing_attempts, tuple(failures))
