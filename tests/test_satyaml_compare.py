import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from telemetry_yield.satyaml_compare import (
    SatYamlTransmitter,
    SatnogsTransmitter,
    comparison_rows,
    description_features,
    load_satyaml_archive,
    match_transmitters,
    parse_satnogs_json,
    parse_satyaml_text,
    summarize_comparison,
)


def satyaml_transmitter(**overrides):
    values = {
        "source_file": "fixture.yml",
        "satellite_name": "TESTSAT",
        "norad_id": 12345,
        "description": "9k6 FSK downlink",
        "frequency_hz": 437_500_000.0,
        "modulation": "FSK",
        "baudrate": 9600.0,
        "framing": "AX.25 G3RUH",
        "scrambler": None,
    }
    values.update(overrides)
    return SatYamlTransmitter(**values)


def satnogs_transmitter(**overrides):
    values = {
        "uuid": "tx-1",
        "norad_id": 12345,
        "description": "Mode U GMSK9k6 transmitter",
        "mode": "GMSK",
        "baud": 9600.0,
        "downlink_low_hz": 437_500_000.0,
        "downlink_high_hz": 437_500_000.0,
        "framing": None,
        "scrambler": None,
        "status": "active",
        "updated": "2026-08-01T00:00:00Z",
    }
    values.update(overrides)
    return SatnogsTransmitter(**values)


