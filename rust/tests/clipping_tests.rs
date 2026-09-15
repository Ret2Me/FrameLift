use super::*;
use serde_json::Value;

fn oracle() -> Value {
    serde_json::from_str(include_str!("clipping_fixtures/oracle.json")).unwrap()
}

fn fixture_root() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("rust/tests/clipping_fixtures")
}

fn close(actual: f64, expected: f64, absolute: f64, relative: f64, label: &str) {
    assert!(
        actual.is_finite() && expected.is_finite(),
        "{label}: nonfinite value"
    );
    let tolerance = absolute + relative * expected.abs();
    assert!(
        (actual - expected).abs() <= tolerance,
        "{label}: got {actual:.17e}, expected {expected:.17e}, abs delta {:.4e}, limit {tolerance:.4e}",
        (actual - expected).abs()
    );
}

fn assert_window_equivalent(actual: &PhaseWindowCandidate, expected: &PhaseWindowCandidate) {
    assert_eq!(actual.analysis_index, expected.analysis_index);
    assert_eq!(
        actual.analysis_start_seconds,
        expected.analysis_start_seconds
    );
    assert_eq!(actual.decoder_start_seconds, expected.decoder_start_seconds);
    assert_eq!(
        actual.endpoint_clip_fraction,
        expected.endpoint_clip_fraction
    );
    close(
        actual.mean_power,
        expected.mean_power,
        1e-6,
        1e-14,
        "window mean power",
    );
    close(
        actual.lag1_phase_coherence,
        expected.lag1_phase_coherence,
        2e-14,
        0.0,
        "window coherence",
    );
    close(
        actual.score,
        expected.score,
        2e-13,
        0.0,
        "window selection score",
    );
}

#[test]
fn frozen_clipping_oracle_and_iq_are_untampered() {
    let oracle = oracle();
    assert_eq!(oracle["schema"], "clipping-offline-python-oracle-v1");
    assert_eq!(oracle["runtime_dependency"], false);
    for source in oracle["sources"].as_array().unwrap() {
        let basename = Path::new(source["path"].as_str().unwrap())
            .file_name()
            .unwrap();
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("src/telemetry_yield")
            .join(basename);
        assert_eq!(input::identity(&path).unwrap().sha256, source["sha256"]);
    }
    for case in oracle["cases"].as_array().unwrap() {
        let path = fixture_root().join(case["filename"].as_str().unwrap());
        assert_eq!(input::identity(&path).unwrap().sha256, case["input_sha256"]);
    }
}

#[test]
fn window_selection_and_phase_first_match_offline_python() {
    for case in oracle()["cases"].as_array().unwrap() {
        let config: BlindPhaseFskConfig = serde_json::from_value(case["config"].clone()).unwrap();
        let path = fixture_root().join(case["filename"].as_str().unwrap());
        let actual = select_ci16_phase_windows(&path, &config).unwrap();
        let expected: Vec<PhaseWindowCandidate> =
            serde_json::from_value(case["window_selector"].clone()).unwrap();
        assert_eq!(actual.len(), expected.len());
        for (a, e) in actual.iter().zip(&expected) {
            assert_window_equivalent(a, e);
        }
        let mut file = File::open(&path).unwrap();
        let raw = read_pairs(&mut file, 28_800).unwrap();
        let reconstructed = constant_radius_declip_exact_endpoints(&raw, 3.0).unwrap();
        let actual = phase_first(&reconstructed, 1, &config).unwrap();
        let expected: Vec<f64> = serde_json::from_value(case["phase_first_lag1"].clone()).unwrap();
        assert_eq!(actual.samples.len(), expected.len());
        for (index, (a, e)) in actual.samples.iter().zip(&expected).enumerate() {
            close(*a, *e, 1e-10, 1e-13, &format!("phase-first sample {index}"));
        }
    }
}

