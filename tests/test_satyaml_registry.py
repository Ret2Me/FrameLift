from __future__ import annotations

import json
from pathlib import Path

from telemetry_yield.gr_satellites_backend import GrSatellitesBackend
from telemetry_yield.satyaml_registry import build_satyaml_registry


FIXTURES = Path(__file__).parent / "fixtures" / "satyaml_registry"


def test_registry_loads_profiles_ready_for_adapter() -> None:
    registry = build_satyaml_registry(
        (FIXTURES / "ax25.yml", FIXTURES / "ccsds_like.yml")
    )

    assert registry.diagnostics == ()
    ax25 = registry.get_by_name("test ax25")
    assert ax25 is not None
    assert ax25 is registry.get_by_norad(90001)
    assert ax25.selector_kind == "satyaml"
    assert ax25.modulations == ("FSK",)
    assert ax25.framing == ("AX.25 G3RUH",)
    # G3RUH in a framing label is not guessed to be FEC.
    assert ax25.fec == ()
    transmitter = ax25.transmitters[0]
    assert transmitter.metadata["baudrate"] == 9600
    assert transmitter.metadata["frequency"] == 437250000
    assert transmitter.metadata["deviation"] == 2400

    adapter = GrSatellitesBackend(
        executable="/usr/bin/gr_satellites",
        executable_version="5.9.0",
        profile=ax25,
        timeout_seconds=10,
        max_output_bytes=4096,
    )
    assert adapter.capabilities.modulations == ("FSK",)
    assert adapter.capabilities.framing == ("AX.25 G3RUH",)
    assert adapter.capabilities.fec == ()


def test_explicit_fec_and_ccsds_like_framing_are_preserved_without_claiming_support() -> None:
    registry = build_satyaml_registry((FIXTURES / "ccsds_like.yml",))

    profile = registry.get_by_norad(90002)
    assert profile is not None
    assert profile.modulations == ("BPSK",)
    assert profile.framing == ("CCSDS Concatenated",)
    assert profile.fec == ("convolutional-r1/2", "reed-solomon")
    assert profile.transmitters[0].metadata["scrambler"] == "CCSDS"


def test_bad_yaml_is_diagnostic_and_does_not_discard_good_profiles() -> None:
    registry = build_satyaml_registry(
        (FIXTURES / "malformed.yml", FIXTURES / "ax25.yml")
    )

    assert len(registry.profiles) == 1
    assert registry.get_by_name("TEST AX25") is not None
    assert len(registry.diagnostics) == 1
    assert registry.diagnostics[0].code == "schema_error"
    assert "framing" in registry.diagnostics[0].message


def test_unsafe_yaml_tag_is_rejected_by_safe_loader() -> None:
    registry = build_satyaml_registry(FIXTURES / "unsafe.yml")

    assert registry.profiles == ()
    assert len(registry.diagnostics) == 1
    assert registry.diagnostics[0].code == "yaml_error"


def test_duplicate_keys_are_diagnosed_and_removed_only_from_ambiguous_index() -> None:
    registry = build_satyaml_registry(
        (
            FIXTURES / "ax25.yml",
            FIXTURES / "ccsds_like.yml",
            FIXTURES / "duplicate_name.yml",
            FIXTURES / "duplicate_norad.yml",
        )
    )

    assert len(registry.profiles) == 4
    assert registry.get_by_name("test ax25") is None
    assert registry.get_by_norad(90002) is None
    assert registry.get_by_norad(90001) is not None
    assert registry.get_by_name("test ccsds") is not None
    assert registry.conflicted_names == ("test ax25",)
    assert registry.conflicted_norads == (90002,)
    assert {item.code for item in registry.diagnostics} == {
        "duplicate_name",
        "duplicate_norad",
    }


def test_directory_scan_and_serialization_are_deterministic() -> None:
    files = (FIXTURES / "ccsds_like.yml", FIXTURES / "ax25.yml")
    forward = build_satyaml_registry(files).to_dict()
    reverse = build_satyaml_registry(tuple(reversed(files))).to_dict()

    assert forward == reverse
    json.dumps(forward, sort_keys=True, allow_nan=False)
    assert [item["name"] for item in forward["profiles"]] == [
        "TEST AX25",
        "TEST CCSDS",
    ]


def test_directory_scan_keeps_working_when_one_fixture_is_malformed() -> None:
    registry = build_satyaml_registry((FIXTURES,))

    assert len(registry.profiles) == 4
    assert any(item.code == "schema_error" for item in registry.diagnostics)
    assert any(item.code == "duplicate_name" for item in registry.diagnostics)