class SatYamlParserTests(unittest.TestCase):
    def test_parses_multiple_transmitters_and_supported_scalars(self):
        text = """\
name: 'Test Sat'
norad: 12345
data:
  &tlm Telemetry:
    telemetry: ax25
transmitters:
  1k2 AFSK downlink:
    frequency: 145.825e+6
    modulation: AFSK
    baudrate: 1200
    framing: "AX.25"
    scrambler: none # explicit value
    data:
    - *tlm
  9k6 FSK downlink:
    frequency: 437.500e+6
    modulation: FSK
    baudrate: 9600
    framing: AX.25 G3RUH
    data:
    - *tlm
"""
        transmitters = parse_satyaml_text(text, source_file="Test.yml")

        self.assertEqual(len(transmitters), 2)
        self.assertEqual(transmitters[0].satellite_name, "Test Sat")
        self.assertEqual(transmitters[0].frequency_hz, 145_825_000)
        self.assertEqual(transmitters[0].scrambler, "none")
        self.assertIsNone(transmitters[1].scrambler)

    def test_rejects_incomplete_transmitter(self):
        text = """\
name: TEST
norad: 1
transmitters:
  incomplete:
    frequency: 100e+6
"""
        with self.assertRaisesRegex(ValueError, "missing"):
            parse_satyaml_text(text, source_file="bad.yml")

    def test_accepts_valid_nonstandard_yaml_indentation(self):
        text = """\
name: TEST
norad: 1
transmitters:
   downlink:
     frequency: 100e+6
     modulation: FSK
     baudrate: 9600
     framing: AX.25
"""
        result = parse_satyaml_text(text, source_file="three-spaces.yml")
        self.assertEqual(result[0].frequency_hz, 100_000_000)

    def test_reads_only_satyaml_members_from_archive(self):
        content = b"""\
name: TEST
norad: 7
transmitters:
  one:
    frequency: 1e6
    modulation: FSK
    baudrate: 1
    framing: AX.25
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                info = tarfile.TarInfo("repo/python/satyaml/TEST.yml")
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
                ignored = b"not yaml"
                info = tarfile.TarInfo("repo/README.md")
                info.size = len(ignored)
                archive.addfile(info, io.BytesIO(ignored))

            result = load_satyaml_archive(path)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].norad_id, 7)


class SatnogsParserTests(unittest.TestCase):
    def test_parses_snapshot_and_optional_parameter_fields(self):
        payload = json.dumps(
            [
                {
                    "uuid": "abc",
                    "norad_cat_id": 42,
                    "description": "Telemetry",
                    "mode": "FSK",
                    "baud": 9600,
                    "downlink_low": 437500000,
                    "downlink_high": 437500000,
                    "params": {"framing": "AX.25", "randomizer": "G3RUH"},
                    "status": "active",
                    "updated": "2026-08-01T00:00:00Z",
                }
            ]
        )
        result = parse_satnogs_json(payload)

        self.assertEqual(result[0].framing, "AX.25")
        self.assertEqual(result[0].scrambler, "G3RUH")

    def test_rejects_duplicate_uuids(self):
        record = {"uuid": "same", "norad_cat_id": 1}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_satnogs_json(json.dumps([record, record]))


class MatchingTests(unittest.TestCase):
    def test_description_features_expand_baud_and_normalize_gmsk(self):
        self.assertEqual(
            description_features("Mode U - GMSK9k6 Transmitter"),
            ("9600", "fsk"),
        )

    def test_unique_norad_candidate_is_matched_even_with_generic_description(self):
        match = match_transmitters(
            [satyaml_transmitter(description="downlink")],
            [satnogs_transmitter(description="Telemetry")],
        )[0]
        self.assertEqual(match.status, "unique_norad_candidate")
        self.assertIsNotNone(match.satnogs)

    def test_ambiguous_descriptions_are_not_forced(self):
        candidates = [
            satnogs_transmitter(uuid="a", description="9k6 FSK A", baud=9600),
            satnogs_transmitter(uuid="b", description="9k6 FSK B", baud=4800),
        ]
        match = match_transmitters([satyaml_transmitter()], candidates)[0]
        self.assertEqual(match.status, "ambiguous_description")
        self.assertIsNone(match.satnogs)

    def test_equivalent_description_tie_is_safe(self):
        candidates = [
            satnogs_transmitter(uuid="a", description="9k6 FSK A"),
            satnogs_transmitter(uuid="b", description="9k6 FSK B"),
        ]
        match = match_transmitters([satyaml_transmitter()], candidates)[0]
        self.assertEqual(match.status, "equivalent_description_tie")
        self.assertEqual(match.equivalent_candidates, 2)

    def test_parameter_conflict_vetoes_description_match_without_selecting_by_value(self):
        candidates = [
            satnogs_transmitter(
                uuid="description-winner",
                description="9k6 FSK downlink",
                downlink_low_hz=145_000_000,
                downlink_high_hz=145_000_000,
            ),
            satnogs_transmitter(
                uuid="frequency-winner",
                description="Telemetry",
                downlink_low_hz=437_500_000,
                downlink_high_hz=437_500_000,
            ),
        ]
        match = match_transmitters([satyaml_transmitter()], candidates)[0]
        self.assertEqual(match.status, "description_parameter_conflict_frequency")
        self.assertIsNone(match.satnogs)


class ComparisonTests(unittest.TestCase):
    def test_reports_mismatch_availability_and_frequency_tolerance(self):
        definition = satyaml_transmitter(scrambler="CCSDS")
        candidate = satnogs_transmitter(
            mode="GMSK",
            baud=4800,
            downlink_low_hz=437_499_500,
            downlink_high_hz=437_499_500,
        )
        match = match_transmitters([definition], [candidate])[0]
        rows = {row["field"]: row for row in comparison_rows([match])}

        self.assertEqual(rows["modulation"]["comparison_status"], "mismatch")
        self.assertEqual(rows["baudrate"]["comparison_status"], "mismatch")
        self.assertEqual(rows["frequency_downlink_hz"]["comparison_status"], "match")
        self.assertEqual(rows["framing"]["comparison_status"], "unavailable_in_satnogs")
        self.assertEqual(rows["scrambler"]["comparison_status"], "unavailable_in_satnogs")

    def test_satnogs_composite_mode_compares_by_modulation_family(self):
        definition = satyaml_transmitter(modulation="FSK")
        candidate = satnogs_transmitter(mode="FSK AX.25 G3RUH")
        match = match_transmitters([definition], [candidate])[0]
        rows = {row["field"]: row for row in comparison_rows([match])}
        self.assertEqual(rows["modulation"]["comparison_status"], "match")

    def test_unmatched_rows_are_explicit_and_summarized(self):
        match = match_transmitters([satyaml_transmitter()], [])[0]
        rows = comparison_rows([match])
        summary = summarize_comparison([match], rows)

        self.assertEqual(len(rows), 5)
        self.assertEqual(summary["matched_transmitters"], 0)
        self.assertEqual(
            summary["field_status_counts"]["modulation"],
            {"not_compared_unmatched": 1},
        )


if __name__ == "__main__":
    unittest.main()