#[test]
fn blind_native_decode_matches_python_bytes_and_ordered_discrete_provenance() {
    let oracle = oracle();
    let mut positive_cases = 0;
    let mut documented_legacy_misses = 0;
    for case in oracle["cases"].as_array().unwrap() {
        let config: BlindPhaseFskConfig = serde_json::from_value(case["config"].clone()).unwrap();
        let selected: Vec<PhaseWindowCandidate> =
            serde_json::from_value(case["selected"].clone()).unwrap();
        let path = fixture_root().join(case["filename"].as_str().unwrap());
        let result = decode_clipping_robust_ax25_ci16(&path, &config, Some(&selected)).unwrap();
        assert_eq!(
            result.frontend_input_representation,
            "constant_radius_endpoint_reconstruction_not_recorded_IQ"
        );
        assert!(result.repaired_frames_require_external_verification);
        let mut actual = serde_json::to_value(result).unwrap();
        let mut expected = case["decode"].clone();
        actual.as_object_mut().unwrap().remove("input_path");
        expected.as_object_mut().unwrap().remove("input_path");
        actual
            .as_object_mut()
            .unwrap()
            .remove("frontend_input_representation");
        actual
            .as_object_mut()
            .unwrap()
            .remove("repaired_frames_require_external_verification");
        let frames = actual["frames"].as_array_mut().unwrap();
        let expected_frames = expected["frames"].as_array_mut().unwrap();
        assert_eq!(
            frames.len(),
            expected_frames.len(),
            "case {} frame count",
            case["name"]
        );
        if !frames.is_empty() {
            positive_cases += 1;
        }
        if case["known_synthetic_frame_present"] == true
            && case["reference_recovered_known_frame"] == false
        {
            assert!(frames.is_empty());
            documented_legacy_misses += 1;
        }
        for (a, e) in frames.iter_mut().zip(expected_frames.iter_mut()) {
            // Floating reductions are evaluated separately; no tolerance applies
            // to payload/FCS, bit flips, timing bank choices, stage or ordering.
            for name in ["score", "threshold"] {
                close(
                    a["timing"][name].as_f64().unwrap(),
                    e["timing"][name].as_f64().unwrap(),
                    2e-12,
                    2e-13,
                    name,
                );
                a["timing"].as_object_mut().unwrap().remove(name);
                e["timing"].as_object_mut().unwrap().remove(name);
            }
            close(
                a["estimated_frame_start_seconds"].as_f64().unwrap(),
                e["estimated_frame_start_seconds"].as_f64().unwrap(),
                1e-14,
                0.0,
                "estimated start",
            );
            a.as_object_mut()
                .unwrap()
                .remove("estimated_frame_start_seconds");
            e.as_object_mut()
                .unwrap()
                .remove("estimated_frame_start_seconds");
            let reliabilities = a["flipped_symbol_reliabilities"].as_array().unwrap();
            let reference_reliabilities = e["flipped_symbol_reliabilities"].as_array().unwrap();
            assert_eq!(reliabilities.len(), reference_reliabilities.len());
            for (ar, er) in reliabilities.iter().zip(reference_reliabilities) {
                close(
                    ar.as_f64().unwrap(),
                    er.as_f64().unwrap(),
                    1e-10,
                    1e-13,
                    "flip reliability",
                );
            }
            a.as_object_mut()
                .unwrap()
                .remove("flipped_symbol_reliabilities");
            e.as_object_mut()
                .unwrap()
                .remove("flipped_symbol_reliabilities");
        }
        assert_eq!(
            actual, expected,
            "case {} exact discrete contract",
            case["name"]
        );
    }
    assert_eq!(
        positive_cases, 1,
        "do not pass a vacuous all-zero receiver oracle"
    );
    assert_eq!(
        documented_legacy_misses, 1,
        "legacy plain-FSK miss must not be called a true negative"
    );
}

#[test]
fn projected_declipping_matches_offline_python_with_separate_numeric_tolerance() {
    let oracle = oracle();
    let reference = &oracle["projection"];
    let components: Vec<[i16; 2]> =
        serde_json::from_value(reference["components"].clone()).unwrap();
    let outputs = projected_bandlimited_declipping(
        &components,
        reference["sample_rate_hz"].as_f64().unwrap(),
        reference["bandlimit_hz"].as_f64().unwrap(),
        &[1, 3, 5],
    )
    .unwrap();
    for (iteration, actual) in outputs {
        let expected = &reference["checkpoints"][iteration.to_string()];
        let pairs: Vec<[f64; 2]> =
            serde_json::from_value(expected["reconstruction"].clone()).unwrap();
        assert_eq!(actual.reconstruction.len(), pairs.len());
        for (index, (a, e)) in actual.reconstruction.iter().zip(&pairs).enumerate() {
            for j in 0..2 {
                close(
                    a[j],
                    e[j],
                    1e-8,
                    1e-14,
                    &format!("projection {iteration}:{index}:{j}"),
                );
            }
        }
        let metrics = serde_json::to_value(actual.metrics).unwrap();
        for (key, value) in expected["metrics"].as_object().unwrap() {
            if value.is_f64() {
                close(
                    metrics[key].as_f64().unwrap(),
                    value.as_f64().unwrap(),
                    1e-10,
                    1e-12,
                    key,
                );
            } else {
                assert_eq!(&metrics[key], value, "discrete projection metric {key}");
            }
        }
    }
}

