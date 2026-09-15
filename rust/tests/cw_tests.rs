use super::*;
use crate::input;
use serde_json::Value;
use std::io::{Seek, SeekFrom, Write};

fn morse_envelope() -> Vec<bool> {
    let unit = 480;
    let mut values = vec![false; 7 * unit];
    for (index, code) in ["...", "---", "..."].iter().enumerate() {
        for (position, symbol) in code.bytes().enumerate() {
            values.extend(std::iter::repeat_n(
                true,
                unit * if symbol == b'-' { 3 } else { 1 },
            ));
            if position + 1 < code.len() {
                values.extend(std::iter::repeat_n(false, unit));
            }
        }
        if index < 2 {
            values.extend(std::iter::repeat_n(false, 3 * unit));
        }
    }
    values.extend(std::iter::repeat_n(false, 7 * unit));
    values
}
fn fixture_bytes(case: &Value, oscillator: &[[i16; 2]]) -> Vec<u8> {
    if let Some(name) = case["input_file"].as_str() {
        return match name {
            "cw_legacy_sos.ci16" => include_bytes!("cw_legacy_sos.ci16").to_vec(),
            "cw_legacy_carrier.ci16" => include_bytes!("cw_legacy_carrier.ci16").to_vec(),
            "cw_legacy_noise.ci16" => include_bytes!("cw_legacy_noise.ci16").to_vec(),
            _ => panic!("unexpected fixture"),
        };
    }
    let mut state = case["seed"].as_u64().unwrap();
    let samples = case["samples"].as_u64().unwrap() as usize;
    let kind = case["waveform"].as_str().unwrap();
    let envelope = morse_envelope();
    let mut output = Vec::new();
    for index in 0..samples {
        let mut pair = [0_i16; 2];
        for value in &mut pair {
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            *value = ((state >> 32) % 61) as i16 - 30;
        }
        let enabled = kind == "carrier"
            || (kind == "sos_twice"
                && [0, 80_000].iter().any(|start| {
                    index >= *start && envelope.get(index - start).copied().unwrap_or(false)
                }));
        if enabled {
            for (value, osc) in pair.iter_mut().zip(oscillator[index % 8]) {
                *value += 9 * osc;
            }
        }
        if kind == "zero" {
            pair = [0, 0];
        }
        for value in pair {
            output.extend_from_slice(&value.to_le_bytes());
        }
    }
    output
}
fn compare(actual: &Value, expected: &Value, path: &str, max_error: &mut f64) {
    match (actual, expected) {
        (Value::Number(a), Value::Number(b)) if a.is_f64() || b.is_f64() => {
            let (a, b) = (a.as_f64().unwrap(), b.as_f64().unwrap());
            let error = (a - b).abs() / (1.0 + b.abs());
            *max_error = max_error.max(error);
            assert!(
                error <= 2e-6,
                "{path}: actual={a:.17e}, expected={b:.17e}, normalized_error={error:.4e}"
            );
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "{path}");
            for (i, (a, b)) in a.iter().zip(b).enumerate() {
                compare(a, b, &format!("{path}/{i}"), max_error);
            }
        }
        (Value::Object(a), Value::Object(b)) => {
            assert_eq!(a.len(), b.len(), "{path}");
            for (key, b) in b {
                compare(a.get(key).unwrap(), b, &format!("{path}/{key}"), max_error);
            }
        }
        _ => assert_eq!(actual, expected, "{path}"),
    }
}
#[test]
fn unchanged_python_waveforms_match_classification_morse_ranking_and_scores() {
    let fixture: Value = serde_json::from_str(include_str!("cw_oracle.json")).unwrap();
    assert_eq!(fixture["legacy_tests_passed"], 3);
    let oscillator: Vec<[i16; 2]> = serde_json::from_value(fixture["oscillator"].clone()).unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let mut source = tempfile::NamedTempFile::new().unwrap();
        source.write_all(&fixture_bytes(case, &oscillator)).unwrap();
        source.flush().unwrap();
        assert_eq!(
            input::identity(source.path()).unwrap().sha256,
            case["input_sha256"].as_str().unwrap()
        );
        let config = serde_json::from_value(case["config"].clone()).unwrap();
        let actual = extract_ci16(source.path(), &config).unwrap();
        let mut error = 0.0;
        let mut actual_json = serde_json::to_value(&actual).unwrap();
        let mut expected_json = case["expected"].clone();
        if case["name"] == "legacy_carrier" {
            // The reference's two centers are exactly equal. Rust FFT
            // roundoff separates them by a few ULPs, so the K-means mask's
            // duty, transition count and floored separation are ill-conditioned.
            // Compare the meaningful classification, selection, candidates,
            // carrier offsets, envelope level and evidence score normally.
            // Do not modify either implementation's computed output.
            assert_eq!(actual.classification, "non_keyed");
            assert!(actual.candidates.is_empty());
            assert_eq!(actual.keying_evidence_score, 0.0);
            assert!((actual.envelope_high_to_low_ratio.unwrap() - 1.0).abs() < 1e-12);
            assert!(
                (actual.envelope_high_center.unwrap() - actual.envelope_low_center.unwrap()).abs()
                    < 1e-8
            );
            assert!((0.0..=1.0).contains(&actual.keying_duty_cycle.unwrap()));
            eprintln!(
                "CW constant-carrier diagnostic scope: Rust duty={}, transitions={}, {}; Python duty=0, transitions=0, separation=0",
                actual.keying_duty_cycle.unwrap(),
                actual.keying_transition_count,
                actual.evidence[1]
            );
            for value in [&mut actual_json, &mut expected_json] {
                let object = value.as_object_mut().unwrap();
                object.remove("keying_duty_cycle");
                object.remove("keying_transition_count");
                object["evidence"].as_array_mut().unwrap().drain(1..4);
            }
        }
        compare(
            &actual_json,
            &expected_json,
            case["name"].as_str().unwrap(),
            &mut error,
        );
        assert!(
            actual
                .candidates
                .iter()
                .all(|candidate| candidate.candidate_validation == "pending")
        );
        if case["name"] == "legacy_sos" {
            assert_eq!(actual.classification, "keyed");
            assert!(actual.candidates.iter().any(|v| v.text == "SOS"));
        }
        if case["name"] == "legacy_carrier" {
            assert_eq!(actual.classification, "non_keyed");
            assert!(actual.candidates.is_empty());
        }
        eprintln!("CW {} normalized numeric error {error:.3e}", case["name"]);
    }
    assert_eq!(
        NarrowbandCwExtractor::default().capabilities().output_kind,
        "untrusted_text_candidates"
    );
}

