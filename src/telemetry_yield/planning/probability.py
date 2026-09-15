"""Calibratable reception-probability interface with a safe baseline model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from .models import ProbabilityEstimate

TransmitterState = Literal["confirmed", "expected", "not_transmitting", "unknown"]
EvidenceSource = Literal["local", "satnogs"]


@dataclass(frozen=True, slots=True)
class ReceptionEvidence:
    """One historical outcome with explicit observability semantics.

    A missing SatNOGS decode is not a negative unless the station listened and
    transmission was confirmed or expected.  This prevents selection bias and
    missing uploads from silently becoming decoder failures.
    """

    norad_id: int
    station_id: str | None
    resource_id: str | None
    listened: bool
    transmitter_state: TransmitterState
    decoded: bool | None
    source: EvidenceSource
    weight: float = 1.0
    signal_present: bool | None = None
    transmitter_uuid: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.norad_id, bool)
            or not isinstance(self.norad_id, int)
            or self.norad_id <= 0
        ):
            raise ValueError("norad_id must be positive")
        if not isinstance(self.listened, bool):
            raise ValueError("listened must be boolean")
        for name in ("decoded", "signal_present"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be boolean or null")
        for name in ("station_id", "resource_id", "transmitter_uuid"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{name} must be non-empty when supplied")
        if self.transmitter_state not in {
            "confirmed",
            "expected",
            "not_transmitting",
            "unknown",
        }:
            raise ValueError("invalid transmitter_state")
        if self.source not in {"local", "satnogs"}:
            raise ValueError("invalid evidence source")
        if self.weight <= 0 or not math.isfinite(self.weight):
            raise ValueError("evidence weight must be finite and positive")
        if self.decoded is True and not self.listened:
            raise ValueError("a decoded frame requires a listening observation")
        if self.signal_present is True and not self.listened:
            raise ValueError("a confirmed signal requires a listening observation")


@dataclass(frozen=True, slots=True)
class ReceptionFeatures:
    norad_id: int
    station_id: str
    resource_id: str
    max_elevation_deg: float
    min_range_km: float
    duration_seconds: float
    tle_age_hours: float
    frequency_hz: float | None = None
    modulation: str | None = None
    baud: float | None = None
    transmit_power_dbw: float | None = None
    transmit_antenna_gain_dbi: float | None = None
    receive_antenna_gain_dbi: float | None = None
    system_noise_temperature_k: float | None = None
    atmospheric_attenuation_db: float | None = None
    space_weather_kp: float | None = None
    ionospheric_scintillation_index: float | None = None
    air_temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    surface_pressure_kpa: float | None = None
    wind_speed_m_s: float | None = None
    precipitation_corrected: float | None = None
    required_snr_db: float | None = None
    environment_source: str | None = None
    satnogs_station_id: int | None = None
    transmitter_uuid: str | None = None
    transmitter_status: str | None = None
    rise_azimuth_deg: float | None = None
    set_azimuth_deg: float | None = None
    antenna_type: str | None = None
    antenna_frequency_supported: bool | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.norad_id, bool)
            or not isinstance(self.norad_id, int)
            or self.norad_id <= 0
        ):
            raise ValueError("norad_id must be positive")
        for name in ("station_id", "resource_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        required = {
            "max_elevation_deg": self.max_elevation_deg,
            "min_range_km": self.min_range_km,
            "duration_seconds": self.duration_seconds,
            "tle_age_hours": self.tle_age_hours,
        }
        if any(not math.isfinite(value) for value in required.values()):
            raise ValueError("required reception features must be finite")
        if not -90 <= self.max_elevation_deg <= 90:
            raise ValueError("max_elevation_deg must be in [-90, 90]")
        if self.min_range_km <= 0 or self.duration_seconds <= 0:
            raise ValueError("range and duration must be positive")
        if self.tle_age_hours < 0:
            raise ValueError("tle_age_hours must be non-negative")

        for name in (
            "frequency_hz",
            "baud",
            "transmit_power_dbw",
            "transmit_antenna_gain_dbi",
            "receive_antenna_gain_dbi",
            "system_noise_temperature_k",
            "atmospheric_attenuation_db",
            "space_weather_kp",
            "ionospheric_scintillation_index",
            "air_temperature_c",
            "relative_humidity_percent",
            "surface_pressure_kpa",
            "wind_speed_m_s",
            "precipitation_corrected",
            "required_snr_db",
        ):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite when supplied")
        if self.frequency_hz is not None and self.frequency_hz <= 0:
            raise ValueError("frequency_hz must be positive")
        if self.baud is not None and self.baud <= 0:
            raise ValueError("baud must be positive")
        if (
            self.system_noise_temperature_k is not None
            and self.system_noise_temperature_k <= 0
        ):
            raise ValueError("system_noise_temperature_k must be positive")
        if (
            self.atmospheric_attenuation_db is not None
            and self.atmospheric_attenuation_db < 0
        ):
            raise ValueError("atmospheric_attenuation_db must be non-negative")
        if self.space_weather_kp is not None and not 0 <= self.space_weather_kp <= 9:
            raise ValueError("space_weather_kp must be in [0, 9]")
        if (
            self.ionospheric_scintillation_index is not None
            and self.ionospheric_scintillation_index < 0
        ):
            raise ValueError("ionospheric_scintillation_index must be non-negative")
        if self.relative_humidity_percent is not None and not (
            0 <= self.relative_humidity_percent <= 100
        ):
            raise ValueError("relative_humidity_percent must be in [0, 100]")
        if self.surface_pressure_kpa is not None and self.surface_pressure_kpa <= 0:
            raise ValueError("surface_pressure_kpa must be positive")
        if self.wind_speed_m_s is not None and self.wind_speed_m_s < 0:
            raise ValueError("wind_speed_m_s must be non-negative")
        if self.satnogs_station_id is not None and (
            isinstance(self.satnogs_station_id, bool)
            or not isinstance(self.satnogs_station_id, int)
            or self.satnogs_station_id <= 0
        ):
            raise ValueError("satnogs_station_id must be a positive integer")
        for name in ("rise_azimuth_deg", "set_azimuth_deg"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or not 0 <= value <= 360):
                raise ValueError(f"{name} must be in [0, 360]")
        if self.antenna_frequency_supported is not None and not isinstance(
            self.antenna_frequency_supported, bool
        ):
            raise ValueError("antenna_frequency_supported must be boolean or null")
        if self.modulation is not None:
            modulation = self.modulation.strip()
            if not modulation:
                raise ValueError("modulation must be non-empty when supplied")
            object.__setattr__(self, "modulation", modulation)
        if self.environment_source is not None:
            source = self.environment_source.strip()
            if not source:
                raise ValueError("environment_source must be non-empty when supplied")
            object.__setattr__(self, "environment_source", source)
        for name in ("transmitter_uuid", "transmitter_status", "antenna_type"):
            value = getattr(self, name)
            if value is not None:
                normalized = value.strip()
                if not normalized:
                    raise ValueError(f"{name} must be non-empty when supplied")
                object.__setattr__(self, name, normalized)


class ProbabilityEstimator(Protocol):
    def estimate(
        self,
        features: ReceptionFeatures,
        evidence: Sequence[ReceptionEvidence],
        *,
        transmitter_probability: float,
    ) -> ProbabilityEstimate: ...


def _logit(probability: float) -> float:
    probability = min(1 - 1e-9, max(1e-9, probability))
    return math.log(probability / (1 - probability))


def _sigmoid(value: float) -> float:
    if value >= 0:
        factor = math.exp(-value)
        return 1 / (1 + factor)
    factor = math.exp(value)
    return factor / (1 + factor)


class HierarchicalBetaEstimator:
    """Dependency-free, auditable cold-start estimator.

    It is intentionally a baseline rather than a claim of a trained AI model.
    Historical local and SatNOGS outcomes update separate detectable-signal and
    conditional-decode Beta priors, while known geometry and link-margin
    features supply bounded log-odds adjustments. ``ProbabilityEstimate`` keeps
    the old serialized names ``p_transmit`` and ``p_decode_given_transmit`` for
    compatibility; their precise operational meanings are exposed by the
    ``p_signal_present`` and ``p_decode_given_signal`` properties. Missing
    optional inputs widen uncertainty instead of preventing an estimate.
    """

    model_version = "hierarchical-beta-signal-v2"

    terrestrial_weather_features = (
        "air_temperature_c",
        "relative_humidity_percent",
        "surface_pressure_kpa",
        "wind_speed_m_s",
        "precipitation_corrected",
    )
    link_margin_features = (
        "frequency_hz",
        "transmit_power_dbw",
        "transmit_antenna_gain_dbi",
        "receive_antenna_gain_dbi",
        "system_noise_temperature_k",
        "required_snr_db",
    )

    def __init__(
        self,
        *,
        decode_prior_alpha: float = 2.0,
        decode_prior_beta: float = 2.0,
        transmit_prior_strength: float = 4.0,
    ) -> None:
        if min(decode_prior_alpha, decode_prior_beta, transmit_prior_strength) <= 0:
            raise ValueError("prior parameters must be positive")
        self.decode_prior_alpha = decode_prior_alpha
        self.decode_prior_beta = decode_prior_beta
        self.transmit_prior_strength = transmit_prior_strength

    @staticmethod
    def _match_weight(item: ReceptionEvidence, features: ReceptionFeatures) -> float:
        if item.norad_id != features.norad_id:
            return 0.0
        weight = item.weight * (1.0 if item.source == "local" else 0.65)
        if item.station_id is not None:
            weight *= 1.0 if item.station_id == features.station_id else 0.35
        if item.resource_id is not None:
            weight *= 1.0 if item.resource_id == features.resource_id else 0.5
        return weight

    @staticmethod
    def _link_margin(features: ReceptionFeatures) -> float | None:
        values = (
            features.frequency_hz,
            features.transmit_power_dbw,
            features.transmit_antenna_gain_dbi,
            features.receive_antenna_gain_dbi,
            features.system_noise_temperature_k,
            features.required_snr_db,
        )
        if any(value is None for value in values):
            return None
        assert features.frequency_hz is not None
        assert features.transmit_power_dbw is not None
        assert features.transmit_antenna_gain_dbi is not None
        assert features.receive_antenna_gain_dbi is not None
        assert features.system_noise_temperature_k is not None
        assert features.required_snr_db is not None
        bandwidth_hz = max(features.baud or 2_500.0, 1.0)
        wavelength_m = 299_792_458.0 / features.frequency_hz
        path_loss_db = 20 * math.log10(
            4 * math.pi * features.min_range_km * 1000.0 / wavelength_m
        )
        attenuation = features.atmospheric_attenuation_db or 0.0
        received_dbw = (
            features.transmit_power_dbw
            + features.transmit_antenna_gain_dbi
            + features.receive_antenna_gain_dbi
            - path_loss_db
            - attenuation
        )
        noise_dbw = 10 * math.log10(
            1.380649e-23 * features.system_noise_temperature_k * bandwidth_hz
        )
        return received_dbw - noise_dbw - features.required_snr_db

    def feature_use_contract(self, features: ReceptionFeatures) -> dict[str, object]:
        """Describe feature use without claiming that captured means consumed.

        The cold-start model deliberately has no hand-written coefficient for
        terrestrial weather. Those fields are retained for the frozen learned
        ablation, while Kp, supplied propagation terms and a complete link
        budget are active in this estimator. Opportunity generation appends
        hardware feasibility constraints because they run before this method.
        """

        active = ["max_elevation_deg", "tle_age_hours"]
        for name in (
            "atmospheric_attenuation_db",
            "space_weather_kp",
            "ionospheric_scintillation_index",
        ):
            if getattr(features, name) is not None:
                active.append(name)
        link_margin_computed = self._link_margin(features) is not None
        if link_margin_computed:
            active.extend(self.link_margin_features)
            if features.baud is not None:
                active.append("baud")
        evaluation_only = [
            name
            for name in self.terrestrial_weather_features
            if getattr(features, name) is not None
        ]
        return {
            "schema_version": "probability-feature-use-v1",
            "model_version": self.model_version,
            "active_probability_features": sorted(set(active)),
            "evaluation_only_features": evaluation_only,
            "evaluation_only_reason": (
                "terrestrial weather is retained for the predeclared learned "
                "out-of-sample ablation; this frozen cold-start estimator does "
                "not assign it an unvalidated heuristic effect"
            ),
            "link_margin_computed": link_margin_computed,
            "link_margin_missing_features": [
                name
                for name in self.link_margin_features
                if getattr(features, name) is None
            ],
            "active_feasibility_constraints": [],
        }

    def estimate(
        self,
        features: ReceptionFeatures,
        evidence: Sequence[ReceptionEvidence],
        *,
        transmitter_probability: float,
    ) -> ProbabilityEstimate:
        if not 0 <= transmitter_probability <= 1:
            raise ValueError("transmitter_probability must be in [0, 1]")
        decode_alpha, decode_beta = self.decode_prior_alpha, self.decode_prior_beta
        signal_alpha = max(
            1e-6, transmitter_probability * self.transmit_prior_strength
        )
        signal_beta = max(
            1e-6, (1 - transmitter_probability) * self.transmit_prior_strength
        )
        counted = 0
        for item in evidence:
            weight = self._match_weight(item, features)
            if weight <= 0:
                continue
            if item.listened and item.signal_present is not None:
                if item.signal_present:
                    signal_alpha += weight
                else:
                    signal_beta += weight
            elif item.transmitter_state == "confirmed":
                signal_alpha += weight
            elif item.transmitter_state == "not_transmitting":
                signal_beta += weight
            elif item.transmitter_state == "expected" and item.listened:
                signal_alpha += 0.25 * weight
                signal_beta += 0.25 * weight

            qualified_decode = (
                item.listened
                and item.decoded is not None
                and item.transmitter_state in {"confirmed", "expected"}
            )
            if qualified_decode:
                counted += 1
                if item.decoded:
                    decode_alpha += weight
                else:
                    decode_beta += weight

        p_signal = signal_alpha / (signal_alpha + signal_beta)
        decode_mean = decode_alpha / (decode_alpha + decode_beta)
        log_odds = _logit(decode_mean)
        log_odds += 0.035 * max(-20.0, min(60.0, features.max_elevation_deg - 20.0))
        log_odds -= 0.012 * max(0.0, features.tle_age_hours - 24.0) / 24.0
        if features.atmospheric_attenuation_db is not None:
            log_odds -= 0.18 * max(0.0, features.atmospheric_attenuation_db)
        if features.space_weather_kp is not None:
            log_odds -= 0.06 * max(0.0, features.space_weather_kp - 4.0)
        if features.ionospheric_scintillation_index is not None:
            log_odds -= 0.8 * max(0.0, features.ionospheric_scintillation_index)
        link_margin = self._link_margin(features)
        if link_margin is not None:
            log_odds += max(-2.5, min(2.5, link_margin / 5.0))
        p_decode = _sigmoid(log_odds)
        p_success = p_signal * p_decode

        optional = {
            "frequency_hz": features.frequency_hz,
            "modulation": features.modulation,
            "baud": features.baud,
            "transmit_power_dbw": features.transmit_power_dbw,
            "transmit_antenna_gain_dbi": features.transmit_antenna_gain_dbi,
            "receive_antenna_gain_dbi": features.receive_antenna_gain_dbi,
            "system_noise_temperature_k": features.system_noise_temperature_k,
            "atmospheric_attenuation_db": features.atmospheric_attenuation_db,
            "space_weather_kp": features.space_weather_kp,
            "ionospheric_scintillation_index": features.ionospheric_scintillation_index,
            "required_snr_db": features.required_snr_db,
        }
        missing = tuple(name for name, value in optional.items() if value is None)
        effective_n = decode_alpha + decode_beta + signal_alpha + signal_beta
        beta_variance = p_success * (1 - p_success) / max(effective_n + 1.0, 2.0)
        missing_penalty = min(0.09, len(missing) * 0.008)
        standard_deviation = min(0.5, math.sqrt(beta_variance) + missing_penalty)
        spread = 1.645 * standard_deviation
        return ProbabilityEstimate(
            p_transmit=p_signal,
            p_decode_given_transmit=p_decode,
            p_success=p_success,
            standard_deviation=standard_deviation,
            lower_90=max(0.0, p_success - spread),
            upper_90=min(1.0, p_success + spread),
            missing_features=missing,
            evidence_count=counted,
            model_version=self.model_version,
        )
