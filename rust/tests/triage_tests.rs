use super::*;
use std::io::Write;

fn fixture_bytes(case: &Value, oscillator: &[[i16; 2]]) -> Vec<u8> {
    let mut state = case["seed"].as_u64().unwrap();
    let count = case["samples"].as_u64().unwrap() as usize;
    let rate = case["sample_rate"].as_u64().unwrap() as usize;
    let kind = case["waveform"].as_str().unwrap();
    let mut phase = 0_i32;
    let mut symbol = 1_i32;
    let cf32 = case["mode"] == "phase";
    let mut bytes = Vec::new();
    for index in 0..count {
        let mut pair = [0_i16; 2];
        for v in &mut pair {
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            *v = ((state >> 32) % 401) as i16 - 200;
        }
        if kind == "carrier" || (kind == "mixed" && (2 * rate..4 * rate).contains(&index)) {
            phase = (phase + 1) % 16;
            for j in 0..2 {
                pair[j] += 5 * oscillator[phase as usize][j];
            }
        }
        if kind == "fsk" || (kind == "mixed" && (8 * rate..8 * rate + rate / 2).contains(&index)) {
            if index % 6 == 0 {
                symbol = if (state >> 43) & 1 != 0 { 1 } else { -1 };
            }
            phase = (phase + symbol + 16) % 16;
            for j in 0..2 {
                pair[j] += 3 * oscillator[phase as usize][j];
            }
        }
        if kind == "mixed" && index == 6 * rate {
            pair = [32767, -32768];
        }
        for value in pair {
            if cf32 {
                bytes.extend_from_slice(&(value as f32 / 1024.0).to_le_bytes());
            } else {
                bytes.extend_from_slice(&value.to_le_bytes());
            }
        }
    }
    bytes
}
fn compare(actual: &Value, expected: &Value, path: &str, max_error: &mut f64) {
    match (actual, expected) {
        (Value::Number(a), Value::Number(b)) if a.is_f64() || b.is_f64() => {
            let (a, b) = (a.as_f64().unwrap(), b.as_f64().unwrap());
            let error = (a - b).abs() / (1.0 + b.abs());
            *max_error = max_error.max(error);
            assert!(
                error <= 2e-6,
                "{path}: actual={a:.17e}, expected={b:.17e}, relative={error:.4e}"
            );
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "{path}: length");
            for (i, (a, b)) in a.iter().zip(b).enumerate() {
                compare(a, b, &format!("{path}/{i}"), max_error);
            }
        }
        (Value::Object(a), Value::Object(b)) => {
            assert_eq!(a.len(), b.len(), "{path}: fields");
            for (key, b) in b {
                compare(
                    a.get(key).unwrap_or_else(|| panic!("missing {path}/{key}")),
                    b,
                    &format!("{path}/{key}"),
                    max_error,
                );
            }
        }
        _ => assert_eq!(actual, expected, "{path}"),
    }
}
#[test]
fn fixed_python_oracles_preserve_discrete_scheduling_and_close_scores() {
    let fixture: Value = serde_json::from_str(include_str!("triage_oracle.json")).unwrap();
    let oscillator: Vec<[i16; 2]> = serde_json::from_value(fixture["oscillator"].clone()).unwrap();
    for (index, case) in fixture["cases"].as_array().unwrap().iter().enumerate() {
        let mut file = tempfile::NamedTempFile::new().unwrap();
        file.write_all(&fixture_bytes(case, &oscillator)).unwrap();
        file.flush().unwrap();
        assert_eq!(
            input::identity(file.path()).unwrap().sha256,
            case["input_sha256"].as_str().unwrap()
        );
        let config = case["config"].clone();
        let mut actual = match case["mode"].as_str().unwrap() {
            "triage" => triage_ci16le_file(file.path(), &serde_json::from_value(config).unwrap()),
            "phase" => {
                select_phase_windows_cf32(file.path(), &serde_json::from_value(config).unwrap())
            }
            "burst" => select_protocol_neutral_ci16_windows(
                file.path(),
                &serde_json::from_value(config).unwrap(),
            ),
            "routing" => {
                route_ci16le_waveform(file.path(), &serde_json::from_value(config).unwrap())
            }
            _ => unreachable!(),
        }
        .unwrap();
        actual.as_object_mut().unwrap().remove("input_path");
        let mut error = 0.0;
        compare(
            &actual,
            &case["expected"],
            &format!("case{index}"),
            &mut error,
        );
        eprintln!(
            "triage oracle case {index} {}: max relative score error {error:.3e}",
            case["mode"]
        );
    }
}
#[test]
fn coverage_stable_ranking_and_explicit_invalid_configuration() {
    let config = BurstWindowSelectorConfig::default();
    let starts = coverage_starts(23.0, &config).unwrap();
    assert_eq!(starts, vec![0.0, 4.0, 8.0, 12.0, 16.0, 18.0]);
    assert_eq!(
        coverage_audit(&starts, 23.0, 5.0)["entire_capture_covered"],
        true
    );
    assert_eq!(
        rank_with_nms(&[4.0, 4.0, 3.0, 2.0], 3, 0.5, 1.0),
        vec![0, 2]
    );
    assert_eq!(stable_rank(&[2.0, 2.0, 1.0]), vec![0, 1, 2]);
    let mut file = tempfile::NamedTempFile::new().unwrap();
    file.write_all(b"bad").unwrap();
    assert!(triage_ci16le_file(file.path(), &SignalTriageConfig::default()).is_err());
    assert!(select_phase_windows_cf32(file.path(), &PhaseWindowSelectorConfig::default()).is_err());
    assert!(
        select_protocol_neutral_ci16_windows(
            file.path(),
            &BurstWindowSelectorConfig {
                coverage_hop_seconds: Some(6.0),
                ..config
            }
        )
        .is_err()
    );
    assert!(
        route_ci16le_waveform(
            file.path(),
            &WaveformRoutingConfig {
                symbol_rate_bank_baud: vec![1200],
                ..Default::default()
            }
        )
        .is_err()
    );
    let row = json!({"ranking_score":1.0,"active_window_fraction":0.5,"max_fft_peak_to_median_ratio":3.0});
    assert_eq!(
        rank_signal_triage(&[(2, row.clone()), (1, row.clone())]).unwrap()[0].0,
        1
    );
    assert!(rank_signal_triage(&[(1, row.clone()), (1, row)]).is_err());
}
#[test]
fn cf32_nonfinite_and_zero_power_fail_closed() {
    let mut file = tempfile::NamedTempFile::new().unwrap();
    for _ in 0..16 {
        file.write_all(&0.0_f32.to_le_bytes()).unwrap();
    }
    let config = PhaseWindowSelectorConfig {
        sample_rate_hz: 4,
        ..Default::default()
    };
    assert!(
        select_phase_windows_cf32(file.path(), &config)
            .unwrap_err()
            .contains("baseline")
    );
    file.as_file_mut().seek(SeekFrom::Start(0)).unwrap();
    file.write_all(&f32::NAN.to_le_bytes()).unwrap();
    assert!(
        select_phase_windows_cf32(file.path(), &config)
            .unwrap_err()
            .contains("nonfinite")
    );
}