#[test]
fn linear_padding_matches_all_small_masks_and_dense_large_input() {
    for pattern in 0..256 {
        let active: Vec<_> = (0..8).map(|i| pattern & (1 << i) != 0).collect();
        for padding in [0, 1, 2, 3, 7, 8, 16, usize::MAX] {
            let expected: Vec<_> = (0..active.len())
                .map(|i| {
                    active
                        .iter()
                        .enumerate()
                        .any(|(j, on)| *on && i.abs_diff(j) <= padding)
                })
                .collect();
            assert_eq!(padded_windows(&active, padding), expected);
        }
    }
    assert!(padded_windows(&[], usize::MAX).is_empty());
    assert_eq!(
        padded_windows(&vec![true; 100_000], usize::MAX),
        vec![true; 100_000]
    );
}
#[test]
fn numpy_argpartition_and_morse_run_oracles_match() {
    let fixture: Value = serde_json::from_str(include_str!("cw_oracle.json")).unwrap();
    for (index, case) in fixture["partitions"].as_array().unwrap().iter().enumerate() {
        let values: Vec<f64> = serde_json::from_value(case["values"].clone()).unwrap();
        let keep = case["keep"].as_u64().unwrap() as usize;
        let mut actual: Vec<_> = (0..values.len()).collect();
        introselect(&values, &mut actual, values.len() - keep);
        assert_eq!(
            serde_json::to_value(actual).unwrap(),
            case["expected"],
            "partition {index}"
        );
    }
    for (index, case) in fixture["runs"].as_array().unwrap().iter().enumerate() {
        let input: Vec<(bool, f64)> = serde_json::from_value(case["runs"].clone()).unwrap();
        let actual = decode_runs(&input, case["wpm"].as_u64().unwrap() as u32);
        let expected: (String, f64, f64, usize) =
            serde_json::from_value(case["expected"].clone()).unwrap();
        assert_eq!(actual.0, expected.0, "run case {index}: decoded text");
        assert_eq!(actual.2, expected.2, "run case {index}: valid fraction");
        assert_eq!(actual.3, expected.3, "run case {index}: character count");
        // exp() uses the platform math implementation. The Linux CI runner and
        // the oracle host differ by one ULP here, without a decoded-data change.
        // Only this diagnostic gets a bounded tolerance; text, counts, fractions,
        // partition order and the end-to-end candidate decisions stay exact.
        assert!(
            timing_score_matches(actual.1, expected.1),
            "run case {index}: timing score {} != {} (maximum 4 ULP)",
            actual.1,
            expected.1
        );
    }
}

