use super::*;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::Path;

fn oracle() -> Value {
    serde_json::from_str(include_str!("afsk_legacy_oracle.json")).unwrap()
}
fn data(name: &str) -> Vec<u8> {
    std::fs::read(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("rust/tests/afsk_legacy_fixtures")
            .join(name),
    )
    .unwrap()
}
fn soft_bytes(bytes: &[u8]) -> Vec<f64> {
    bytes
        .as_chunks::<4>()
        .0
        .iter()
        .map(|b| f32::from_le_bytes(*b) as f64)
        .collect()
}

#[test]
fn frozen_timing_candidates_preserve_rank_variable_lengths_guards_and_bits() {
    let oracle = oracle();
    for case in oracle["timing_cases"].as_array().unwrap() {
        let soft: Vec<f64> = serde_json::from_value(case["soft"].clone()).unwrap();
        let config: Afsk1200Config = serde_json::from_value(case["config"].clone()).unwrap();
        let bank = timing_candidates(&soft, &config).unwrap();
        let expected = case["expected"].as_array().unwrap();
        assert_eq!(bank.len(), expected.len(), "{}", case["name"]);
        for (actual, expected) in bank.iter().zip(expected) {
            assert_eq!(
                json!(actual.phase_index),
                expected["phase_index"],
                "{}",
                case["name"]
            );
            assert_eq!(json!(actual.timing.symbol_count), expected["symbol_count"]);
            for (name, value) in [
                ("rate_error_ppm", actual.timing.rate_error_ppm),
                ("phase_samples", actual.timing.phase_samples),
                ("step_samples", actual.timing.step_samples),
            ] {
                assert_eq!(json!(value), expected[name], "{} {name}", case["name"]);
            }
            if expected["score"] == "-inf" {
                assert_eq!(actual.timing.score, f64::NEG_INFINITY);
            } else {
                assert!(
                    (actual.timing.score - expected["score"].as_f64().unwrap()).abs() < 2e-13,
                    "{}",
                    case["name"]
                );
            }
            assert!(
                (actual.timing.threshold - expected["threshold"].as_f64().unwrap()).abs() < 2e-13
            );
            let symbols = soft_symbols(&soft, actual).unwrap();
            let levels: Vec<u8> = symbols
                .iter()
                .map(|v| u8::from(*v >= actual.timing.threshold))
                .collect();
            assert_eq!(json!(levels), expected["levels"], "{}", case["name"]);
        }
    }
}

#[test]
fn complete_legacy_iq_results_match_full_frames_rejections_counts_and_provenance() {
    let oracle = oracle();
    for case in oracle["iq_cases"].as_array().unwrap() {
        let bytes = data(case["input"].as_str().unwrap());
        assert_eq!(
            hex::encode(Sha256::digest(&bytes)),
            case["input_sha256"].as_str().unwrap()
        );
        let iq: Vec<Complex64> = bytes
            .as_chunks::<8>()
            .0
            .iter()
            .map(|b| {
                Complex64::new(
                    f32::from_le_bytes(b[..4].try_into().unwrap()) as f64,
                    f32::from_le_bytes(b[4..].try_into().unwrap()) as f64,
                )
            })
            .collect();
        let config: Afsk1200Config = serde_json::from_value(case["config"].clone()).unwrap();
        let result = decode_afsk1200_iq(&iq, &config, 177).unwrap();
        assert_eq!(
            serde_json::to_value(&result).unwrap(),
            case["expected"],
            "{} same-IQ",
            case["name"]
        );
        let original_soft = soft_bytes(&data(case["soft"].as_str().unwrap()));
        assert_eq!(
            serde_json::to_value(decode_soft(&original_soft, &config, 177).unwrap()).unwrap(),
            case["expected"],
            "{} same-soft",
            case["name"]
        );
        for frame in &result.frames {
            assert!(protocol::valid_ax25_fcs(&frame.frame_with_fcs));
            assert!(protocol::valid_ax25_ui(&frame.normalized_pdu));
        }
        for frame in &result.rejected_frames {
            assert!(protocol::valid_ax25_fcs(&frame.frame_with_fcs));
            assert!(!protocol::valid_ax25_ui(
                &frame.frame_with_fcs[..frame.frame_with_fcs.len() - 2]
            ));
        }
    }
}

