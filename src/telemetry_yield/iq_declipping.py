"""Deterministic alternating projections for saturated complex int16 IQ.

This is an explicitly separate experimental front end.  It reconstructs a
band-limited complex vector subject to the component-wise constraints implied
by a saturating float-to-int16 conversion.  It must not be described as the
recorded IQ or as an exact inverse of the SatNOGS IQ sink.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class DeclippingMetrics:
    """Diagnostics for one fixed iteration checkpoint."""

    iterations: int
    scalar_count: int
    positive_clipped_scalars: int
    negative_clipped_scalars: int
    clipped_fraction: float
    out_of_band_energy_fraction_before: float
    out_of_band_energy_fraction_after_constraint_projection: float
    maximum_absolute_component: float
    maximum_unclipped_constraint_error: float
    minimum_positive_clip_margin: float
    minimum_negative_clip_margin: float


def _out_of_band_fraction(values, passband) -> float:
    import numpy as np

    spectrum = np.fft.fft(values)
    energy = np.abs(spectrum) ** 2
    total = float(energy.sum())
    if total <= 0.0:
        return 0.0
    return float(energy[~passband].sum() / total)


def projected_bandlimited_declipping(
    components: Sequence[Sequence[int]],
    *,
    sample_rate_hz: float = 57_600.0,
    bandlimit_hz: float = 12_000.0,
    iteration_checkpoints: Sequence[int] = (5, 10, 20),
):
    """Return constrained reconstructions at fixed alternating-projection steps.

    ``components`` is an ``N x 2`` array of signed int16 I/Q values.  At each
    iteration the complex vector is projected onto the FFT passband and then
    onto the exact observation constraints:

    * non-endpoint components equal the recorded scalar;
    * ``+32767`` components remain greater than or equal to ``+32767``;
    * ``-32768`` components remain less than or equal to ``-32768``.

    The returned checkpoint arrays are float64 component pairs after the
    constraint projection, so every recorded inequality is satisfied exactly.
    """

    import numpy as np

    observed = np.asarray(components)
    if observed.ndim != 2 or observed.shape[1] != 2 or observed.shape[0] < 256:
        raise ValueError("components must have shape (N, 2) with N >= 256")
    if observed.dtype.kind not in "iu":
        raise ValueError("components must contain integer observations")
    if observed.min() < -32_768 or observed.max() > 32_767:
        raise ValueError("components are outside signed int16 range")
    if sample_rate_hz <= 0 or bandlimit_hz <= 0:
        raise ValueError("sample rate and bandlimit must be positive")
    if bandlimit_hz >= sample_rate_hz / 2:
        raise ValueError("bandlimit must be below Nyquist")
    checkpoints = tuple(sorted({int(value) for value in iteration_checkpoints}))
    if not checkpoints or checkpoints[0] < 1:
        raise ValueError("iteration checkpoints must be positive")

    observed = observed.astype(np.float64, copy=False)
    positive = observed == 32_767.0
    negative = observed == -32_768.0
    unclipped = ~(positive | negative)
    frequencies = np.fft.fftfreq(observed.shape[0], d=1.0 / sample_rate_hz)
    passband = np.abs(frequencies) <= bandlimit_hz
    state = observed[:, 0] + 1j * observed[:, 1]
    before_oob = _out_of_band_fraction(state, passband)
    results = {}

    for iteration in range(1, checkpoints[-1] + 1):
        spectrum = np.fft.fft(state)
        spectrum[~passband] = 0.0
        bandlimited = np.fft.ifft(spectrum)
        projected = np.column_stack((bandlimited.real, bandlimited.imag))
        projected[unclipped] = observed[unclipped]
        projected[positive] = np.maximum(projected[positive], 32_767.0)
        projected[negative] = np.minimum(projected[negative], -32_768.0)
        state = projected[:, 0] + 1j * projected[:, 1]

        if iteration in checkpoints:
            reconstruction = np.asarray(projected, dtype=np.float64).copy()
            positive_margin = (
                float(np.min(reconstruction[positive] - 32_767.0))
                if np.any(positive)
                else 0.0
            )
            negative_margin = (
                float(np.min(-32_768.0 - reconstruction[negative]))
                if np.any(negative)
                else 0.0
            )
            metrics = DeclippingMetrics(
                iterations=iteration,
                scalar_count=int(observed.size),
                positive_clipped_scalars=int(np.count_nonzero(positive)),
                negative_clipped_scalars=int(np.count_nonzero(negative)),
                clipped_fraction=float(np.mean(positive | negative)),
                out_of_band_energy_fraction_before=before_oob,
                out_of_band_energy_fraction_after_constraint_projection=(
                    _out_of_band_fraction(state, passband)
                ),
                maximum_absolute_component=float(np.max(np.abs(reconstruction))),
                maximum_unclipped_constraint_error=(
                    float(np.max(np.abs(reconstruction[unclipped] - observed[unclipped])))
                    if np.any(unclipped)
                    else 0.0
                ),
                minimum_positive_clip_margin=positive_margin,
                minimum_negative_clip_margin=negative_margin,
            )
            results[iteration] = (reconstruction, metrics)
    return results
