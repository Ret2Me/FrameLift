from __future__ import annotations

from pathlib import Path

from telemetry_yield.catalog_satyaml_routing import (
    CatalogObservation,
    CatalogTransmitter,
    load_catalog_iq_observations,
    load_positive_g3ruh_iq_observations,
    route_catalog_observations,
)
from telemetry_yield.satyaml_registry import build_satyaml_registry


FIXTURES = Path(__file__).parent / "fixtures" / "satyaml_registry"


def _observation(**overrides: object) -> CatalogObservation:
    values: dict[str, object] = {
        "observation_id": 1,
        "satellite_id": "SAT-UUID",
        "frequency_hz": 437_250_000,
        "mode": "FSK",
        "iq_url": "https://example.invalid/observation.iq",
    }
    values.update(overrides)
    return CatalogObservation(**values)  # type: ignore[arg-type]


def _transmitter(**overrides: object) -> CatalogTransmitter:
    values: dict[str, object] = {
        "transmitter_uuid": "TX-UUID",
        "satellite_id": "SAT-UUID",
        "norad_id": 90001,
        "frequency_hz": 437_250_000,
        "mode": "FSK",
        "baud": 9600,
        "status": "active",
    }
    values.update(overrides)
    return CatalogTransmitter(**values)  # type: ignore[arg-type]


def test_exact_chain_routes_to_frequency_matched_profile() -> None:
    registry = build_satyaml_registry(FIXTURES / "ax25.yml")

    result = route_catalog_observations(
        (_observation(),), (_transmitter(),), registry
    )

    route = result.routes[0]
    assert route.status == "routed"
    assert route.norad_id == 90001
    assert route.profile is not None
    assert route.profile.name == "TEST AX25"
    assert route.profile_transmitter_ids == ("UHF telemetry",)
    assert route.disagreements == ()
    assert result.summary()["existing_profile_routable_count"] == 1
    assert result.summary()["modulation_distribution"] == {"FSK": 1}
    assert result.summary()["framing_distribution"] == {"AX.25 G3RUH": 1}


def test_frequency_is_exact_by_default_and_tolerance_is_explicit() -> None:
    registry = build_satyaml_registry(FIXTURES / "ax25.yml")
    shifted = _observation(frequency_hz=437_250_001)

    exact = route_catalog_observations((shifted,), (_transmitter(),), registry)
    bounded = route_catalog_observations(
        (shifted,), (_transmitter(),), registry, frequency_tolerance_hz=1
    )

    assert exact.routes[0].status == "unmatched_catalog_frequency"
    assert bounded.routes[0].status == "routed"
    assert exact.frequency_tolerance_hz == 0.0
    assert bounded.frequency_tolerance_hz == 1.0


def test_catalog_norad_disagreement_is_ambiguous() -> None:
    registry = build_satyaml_registry(FIXTURES / "ax25.yml")
    conflicting = _transmitter(
        transmitter_uuid="OTHER-TX", norad_id=90002
    )

    result = route_catalog_observations(
        (_observation(),), (_transmitter(), conflicting), registry
    )

    assert result.routes[0].status == "ambiguous_catalog_norad"
    assert result.routes[0].profile is None


def test_registry_norad_conflict_is_ambiguous() -> None:
    registry = build_satyaml_registry(
        (FIXTURES / "ccsds_like.yml", FIXTURES / "duplicate_norad.yml")
    )
    observation = _observation(frequency_hz=2_200_000_000)
    transmitter = _transmitter(frequency_hz=2_200_000_000, norad_id=90002)

    result = route_catalog_observations((observation,), (transmitter,), registry)

    assert result.routes[0].status == "ambiguous_satyaml_profile"


def test_profile_frequency_must_also_match() -> None:
    registry = build_satyaml_registry(FIXTURES / "ax25.yml")
    observation = _observation(frequency_hz=437_260_000)
    transmitter = _transmitter(frequency_hz=437_260_000)

    result = route_catalog_observations((observation,), (transmitter,), registry)

    assert result.routes[0].status == "unmatched_profile_frequency"
    assert result.summary()["definite_new_or_updated_profile_count"] == 1


def test_mode_and_baud_disagreements_are_reported_without_name_guessing() -> None:
    registry = build_satyaml_registry(FIXTURES / "ax25.yml")
    observation = _observation(mode="GMSK")
    transmitter = _transmitter(mode="GMSK", baud=4800)

    result = route_catalog_observations((observation,), (transmitter,), registry)

    route = result.routes[0]
    assert route.status == "routed"
    assert [item.field for item in route.disagreements] == ["mode", "baud"]
    assert all(item.comparison == "exact_no_aliases" for item in route.disagreements)
    assert result.summary()["mode_disagreement_count"] == 1
    assert result.summary()["baud_disagreement_count"] == 1


def test_same_name_never_routes_a_different_uuid() -> None:
    registry = build_satyaml_registry(FIXTURES / "ax25.yml")

    result = route_catalog_observations(
        (_observation(satellite_id="UNKNOWN"),), (_transmitter(),), registry
    )

    assert result.routes[0].status == "unmatched_satellite_uuid"
    assert result.summary()["metadata_or_ambiguity_blocked_count"] == 1


def test_result_order_and_summary_are_deterministic() -> None:
    registry = build_satyaml_registry(FIXTURES / "ax25.yml")
    observations = (
        _observation(observation_id=2, satellite_id="UNKNOWN"),
        _observation(observation_id=1),
    )

    forward = route_catalog_observations(observations, (_transmitter(),), registry)
    reverse = route_catalog_observations(
        tuple(reversed(observations)), (_transmitter(),), registry
    )

    assert forward.to_dict() == reverse.to_dict()
    assert [item.observation.observation_id for item in forward.routes] == [1, 2]


def test_catalogue_loader_selects_exact_positive_g3ruh_iq(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text(
        """{
          "observations": [
            {"observation_id": 3, "satellite_id": "S3", "frequency_hz": 3,
             "mode": "FSK AX.25 G3RUH", "frames_recovered": true,
             "frame_count": 1, "links": {"iq": "https://invalid/3.iq"}},
            {"observation_id": 1, "satellite_id": "S1", "frequency_hz": 1,
             "mode": "FSK AX.25 G3RUH", "frames_recovered": false,
             "frame_count": 0, "links": {"iq": "https://invalid/1.iq"}},
            {"observation_id": 2, "satellite_id": "S2", "frequency_hz": 2,
             "mode": "GMSK", "frames_recovered": true,
             "frame_count": 1, "links": {"iq": "https://invalid/2.iq"}},
            {"observation_id": 4, "satellite_id": "S4", "frequency_hz": 4,
             "mode": "FSK AX.25 G3RUH", "frames_recovered": true,
             "frame_count": 1, "links": {"iq": null}}
          ]
        }""",
        encoding="utf-8",
    )

    selected = load_positive_g3ruh_iq_observations(catalogue)
    generic = load_catalog_iq_observations(
        catalogue, frames_recovered=True, mode="FSK AX.25 G3RUH"
    )

    assert selected == generic
    assert [item.observation_id for item in selected] == [3]


def test_catalogue_loader_validates_filter_types(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text('{"observations": []}', encoding="utf-8")

    try:
        load_catalog_iq_observations(catalogue, frames_recovered=1)  # type: ignore[arg-type]
    except TypeError as error:
        assert "frames_recovered" in str(error)
    else:
        raise AssertionError("integer outcome filter must be rejected")