#[test]
fn repaired_rank_uses_full_frame_lexical_order_not_payload_tuple_order() {
    let mut a = sample_detection(1, 10.0, vec![1]);
    let mut b = a.clone();
    a.payload = vec![1];
    a.fcs = vec![3, 4];
    b.payload = vec![1, 2];
    b.fcs = vec![5, 6];
    assert_eq!(repaired_compare(&a, &b, 0.05), Ordering::Greater);
}

#[test]
#[ignore = "bounded real-IQ qualification, explicit output path required; not an ordinary unit test"]
fn real_iq_13168691_selected_positive_window_parity() {
    use sha2::{Digest, Sha256};
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let data_root = std::env::var_os("TELEMETRY_QUALIFICATION_DATA_ROOT")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| root.to_path_buf());
    let reference_path =
        data_root.join("work/blind-phase-fsk/dev13168691-blind-result-v4-3-final.json");
    let reference_identity = input::identity(&reference_path).unwrap();
    assert_eq!(
        reference_identity.sha256,
        "ef68cea4067d9febf0c5dc91d26a5fd0aa70a4fd5cde1115a68c168a8ab758ff"
    );
    let reference = input::read_json(&reference_path).unwrap();
    let freeze_path = data_root.join("reports/blind-phase-fsk-dev13168691-v4-3-final-freeze.json");
    assert_eq!(
        input::identity(&freeze_path).unwrap().sha256,
        "dc304fa91fb2c28e724f2134dec8813e914ccdfa535cf2844fe52f11e13efbc3"
    );
    let freeze = input::read_json(&freeze_path).unwrap();
    assert_eq!(freeze["identity"], reference["freeze"]["identity"]);
    for (path, hash) in freeze["identity"]["source_dependencies"]
        .as_object()
        .unwrap()
    {
        assert_eq!(
            input::identity(&data_root.join(path)).unwrap().sha256,
            hash.as_str().unwrap(),
            "frozen Python source {path}"
        );
    }
    let config: BlindPhaseFskConfig = serde_json::from_value(reference["config"].clone()).unwrap();
    assert_eq!(
        serde_json::to_value(&config).unwrap(),
        freeze["identity"]["config"]
    );
    assert_eq!(
        hex::encode(Sha256::digest(
            crate::ledger::canonical_json(&reference["config"])
                .unwrap()
                .as_bytes()
        )),
        reference["config_sha256"]
    );
    let path = data_root.join(reference["input"]["path"].as_str().unwrap());
    let source = input::identity(&path).unwrap();
    assert_eq!(source.sha256, reference["input"]["sha256"]);
    assert_eq!(
        source.bytes,
        reference["input"]["size_bytes"].as_u64().unwrap()
    );

    // Disclosed fixed subset: second of four historical selected windows.
    // Selection is replayed independently, but only this positive window is
    // decoded to bound the qualification, with every original budget unchanged.
    let selected: Vec<PhaseWindowCandidate> =
        serde_json::from_value(reference["selection"].clone()).unwrap();
    let measured_selection = select_ci16_phase_windows(&path, &config).unwrap();
    assert_eq!(measured_selection.len(), selected.len());
    for (a, e) in measured_selection.iter().zip(&selected) {
        assert_window_equivalent(a, e);
    }
    let window = selected[1].clone();
    assert_eq!(window.decoder_start_seconds, 444.75);
    let rust_dependencies = [
        "rust/clipping.rs",
        "rust/dsp.rs",
        "rust/soft.rs",
        "rust/protocol.rs",
        "rust/input.rs",
        "rust/tests/clipping_tests.rs",
    ];
    let source_before: Vec<_> = rust_dependencies
        .iter()
        .map(|name| input::identity(&root.join(name)).unwrap())
        .collect();
    let executable = input::identity(&std::env::current_exe().unwrap()).unwrap();
    let out = input::existing_new_dir(Path::new(
        &std::env::var("TELEMETRY_CLIPPING_REAL_IQ_AUDIT_OUTPUT")
            .expect("set explicit new audit output directory"),
    ))
    .unwrap();
    input::write_json_new(&out.join("plan.json"), &serde_json::json!({
        "schema":"rust-clipping-real-iq-subset-qualification-plan-v1", "reference":reference_identity,
        "config":config,"source":source,"selected_reference_index":1,"selected_window":window,
        "compiled_test_executable":executable,"rust_sources_before":source_before,
        "development_replay":true,"new_telemetry_yield_claim":false,"full_observation_parity_claim":false
    })).unwrap();
    let started = std::time::Instant::now();
    let result =
        match decode_clipping_robust_ax25_ci16(&path, &config, Some(std::slice::from_ref(&window)))
        {
            Ok(result) => result,
            Err(error) => {
                input::write_json_new(
                    &out.join("result.json"),
                    &serde_json::json!({"status":"failed","error":error}),
                )
                .unwrap();
                panic!("bounded real-IQ replay failed: {error}");
            }
        };
    let elapsed = started.elapsed().as_secs_f64();
    input::write_json_new(&out.join("result.json"), &result).unwrap();
    let expected: Vec<_> = reference["detections"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|frame| {
            frame["decoder_start_seconds"].as_f64() == Some(window.decoder_start_seconds)
        })
        .cloned()
        .collect();
    assert_eq!(
        expected.len(),
        3,
        "fixed existing reference must contain one native and two repaired candidates"
    );
    let mut actual_documents = Vec::new();
    for frame in &result.frames {
        actual_documents.push(serde_json::json!({
            "payload_length_bytes":frame.payload.len(),"payload_sha256":hex::encode(Sha256::digest(&frame.payload)),
            "payload_hex":hex::encode(&frame.payload),"fcs_hex":hex::encode(&frame.fcs),
            "decoder_start_seconds":frame.decoder_start_seconds,"estimated_frame_start_seconds":frame.estimated_frame_start_seconds,
            "constant_radius_factor":frame.constant_radius_factor,"phase_difference_lag":frame.phase_difference_lag,
            "g3ruh_descramble":frame.g3ruh_descramble,"timing":frame.timing,
            "frame_start_symbol":frame.frame_start_symbol,"frame_stop_symbol":frame.frame_stop_symbol,
            "flipped_symbol_indices":frame.flipped_symbol_indices,"flipped_symbol_reliabilities":frame.flipped_symbol_reliabilities,
            "search_stage":frame.search_stage,"attempted_candidates":frame.attempted_candidates
        }));
    }
    let frame_set = |documents: &[Value]| -> BTreeSet<String> {
        documents
            .iter()
            .map(|v| {
                format!(
                    "{}{}",
                    v["payload_hex"].as_str().unwrap(),
                    v["fcs_hex"].as_str().unwrap()
                )
            })
            .collect()
    };
    let actual_set = frame_set(&actual_documents);
    let expected_set = frame_set(&expected);
    let missing: Vec<_> = expected_set
        .difference(&actual_set)
        .map(|bytes| hex::encode(Sha256::digest(hex::decode(bytes).unwrap())))
        .collect();
    let additional: Vec<_> = actual_set
        .difference(&expected_set)
        .map(|bytes| hex::encode(Sha256::digest(hex::decode(bytes).unwrap())))
        .collect();
    let strip_numeric = |mut document: Value| {
        document["timing"].as_object_mut().unwrap().remove("score");
        document["timing"]
            .as_object_mut()
            .unwrap()
            .remove("threshold");
        document
            .as_object_mut()
            .unwrap()
            .remove("estimated_frame_start_seconds");
        document
            .as_object_mut()
            .unwrap()
            .remove("flipped_symbol_reliabilities");
        document
    };
    let exact_provenance = actual_documents
        .iter()
        .cloned()
        .map(&strip_numeric)
        .collect::<Vec<_>>()
        == expected
            .iter()
            .cloned()
            .map(&strip_numeric)
            .collect::<Vec<_>>();
    let mut numeric_deltas = Vec::new();
    let mut numeric_pass = actual_documents.len() == expected.len();
    if exact_provenance {
        for (index, (a, e)) in actual_documents.iter().zip(&expected).enumerate() {
            let mut fields = vec![
                (
                    "timing.score",
                    a["timing"]["score"].as_f64().unwrap(),
                    e["timing"]["score"].as_f64().unwrap(),
                ),
                (
                    "timing.threshold",
                    a["timing"]["threshold"].as_f64().unwrap(),
                    e["timing"]["threshold"].as_f64().unwrap(),
                ),
                (
                    "estimated_frame_start_seconds",
                    a["estimated_frame_start_seconds"].as_f64().unwrap(),
                    e["estimated_frame_start_seconds"].as_f64().unwrap(),
                ),
            ];
            let ar = a["flipped_symbol_reliabilities"].as_array().unwrap();
            let er = e["flipped_symbol_reliabilities"].as_array().unwrap();
            numeric_pass &= ar.len() == er.len();
            for (a, e) in ar.iter().zip(er) {
                fields.push((
                    "flipped_symbol_reliability",
                    a.as_f64().unwrap(),
                    e.as_f64().unwrap(),
                ));
            }
            for (field, actual, expected) in fields {
                let delta = (actual - expected).abs();
                numeric_pass &= delta <= 1e-10 + 1e-12 * expected.abs();
                if delta != 0.0 {
                    numeric_deltas.push(
                        serde_json::json!({"index":index,"field":field,"absolute_delta":delta}),
                    );
                }
            }
        }
    } else {
        numeric_pass = false;
    }
    let independently_validated = result.frames.iter().all(|frame| {
        let mut register = 0xffff_u16;
        for byte in &frame.payload {
            for bit in 0..8 {
                let feedback = (register ^ u16::from((byte >> bit) & 1)) & 1;
                register >>= 1;
                if feedback != 0 {
                    register ^= 0x8408;
                }
            }
        }
        frame.fcs == (register ^ 0xffff).to_le_bytes()
            && crate::protocol::parse_ax25_ui(&frame.payload).is_some()
    });
    let source_after: Vec<_> = rust_dependencies
        .iter()
        .map(|name| input::identity(&root.join(name)).unwrap())
        .collect();
    let rust_sources_unchanged = source_before
        .iter()
        .zip(&source_after)
        .all(|(a, b)| a.sha256 == b.sha256);
    let pass = !expected.is_empty()
        && missing.is_empty()
        && additional.is_empty()
        && exact_provenance
        && numeric_pass
        && independently_validated
        && rust_sources_unchanged;
    let summary = serde_json::json!({
        "schema":"rust-clipping-real-iq-subset-audit-v1","pass":pass,"scope":"one disclosed existing development CI16 window, not full capture or holdout",
        "selected_reference_index":1,"decoder_start_seconds":window.decoder_start_seconds,"window_seconds":config.decoder_window_seconds,
        "expected_detections":expected.len(),"actual_detections":result.frames.len(),"native_detections":result.frames.iter().filter(|f|f.flipped_symbol_indices.is_empty()).count(),
        "repaired_candidates":result.frames.iter().filter(|f|!f.flipped_symbol_indices.is_empty()).count(),
        "missing_full_frame_sha256":missing,"additional_full_frame_sha256":additional,"exact_ordered_discrete_provenance":exact_provenance,
        "numeric_tolerance_pass":numeric_pass,"numeric_deltas":numeric_deltas,"independent_bitwise_fcs_and_ax25_structure_valid":independently_validated,
        "repaired_candidates_authenticated":false,"rust_sources_unchanged_during_run":rust_sources_unchanged,"elapsed_seconds":elapsed,
        "protocol_candidates_attempted":result.protocol_candidates_attempted,"native_protocol_candidates_attempted":result.native_protocol_candidates_attempted,
        "repair_protocol_candidates_attempted":result.repair_protocol_candidates_attempted,"historical_subset_attempt_count_available":false,
        "publication_ready":false,"deployment_ready":false,"result":input::identity(&out.join("result.json")).unwrap()
    });
    input::write_json_new(&out.join("audit.json"), &summary).unwrap();
    println!(
        "{}",
        serde_json::json!({"pass":pass,"actual":result.frames.len(),"expected":expected.len(),"elapsed_seconds":elapsed,"audit":out.join("audit.json")})
    );
    assert!(
        pass,
        "bounded real-IQ audit failed; see persisted audit, no broad accuracy claim is permitted"
    );
}