#[test]
fn opened_source_replacement_and_mutation_are_rejected() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("capture.iq");
    fs::write(&source, [1_u8; 64]).unwrap();
    let (file, identity, before) = source_file(&source, 4).unwrap();
    unchanged(&identity, &file, &before).unwrap();
    fs::rename(&source, directory.path().join("original.iq")).unwrap();
    // Identical bytes do not make a replacement inode the opened input.
    fs::write(&source, [1_u8; 64]).unwrap();
    assert!(
        unchanged(&identity, &file, &before)
            .unwrap_err()
            .contains("identity changed")
    );
    let (file, identity, before) = source_file(&source, 4).unwrap();
    fs::write(&source, [2_u8; 64]).unwrap();
    assert!(unchanged(&identity, &file, &before).is_err());
    #[cfg(unix)]
    {
        let link = directory.path().join("link.iq");
        std::os::unix::fs::symlink(&source, &link).unwrap();
        assert!(source_file(&link, 4).is_err());
        let fifo = directory.path().join("fifo.iq");
        let encoded = std::ffi::CString::new(fifo.to_str().unwrap()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(encoded.as_ptr(), 0o600) }, 0);
        assert!(source_file(&fifo, 4).is_err());
    }
}

#[test]
fn general_evidence_format_rounding_matches_python() {
    for (value, expected) in [
        (9999.9, "1e+04"),
        (0.000099999, "0.0001"),
        (9.9999e-6, "1e-05"),
        (999.99, "1000"),
        (-9999.9, "-1e+04"),
        (12.345, "12.35"),
        (0.0012345, "0.001234"),
    ] {
        assert_eq!(general4(value), expected, "{value}");
    }
}

#[test]
fn cf32_mixed_finite_and_overflowing_windows_fail_without_null_metrics() {
    let mut file = tempfile::NamedTempFile::new().unwrap();
    for index in 0..24 {
        let value = if index < 20 { 1.0_f32 } else { 1e30_f32 };
        file.write_all(&value.to_le_bytes()).unwrap();
        file.write_all(&0.0_f32.to_le_bytes()).unwrap();
    }
    let config = PhaseWindowSelectorConfig {
        sample_rate_hz: 4,
        ..Default::default()
    };
    assert!(
        select_phase_windows_cf32(file.path(), &config)
            .unwrap_err()
            .contains("derived power/coherence")
    );
}

#[test]
fn tiny_positive_routing_rate_cannot_overflow_band_indices() {
    let mut file = tempfile::NamedTempFile::new().unwrap();
    for index in 0..32768_i32 {
        file.write_all(&((index % 401 - 200) as i16).to_le_bytes())
            .unwrap();
        file.write_all(&((index * 17 % 401 - 200) as i16).to_le_bytes())
            .unwrap();
    }
    let config = WaveformRoutingConfig {
        sample_rate_hz: 1e-300,
        window_seconds: 3.2768e304,
        psd_fft_size: 16,
        strongest_window_count: 1,
        symbol_rate_bank_baud: vec![9600],
    };
    let outcome = std::panic::catch_unwind(|| route_ci16le_waveform(file.path(), &config));
    assert!(
        outcome.is_ok(),
        "finite metadata must not cause an index overflow panic"
    );
    assert!(outcome.unwrap().is_ok());
}