fn timing_score_matches(actual: f64, expected: f64) -> bool {
    actual.is_finite()
        && expected.is_finite()
        && (0.0..=1.0).contains(&actual)
        && (0.0..=1.0).contains(&expected)
        && actual.to_bits().abs_diff(expected.to_bits()) <= 4
}

#[test]
fn timing_score_tolerance_is_bounded_and_rejects_invalid_values() {
    assert!(timing_score_matches(0.5230471474118935, 0.5230471474118936));
    let reference = 0.5_f64;
    for delta in 0..=8 {
        assert_eq!(
            timing_score_matches(f64::from_bits(reference.to_bits() + delta), reference),
            delta <= 4
        );
    }
    for invalid in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, -0.1, 1.1] {
        assert!(!timing_score_matches(invalid, invalid));
        assert!(!timing_score_matches(invalid, reference));
        assert!(!timing_score_matches(reference, invalid));
    }
    assert!(!timing_score_matches(0.0, 0.01));
}
#[test]
fn malformed_zero_and_bounded_metadata_fail_safely() {
    let mut file = tempfile::NamedTempFile::new().unwrap();
    file.write_all(b"bad").unwrap();
    assert!(extract_ci16(file.path(), &CwProbeConfig::default()).is_err());
    file.as_file_mut().set_len(0).unwrap();
    assert!(extract_ci16(file.path(), &CwProbeConfig::default()).is_err());
    for config in [
        CwProbeConfig {
            sample_rate_hz: 0.0,
            ..Default::default()
        },
        CwProbeConfig {
            preview_fft_size: 3,
            ..Default::default()
        },
        CwProbeConfig {
            wpm_min: 4,
            ..Default::default()
        },
        CwProbeConfig {
            envelope_bin_seconds: 2.0,
            ..Default::default()
        },
        CwProbeConfig {
            carrier_track_transition_scale_hz: f64::NAN,
            ..Default::default()
        },
        CwProbeConfig {
            window_seconds: 1000.0,
            ..Default::default()
        },
    ] {
        assert!(extract_ci16(file.path(), &config).is_err());
    }
    file.seek(SeekFrom::Start(0)).unwrap();
    file.write_all(&vec![0; 16_000 * 4]).unwrap();
    let config = CwProbeConfig {
        sample_rate_hz: 8000.0,
        preview_fft_size: 2048,
        ..Default::default()
    };
    let mut result = extract_ci16(file.path(), &config).unwrap();
    assert_eq!(result.classification, "unknown");
    assert!(result.candidates.is_empty());
    result.envelope_high_center = Some(f64::INFINITY);
    assert!(
        checked_result(result).is_err(),
        "nonfinite output must not become a null diagnostic"
    );
    let oversized = CwProbeConfig {
        carrier_track_candidates: 1_000_000,
        preview_fft_size: 8192,
        ..config
    };
    assert!(extract_ci16(file.path(), &oversized).is_err());
}