fn sample_detection(lag: usize, start: f64, flips: Vec<usize>) -> ClippingRobustAx25Frame {
    ClippingRobustAx25Frame {
        payload: b"payload".to_vec(),
        fcs: vec![0, 0],
        decoder_start_seconds: 8.0,
        estimated_frame_start_seconds: start,
        constant_radius_factor: 3.0,
        phase_difference_lag: lag,
        g3ruh_descramble: true,
        timing: dsp::TimingHypothesis {
            rate_error_ppm: 0.0,
            phase_samples: 0.5,
            step_samples: 2.0,
            threshold: 0.0,
            score: 1.0,
            symbol_count: 256,
        },
        frame_start_symbol: 100,
        frame_stop_symbol: 200,
        flipped_symbol_reliabilities: vec![0.01; flips.len()],
        flipped_symbol_indices: flips,
        search_stage: "test_consensus_only_not_validated_telemetry".into(),
        attempted_candidates: 10,
    }
}

fn scheduler_setup() -> (
    std::path::PathBuf,
    BlindPhaseFskConfig,
    Vec<PhaseWindowCandidate>,
) {
    let oracle = oracle();
    let case = &oracle["cases"][1];
    let mut config: BlindPhaseFskConfig = serde_json::from_value(case["config"].clone()).unwrap();
    config.phase_bins = 4;
    config.short_search_timing_hypotheses = 1;
    config.deep_search_timing_hypotheses = 1;
    config.phase_difference_lags = vec![1];
    config.descramble_modes = vec![true];
    let selected = serde_json::from_value(case["selected"].clone()).unwrap();
    (
        fixture_root().join(case["filename"].as_str().unwrap()),
        config,
        selected,
    )
}

