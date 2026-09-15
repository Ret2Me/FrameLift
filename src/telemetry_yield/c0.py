"""Fail-closed audit of historical live-decoder configuration (C0)."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping


_IDENTITY_FIELDS = (
    "id",
    "start",
    "end",
    "ground_station",
    "norad_cat_id",
    "transmitter_uuid",
    "tle1",
    "tle2",
)

_EXACT_C0_FIELDS = (
    "decoder_graph_id",
    "decoder_graph_version",
    "decoder_config_hash",
    "protocol_id",
    "center_frequency_hz",
    "baudrate",
    "framing",
    "bit_polarity",
    "scrambler",
    "timing_recovery",
)

_IQ_CONTRACT_FIELDS = (
    "sample_rate_hz",
    "doppler_state",
    "spectral_sign",
    "component_order",
    "time_basis",
)


def _is_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def parse_client_metadata(value: object) -> Mapping[str, Any]:
    """Return client metadata as a mapping, failing closed on malformed input."""
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, Mapping) else {}


@dataclass(frozen=True, slots=True)
class C0Audit:
    """Completeness result for one observation and its IQ replay contract."""

    observation_id: str
    identity_missing: tuple[str, ...]
    observed_live_fields: tuple[str, ...]
    exact_c0_missing: tuple[str, ...]
    iq_contract_missing: tuple[str, ...]

    @property
    def identity_ready(self) -> bool:
        return not self.identity_missing

    @property
    def exact_c0_ready(self) -> bool:
        return self.identity_ready and not self.exact_c0_missing

    @property
    def iq_replay_ready(self) -> bool:
        return self.exact_c0_ready and not self.iq_contract_missing

    def as_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "identity_missing": list(self.identity_missing),
            "identity_ready": self.identity_ready,
            "observed_live_fields": list(self.observed_live_fields),
            "exact_c0_missing": list(self.exact_c0_missing),
            "exact_c0_ready": self.exact_c0_ready,
            "iq_contract_missing": list(self.iq_contract_missing),
            "iq_replay_ready": self.iq_replay_ready,
        }


def audit_historical_c0(observation: Mapping[str, Any]) -> C0Audit:
    """Audit exact C0 and IQ-replay evidence without inferring missing values.

    ``samp-rate-rx`` is deliberately treated as SDR input metadata. It cannot
    satisfy the stored-IQ sample-rate field.
    """
    identity_missing = tuple(
        field for field in _IDENTITY_FIELDS if not _is_present(observation.get(field))
    )
    metadata = parse_client_metadata(observation.get("client_metadata"))
    radio = _mapping(metadata.get("radio"))
    radio_parameters = _mapping(radio.get("parameters"))

    candidates = {
        "client_version": observation.get("client_version"),
        "radio_name": radio.get("name"),
        "radio_version": radio.get("version"),
        "receiver_sample_rate_hz": radio_parameters.get("samp-rate-rx"),
        "receiver_frequency_hz": radio_parameters.get("rx-freq"),
        "receiver_bandwidth_hz": radio_parameters.get("bw"),
        "receiver_ppm": radio_parameters.get("ppm"),
        "transmitter_mode": observation.get("transmitter_mode"),
        "transmitter_baud": observation.get("transmitter_baud"),
        "transmitter_framing": radio_parameters.get("framing"),
    }
    observed_live_fields = tuple(
        name for name, value in candidates.items() if _is_present(value)
    )

    exact_c0 = dict(_mapping(observation.get("historical_c0")))
    if _is_present(radio.get("name")):
        exact_c0.setdefault("decoder_graph_id", radio.get("name"))
    if _is_present(radio.get("version")):
        exact_c0.setdefault("decoder_graph_version", radio.get("version"))
    frequency = observation.get("observation_frequency")
    if not _is_present(frequency):
        frequency = observation.get("center_frequency")
    if not _is_present(frequency):
        frequency = radio_parameters.get("rx-freq")
    if _is_present(frequency):
        exact_c0.setdefault("center_frequency_hz", frequency)
    baudrate = observation.get("transmitter_baud")
    if not _is_present(baudrate):
        baudrate = radio_parameters.get("baudrate")
    if _is_present(baudrate):
        exact_c0.setdefault("baudrate", baudrate)
    framing = radio_parameters.get("framing")
    if _is_present(framing):
        exact_c0.setdefault("framing", framing)
    mode = observation.get("transmitter_mode")
    if all(_is_present(value) for value in (mode, baudrate, framing)):
        protocol_id = f"{mode}-{baudrate}-{framing}".lower()
        exact_c0.setdefault("protocol_id", protocol_id)
    exact_c0_missing = tuple(
        field for field in _EXACT_C0_FIELDS if not _is_present(exact_c0.get(field))
    )

    iq_contract = _mapping(observation.get("iq_capture_contract"))
    iq_contract_missing = tuple(
        field for field in _IQ_CONTRACT_FIELDS if not _is_present(iq_contract.get(field))
    )
    return C0Audit(
        observation_id=str(observation.get("id", "unknown")),
        identity_missing=identity_missing,
        observed_live_fields=observed_live_fields,
        exact_c0_missing=exact_c0_missing,
        iq_contract_missing=iq_contract_missing,
    )
