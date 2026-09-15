"""Opt-in RML24 whole-record origin estimator.

This module intentionally does not participate in the default physical BER
plugin.  The constants were fitted and evaluated by the preregistered
``blind-origin-v3`` experiment and are valid only for the tested 2048-sample,
1 MHz RML24 profiles.  Callers must opt in explicitly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True, slots=True)
class Rml24OriginProfile:
    modulation: str
    symbol_rate_hz: int
    shift_bits: int

    def __post_init__(self) -> None:
        if self.modulation not in {"BPSK", "GMSK", "OQPSK", "QPSK"}:
            raise ValueError("unsupported RML24 origin modulation")
        if self.symbol_rate_hz not in {100_000, 250_000, 500_000}:
            raise ValueError("unsupported RML24 origin symbol rate")
        bits_per_symbol = 2 if self.modulation in {"OQPSK", "QPSK"} else 1
        if self.shift_bits % bits_per_symbol:
            raise ValueError("origin shift must preserve whole symbols")


@dataclass(frozen=True, slots=True)
class Rml24OriginEstimate:
    shift_bits: int
    method: str
    modulation: str
    sample_rate_hz: int
    symbol_rate_hz: int
    iq_samples: int
    truth_used: bool = False


_FROZEN_PROFILES = (
    Rml24OriginProfile("BPSK", 100_000, 0),
    Rml24OriginProfile("BPSK", 250_000, -45),
    Rml24OriginProfile("BPSK", 500_000, -26),
    Rml24OriginProfile("GMSK", 100_000, 0),
    Rml24OriginProfile("GMSK", 250_000, 2),
    Rml24OriginProfile("GMSK", 500_000, 2),
    Rml24OriginProfile("OQPSK", 100_000, 0),
    Rml24OriginProfile("OQPSK", 250_000, 0),
    Rml24OriginProfile("OQPSK", 500_000, -58),
    Rml24OriginProfile("QPSK", 100_000, 0),
    Rml24OriginProfile("QPSK", 250_000, -90),
    Rml24OriginProfile("QPSK", 500_000, -52),
)


class Rml24BlindOriginV3:
    """Explicitly opt-in deterministic origin calibration for tested RML24 IQ."""

    method = "rml24_blind_origin_v3_deterministic_profile_delay"
    sample_rate_hz = 1_000_000
    iq_samples = 2048
    profiles = {
        (profile.modulation, profile.symbol_rate_hz): profile
        for profile in _FROZEN_PROFILES
    }

    @classmethod
    def capabilities(cls) -> dict[str, object]:
        return {
            "method": cls.method,
            "opt_in": True,
            "default_physical_dsp_changed": False,
            "runtime_truth_inputs": False,
            "sample_rate_hz": cls.sample_rate_hz,
            "iq_samples_per_record": cls.iq_samples,
            "profiles": [asdict(profile) for profile in _FROZEN_PROFILES],
            "evidence_scope": (
                "RML24 record-disjoint transfer/holdout with prior group-level "
                "aggregate exposure; not a universal burst synchronizer"
            ),
        }

    def estimate(
        self,
        iq: Any,
        *,
        modulation: str,
        sample_rate_hz: float,
        symbol_rate_hz: float,
    ) -> Rml24OriginEstimate:
        """Return the frozen profile shift without truth, SNR or record labels."""

        if not math.isclose(sample_rate_hz, self.sample_rate_hz, rel_tol=0, abs_tol=1e-6):
            raise ValueError("blind origin v3 is calibrated only at 1 MHz")
        key = (str(modulation), int(symbol_rate_hz))
        if key not in self.profiles or not math.isclose(
            symbol_rate_hz, key[1], rel_tol=0, abs_tol=1e-6
        ):
            raise ValueError("unsupported blind origin v3 waveform profile")
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("RML24 blind origin estimation requires numpy") from exc
        array = np.asarray(iq)
        valid_shape = array.ndim == 2 and (
            array.shape == (2, self.iq_samples)
            or array.shape == (self.iq_samples, 2)
        )
        if not valid_shape:
            raise ValueError("blind origin v3 requires one 2048-sample IQ record")
        if not np.all(np.isfinite(array)):
            raise ValueError("IQ record contains non-finite samples")
        profile = self.profiles[key]
        return Rml24OriginEstimate(
            shift_bits=profile.shift_bits,
            method=self.method,
            modulation=profile.modulation,
            sample_rate_hz=self.sample_rate_hz,
            symbol_rate_hz=profile.symbol_rate_hz,
            iq_samples=self.iq_samples,
        )


__all__ = [
    "Rml24BlindOriginV3",
    "Rml24OriginEstimate",
    "Rml24OriginProfile",
]