fn scheduler_path(index: usize) -> ReceiverPath {
    let mut timing = sample_detection(1, 0.0, vec![]).timing;
    timing.score = 17.0 - index as f64;
    ReceiverPath {
        radius: 3.0,
        lag: 1,
        descramble: false,
        rank: 0,
        timing,
        soft: Arc::new(vec![0.0; 512]),
        start: 100 + index,
        estimated: index as f64,
    }
}

fn mock_search_result(
    attempts: usize,
    frames: Vec<soft::SoftDecodedFrame>,
) -> soft::SoftListResult {
    soft::SoftListResult {
        threshold: 0.0,
        exact_flag_count: 0,
        eligible_regions: 1,
        searched_regions: 1,
        attempted_candidates: attempts,
        budget_exhausted: false,
        frames,
        output_limit_reached: false,
    }
}

fn mock_native(start: usize) -> soft::SoftDecodedFrame {
    soft::SoftDecodedFrame {
        // Intentionally not integrity-validated; only private scheduler injection
        // consumes this fixture. No exported result claims it is real telemetry.
        frame_with_fcs: b"scheduler-only-not-telemetry\x00\x00".to_vec(),
        left_flag_bit: start,
        left_flag_hamming: 0,
        right_flag_bit: start + 200,
        flipped_symbol_indices: vec![],
        flipped_symbol_reliabilities: vec![],
    }
}