#[test]
fn frozen_hdlc_boundaries_polarities_and_rejected_payloads_preserve_original_fcs() {
    for case in oracle()["level_cases"].as_array().unwrap() {
        let levels: Vec<u8> = serde_json::from_value(case["levels"].clone()).unwrap();
        assert_eq!(
            json!(crc_frames_from_levels(&levels).unwrap()),
            case["expected"],
            "{}",
            case["name"]
        );
    }
    assert!(crc_frames_from_levels(&[2; 32]).is_err());
    assert!(crc_frames_from_levels(&[1; 15]).unwrap().is_empty());
}

#[test]
fn generic_shim_uses_legacy_guard_and_does_not_change_shared_dsp_bank() {
    let fixture = oracle();
    let case = &fixture["timing_cases"][13];
    let soft: Vec<f64> = serde_json::from_value(case["soft"].clone()).unwrap();
    let config: Afsk1200Config = serde_json::from_value(case["config"].clone()).unwrap();
    let frontend = dsp::Frontend {
        samples: soft.clone(),
        samples_per_symbol: 8.,
    };
    let bank = timing_bank(&frontend, &config.dsp_config()).unwrap();
    let exact = timing_candidates(&soft, &config).unwrap();
    assert_eq!(bank.len(), exact.len());
    for (legacy, expected) in bank.iter().zip(exact) {
        assert_eq!(*legacy, expected.timing);
        assert_eq!(
            legacy_soft_symbols(&frontend, legacy).unwrap(),
            soft_symbols(&soft, &expected).unwrap()
        );
    }
    assert_ne!(
        dsp::ranked_timing(&frontend, &config.dsp_config()).unwrap()[0].symbol_count,
        bank[0].symbol_count
    );
    let mut wrong = config.dsp_config();
    wrong.bank = "diverse".into();
    assert!(timing_bank(&frontend, &wrong).is_err());
}

#[test]
fn invalid_parameters_and_degenerate_inputs_are_not_false_success() {
    let config = Afsk1200Config::default();
    let result = decode_afsk1200_iq(&vec![Complex64::new(0., 0.); 20_000], &config, 0).unwrap();
    assert_eq!(result, empty_result(true));
    assert!(decode_afsk1200_iq(&vec![Complex64::new(1., 0.); 100], &config, 0).is_err());
    assert!(decode_afsk1200_iq(&vec![Complex64::new(f64::NAN, 0.); 20_000], &config, 0).is_err());
    let mut invalid = config.clone();
    invalid.timing_phase_bins = 0;
    assert!(decode_afsk1200_iq(&vec![Complex64::new(0., 0.); 20_000], &invalid, 0).is_err());
    invalid = config.clone();
    invalid.timing_rate_errors_ppm = vec![-1_000_000.];
    assert!(timing_candidates(&[0.; 2048], &invalid).is_err());
    invalid = config.clone();
    invalid.mark_hz = invalid.space_hz;
    assert!(invalid.validate().is_err());
    assert!(timing_candidates(&[f64::INFINITY; 2048], &config).is_err());
}

#[test]
fn bounded_candidate_union_rejects_instead_of_returning_truncated_success() {
    let mut soft = soft_bytes(&data("positive.soft-f32"));
    soft.extend(soft_bytes(&data("rejected.soft-f32")));
    let mut config = Afsk1200Config {
        max_candidate_records: 2,
        ..Afsk1200Config::default()
    };
    let result = decode_soft(&soft, &config, 0).unwrap();
    assert_eq!(result.frames.len(), 1);
    assert_eq!(result.rejected_frames.len(), 1);
    config.max_candidate_records = 1;
    assert!(
        decode_soft(&soft, &config, 0)
            .unwrap_err()
            .contains("candidate record bound")
    );
}
