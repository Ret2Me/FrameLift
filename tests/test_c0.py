import json
import unittest

from telemetry_yield.c0 import audit_historical_c0, parse_client_metadata


def complete_observation():
    return {
        "id": 123,
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T00:05:00Z",
        "ground_station": 1,
        "norad_cat_id": 99999,
        "transmitter_uuid": "tx",
        "tle1": "1 ...",
        "tle2": "2 ...",
        "client_version": "1.0",
        "client_metadata": json.dumps(
            {
                "radio": {
                    "name": "gr-satnogs",
                    "version": "v1",
                    "parameters": {"samp-rate-rx": "1000000"},
                }
            }
        ),
        "historical_c0": {
            "decoder_graph_id": "fsk9600",
            "decoder_graph_version": "v1",
            "decoder_config_hash": "abc",
            "protocol_id": "fsk9600_g3ruh",
            "center_frequency_hz": 437_000_000,
            "baudrate": 9600,
            "framing": "ax25",
            "bit_polarity": "normal",
            "scrambler": "g3ruh",
            "timing_recovery": "mueller_muller",
        },
        "iq_capture_contract": {
            "sample_rate_hz": 57_600,
            "doppler_state": "post_correction",
            "spectral_sign": "positive_rf_is_positive_baseband",
            "component_order": "iq",
            "time_basis": "observation_start_plus_offset",
        },
    }


class HistoricalC0Tests(unittest.TestCase):
    def test_complete_explicit_snapshot_is_replay_ready(self):
        result = audit_historical_c0(complete_observation())
        self.assertTrue(result.identity_ready)
        self.assertTrue(result.exact_c0_ready)
        self.assertTrue(result.iq_replay_ready)

    def test_receiver_sample_rate_does_not_satisfy_iq_contract(self):
        observation = complete_observation()
        observation.pop("iq_capture_contract")
        result = audit_historical_c0(observation)
        self.assertIn("receiver_sample_rate_hz", result.observed_live_fields)
        self.assertIn("sample_rate_hz", result.iq_contract_missing)
        self.assertFalse(result.iq_replay_ready)

    def test_public_fields_are_evidence_but_not_exact_snapshot(self):
        observation = complete_observation()
        observation.pop("historical_c0")
        observation["transmitter_mode"] = "GMSK"
        observation["transmitter_baud"] = 9600
        observation["observation_frequency"] = 437_000_000
        metadata = json.loads(observation["client_metadata"])
        metadata["radio"]["parameters"]["framing"] = "ax25"
        observation["client_metadata"] = json.dumps(metadata)
        result = audit_historical_c0(observation)
        self.assertTrue(result.identity_ready)
        self.assertIn("client_version", result.observed_live_fields)
        self.assertNotIn("decoder_graph_id", result.exact_c0_missing)
        self.assertNotIn("decoder_graph_version", result.exact_c0_missing)
        self.assertNotIn("protocol_id", result.exact_c0_missing)
        self.assertNotIn("center_frequency_hz", result.exact_c0_missing)
        self.assertIn("decoder_config_hash", result.exact_c0_missing)
        self.assertFalse(result.exact_c0_ready)

    def test_malformed_metadata_fails_closed(self):
        self.assertEqual(parse_client_metadata("not json"), {})
        self.assertEqual(parse_client_metadata("[]"), {})


if __name__ == "__main__":
    unittest.main()