#[test]
fn native_sweep_reaches_event_beyond_repair_cap_before_any_repair() {
    let (path, mut config, selected) = scheduler_setup();
    config.maximum_regions_per_start = 1;
    config.repair_path_maximum_attempts = 1;
    config.repair_region_maximum_attempts = 1;
    config.repair_event_maximum_attempts = 1;
    config.repair_window_maximum_events = 16;
    config.repair_window_maximum_attempts = 16;
    let mut calls = Vec::new();
    let result = decode_ci16_with(
        &path,
        &config,
        Some(&selected),
        |_, _| (0..17).map(|index| vec![scheduler_path(index)]).collect(),
        |_, _, budget, options| {
            let start = options.event_start_symbol.unwrap() as usize;
            let native = budget.maximum_flips == 0;
            calls.push((native, start));
            Ok(mock_search_result(
                1,
                if native && start == 116 {
                    vec![mock_native(start)]
                } else {
                    vec![]
                },
            ))
        },
    )
    .unwrap();
    let expected: Vec<_> = (100..117)
        .map(|start| (true, start))
        .chain((100..116).map(|start| (false, start)))
        .collect();
    assert_eq!(calls, expected);
    assert_eq!(result.frames.len(), 1);
    assert_eq!(result.frames[0].search_stage, "native_all_paths");
    assert_eq!(result.protocol_candidates_attempted, 33);
    assert_eq!(result.native_protocol_candidates_attempted, 17);
    assert_eq!(result.repair_protocol_candidates_attempted, 16);
}

#[test]
fn deep_budget_never_increased_and_output_limit_fails_closed() {
    let (path, mut config, selected) = scheduler_setup();
    config.deep_maximum_attempts = 7;
    config.repair_path_maximum_attempts = 100;
    config.repair_event_maximum_attempts = 100;
    config.repair_region_maximum_attempts = 7;
    config.repair_window_maximum_attempts = 100;
    for force_limit in [false, true] {
        let mut budgets = Vec::new();
        let result = decode_ci16_with(
            &path,
            &config,
            Some(&selected),
            |_, _| vec![vec![scheduler_path(0)]],
            |_, _, budget, _| {
                let mut result = mock_search_result(0, vec![]);
                if budget.maximum_flips > 0 {
                    budgets.push(budget.maximum_attempts);
                    result.output_limit_reached = force_limit;
                }
                Ok(result)
            },
        );
        assert_eq!(budgets, vec![7]);
        if force_limit {
            assert!(result.unwrap_err().contains("repair path output bound"));
        } else {
            assert!(result.unwrap().frames.is_empty());
        }
    }
}

#[test]
fn shared_repair_window_budget_cannot_be_reset_by_next_event() {
    let (path, mut config, selected) = scheduler_setup();
    config.deep_maximum_attempts = 7;
    config.repair_path_maximum_attempts = 7;
    config.repair_region_maximum_attempts = 7;
    config.repair_event_maximum_attempts = 10;
    config.repair_window_maximum_attempts = 12;
    let mut budgets = Vec::new();
    let result = decode_ci16_with(
        &path,
        &config,
        Some(&selected),
        |_, _| {
            vec![
                vec![scheduler_path(0), scheduler_path(1)],
                vec![scheduler_path(2)],
            ]
        },
        |_, _, budget, _| {
            if budget.maximum_flips == 0 {
                Ok(mock_search_result(0, vec![]))
            } else {
                budgets.push(budget.maximum_attempts);
                Ok(mock_search_result(budget.maximum_attempts, vec![]))
            }
        },
    )
    .unwrap();
    assert_eq!(budgets, vec![7, 3, 2]);
    assert_eq!(result.repair_protocol_candidates_attempted, 12);
}

#[test]
fn native_detection_cap_fails_closed_without_truncating_success() {
    let (path, mut config, selected) = scheduler_setup();
    config.maximum_frame_detections = 1;
    let error = decode_ci16_with(
        &path,
        &config,
        Some(&selected),
        |_, _| vec![vec![scheduler_path(0)], vec![scheduler_path(1)]],
        |_, _, _, options| {
            Ok(mock_search_result(
                0,
                vec![mock_native(options.event_start_symbol.unwrap() as usize)],
            ))
        },
    )
    .unwrap_err();
    assert!(error.contains("frame detection bound exceeded"));
}

#[test]
fn receiver_path_cap_fails_before_clustering() {
    let oracle = oracle();
    let case = &oracle["cases"][1];
    let mut config: BlindPhaseFskConfig = serde_json::from_value(case["config"].clone()).unwrap();
    config.maximum_receiver_paths_per_window = 1;
    let selected: Vec<PhaseWindowCandidate> =
        serde_json::from_value(case["selected"].clone()).unwrap();
    let path = fixture_root().join(case["filename"].as_str().unwrap());
    let error = decode_clipping_robust_ax25_ci16(&path, &config, Some(&selected)).unwrap_err();
    assert!(error.contains("receiver path bound exceeded"));
}

#[test]
fn correlated_frontends_never_authenticate_repaired_candidate() {
    let result = group_ax25_frame_consensus(
        &[
            sample_detection(1, 10.0, vec![100]),
            sample_detection(3, 10.01, vec![100]),
        ],
        0.025,
    )
    .unwrap();
    assert_eq!(result.len(), 1);
    assert!(result[0].has_correction_consensus);
    assert!(!result[0].has_uncorrected_detection);
    assert!(!result[0].native_trusted);
    assert_eq!(result[0].independent_frontends, vec![(3.0, 1), (3.0, 3)]);
}

#[test]
fn only_uncorrected_detection_sets_legacy_native_trust_flag() {
    let result = group_ax25_frame_consensus(&[sample_detection(1, 10.0, vec![])], 0.025).unwrap();
    assert!(result[0].has_uncorrected_detection);
    assert!(result[0].native_trusted);
    assert!(!result[0].has_correction_consensus);
}

#[test]
fn consensus_does_not_chain_beyond_first_detection_tolerance() {
    let result = group_ax25_frame_consensus(
        &[
            sample_detection(1, 10.0, vec![1]),
            sample_detection(2, 10.02, vec![1]),
            sample_detection(3, 10.04, vec![1]),
        ],
        0.025,
    )
    .unwrap();
    assert_eq!(
        result
            .iter()
            .map(|g| g.detections.len())
            .collect::<Vec<_>>(),
        vec![2, 1]
    );
}

#[test]
fn exact_endpoints_do_not_reclassify_minus_32767() {
    let raw = [[-32767, 100], [-32768, 100], [32767, 100], [32767, -32768]];
    let result = constant_radius_declip_exact_endpoints(&raw, 3.0).unwrap();
    assert_eq!(result[0].re, -32767.0);
    assert_eq!(result[0].im, 100.0);
    assert!(result[1].re < -32768.0);
    assert!(result[2].re > 32767.0);
    let one = constant_radius_declip_exact_endpoints(&raw, 1.0).unwrap();
    // Legacy radius model is not the separate inequality-constrained model.
    assert!(one[3].re < 32767.0);
}

#[test]
fn decimation_preserves_existing_9600_and_19200_profiles() {
    let mut config = BlindPhaseFskConfig::default();
    assert_eq!(frontend_decimation_and_sps(&config).unwrap(), (3, 2.0));
    config.baudrate = 19200.0;
    assert_eq!(frontend_decimation_and_sps(&config).unwrap(), (1, 3.0));
    config.baudrate = 38400.0;
    assert!(config.validate().is_err());
}

#[test]
fn invalid_budgets_and_nonfinite_hypotheses_are_rejected() {
    let mut config = BlindPhaseFskConfig::default();
    config.validate().unwrap();
    config.descramble_modes = vec![true, true];
    assert!(config.validate().is_err());
    config = BlindPhaseFskConfig::default();
    config.repair_event_maximum_attempts = 99;
    assert!(config.validate().is_err());
    config = BlindPhaseFskConfig::default();
    config.constant_radius_factors = vec![f64::NAN];
    assert!(config.validate().is_err());
    config = BlindPhaseFskConfig::default();
    config.rate_errors_ppm = vec![f64::NAN];
    assert!(config.validate().is_err());
    config = BlindPhaseFskConfig::default();
    config.phase_bins = 4;
    assert!(config.validate().is_err());
}

#[test]
fn reconstruction_preserves_exact_observation_constraints() {
    let components: Vec<_> = (0..512)
        .map(|i| match i % 4 {
            0 => [32767, -32768],
            1 => [-32767, 100],
            2 => [100, 32767],
            _ => [-32768, 50],
        })
        .collect();
    let outputs =
        projected_bandlimited_declipping(&components, 57600.0, 12000.0, &[5, 1, 3, 3]).unwrap();
    assert_eq!(outputs.keys().copied().collect::<Vec<_>>(), vec![1, 3, 5]);
    for checkpoint in outputs.values() {
        assert_eq!(checkpoint.metrics.maximum_unclipped_constraint_error, 0.0);
        for (observed, reconstructed) in components.iter().zip(&checkpoint.reconstruction) {
            for i in 0..2 {
                match observed[i] {
                    i16::MIN => assert!(reconstructed[i] <= -32768.0),
                    i16::MAX => assert!(reconstructed[i] >= 32767.0),
                    x => assert_eq!(reconstructed[i], x as f64),
                }
            }
        }
        assert_eq!(
            checkpoint.representation,
            "constrained_reconstruction_not_recorded_IQ"
        );
    }
}

#[test]
fn unbounded_or_empty_projection_requests_fail_closed() {
    let data = vec![[0, 0]; 256];
    assert!(projected_bandlimited_declipping(&data, 57600.0, 30000.0, &[1]).is_err());
    assert!(projected_bandlimited_declipping(&data, 57600.0, 12000.0, &[]).is_err());
    assert!(projected_bandlimited_declipping(&data, 57600.0, 12000.0, &[0]).is_err());
    assert!(projected_bandlimited_declipping(&data, 57600.0, 12000.0, &[10001]).is_err());
    let data = vec![[0, 0]; 4096];
    let too_many_checkpoints: Vec<_> = (1..=10_000).collect();
    assert!(
        projected_bandlimited_declipping(&data, 57600.0, 12000.0, &too_many_checkpoints)
            .unwrap_err()
            .contains("output exceeds")
    );
}

#[test]
fn partial_ci16_pairs_and_empty_selected_windows_are_rejected() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("capture.ci16");
    std::fs::write(&path, [1, 2, 3]).unwrap();
    assert!(select_ci16_phase_windows(&path, &BlindPhaseFskConfig::default()).is_err());
    std::fs::write(&path, [1, 2, 3, 4]).unwrap();
    assert!(
        decode_clipping_robust_ax25_ci16(&path, &BlindPhaseFskConfig::default(), Some(&[]))
            .is_err()
    );
}
