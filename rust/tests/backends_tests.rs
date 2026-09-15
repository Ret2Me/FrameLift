use super::*;
use std::fs;

fn capture(root: &Path) -> CaptureSegment {
    let path = root.join("capture;not-a-shell-command.cf32");
    fs::write(&path, [0; 32]).unwrap();
    CaptureSegment {
        path,
        sample_format: "cf32_le".into(),
        sample_rate_hz: 48000.0,
        start_sample: 0,
        sample_count: None,
        hints: Map::new(),
    }
}
fn config() -> ExternalBackendConfig {
    ExternalBackendConfig {
        backend_id: "example_decoder".into(),
        backend_version: "1.2.3".into(),
        capabilities: ExternalBackendCapabilities {
            modulations: vec!["FSK".into(), "BPSK".into()],
            framing: vec!["AX25".into(), "CCSDS_TM".into()],
            fec: vec!["none".into(), "convolutional".into()],
            sample_formats: vec!["cf32_le".into()],
        },
        timeout_seconds: 2.0,
        max_output_bytes: 4096,
        max_file_bytes: 1048576,
        environment_overrides: BTreeMap::new(),
    }
}
fn run_command(
    config: &ExternalBackendConfig,
    segment: &CaptureSegment,
    argv: &[&str],
) -> ExternalBackendResult {
    run_external_backend(
        config,
        segment,
        |_| Ok(argv.iter().map(|s| (*s).to_owned()).collect()),
        |_, _| Ok(vec![]),
    )
}
fn profile() -> GrSatellitesProfile {
    GrSatellitesProfile {
        selector: "CANVAS".into(),
        selector_kind: ProfileSelectorKind::Name,
        name: None,
        norad_id: None,
        transmitters: vec![formats::SatYamlTransmitter {
            transmitter_id: "UHF telemetry".into(),
            modulation: "FSK".into(),
            framing: "AX.25 G3RUH".into(),
            fec: vec!["none".into()],
            metadata: json!({"baudrate":9600,"frequency":437250000})
                .as_object()
                .unwrap()
                .clone(),
        }],
    }
}
fn gr_backend() -> GrSatellitesBackend {
    GrSatellitesBackend {
        executable: "/usr/bin/gr_satellites".into(),
        executable_version: "5.9.0".into(),
        profile: profile(),
        timeout_seconds: 2.0,
        max_output_bytes: 4096,
        max_kiss_bytes: 4096,
        supports_rawint16_iq: true,
        supports_rawfile_complex64: true,
        backend_id: "gr_satellites".into(),
    }
}

#[test]
fn python_g17_all_finite_scales_match_frozen_oracle() {
    let oracle: Value = serde_json::from_str(include_str!("backends_g17_oracle.json")).unwrap();
    for case in oracle["cases"].as_array().unwrap() {
        let value = case["value"].as_f64().unwrap();
        assert_eq!(
            format_g17(value),
            case["expected"].as_str().unwrap(),
            "{value:?}"
        );
    }
}

#[test]
fn frozen_python_candidate_ordering_and_gr_argv_contracts_match() {
    let oracle: Value =
        serde_json::from_str(include_str!("backends_contract_oracle.json")).unwrap();
    let segment = CaptureSegment {
        path: "/FIXTURE/input.cf32".into(),
        sample_format: "cf32_le".into(),
        sample_rate_hz: 48000.0,
        start_sample: 0,
        sample_count: None,
        hints: Map::new(),
    };
    for (index, case) in oracle["candidate_cases"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        let candidates = case["input"]
            .as_array()
            .unwrap()
            .iter()
            .map(|candidate| ParsedByteCandidate {
                payload: hex::decode(candidate["payload_hex"].as_str().unwrap()).unwrap(),
                provenance: candidate["provenance"].as_object().unwrap().clone(),
            })
            .collect();
        let result =
            attach_candidates(&config(), &segment, &["unused".into()], candidates).unwrap();
        let actual = Value::Array(result.into_iter().map(|candidate| json!({
            "payload_hex":hex::encode(candidate.payload), "parser":candidate.provenance.parser,
        })).collect());
        assert_eq!(actual, case["expected"], "candidate case {index}");
    }
    let mut profile = oracle["profile"].clone();
    for field in ["modulations", "framing", "fec"] {
        profile.as_object_mut().unwrap().remove(field);
    }
    let mut backend = gr_backend();
    backend.profile = serde_json::from_value(profile).unwrap();
    assert_eq!(
        serde_json::to_value(backend.capabilities().modulations).unwrap(),
        oracle["profile"]["modulations"]
    );
    for case in oracle["argv_cases"].as_array().unwrap() {
        let segment = CaptureSegment {
            path: "/FIXTURE/input.iq".into(),
            sample_format: case["sample_format"].as_str().unwrap().into(),
            sample_rate_hz: case["sample_rate_hz"].as_f64().unwrap(),
            start_sample: 0,
            sample_count: None,
            hints: json!({"start_time":"2026-09-08T00:00:00Z"})
                .as_object()
                .unwrap()
                .clone(),
        };
        let actual = backend
            .build_argv(&segment, Path::new("/FIXTURE/output.kiss"))
            .unwrap();
        assert_eq!(serde_json::to_value(actual).unwrap(), case["expected"]);
    }
}

#[test]
fn candidate_sort_dedup_and_unicode_metadata_match_python_contract() {
    let dir = tempfile::tempdir().unwrap();
    let mut segment = capture(dir.path());
    segment.start_sample = 12;
    segment.sample_count = Some(8);
    segment.hints.insert("modulation".into(), json!("unknown"));
    let result = run_external_backend(
        &config(),
        &segment,
        |_| Ok(vec!["/bin/true".into()]),
        |_, _| {
            Ok(vec![
                ParsedByteCandidate {
                    payload: b"z".to_vec(),
                    provenance: json!({"rank":2}).as_object().unwrap().clone(),
                },
                ParsedByteCandidate {
                    payload: b"a".to_vec(),
                    provenance: json!({"rank":1}).as_object().unwrap().clone(),
                },
                ParsedByteCandidate {
                    payload: b"z".to_vec(),
                    provenance: json!({"rank":2}).as_object().unwrap().clone(),
                },
            ])
        },
    );
    assert!(result.succeeded(), "{result:?}");
    assert_eq!(
        result
            .candidates
            .iter()
            .map(|c| c.payload.clone())
            .collect::<Vec<_>>(),
        [b"a".to_vec(), b"z".to_vec()]
    );
    let p = &result.candidates[0].provenance;
    assert_eq!(p.start_sample, 12);
    assert_eq!(p.sample_count, Some(8));
    assert_eq!(p.capture_hints["modulation"], "unknown");
    assert_eq!(p.backend_id, "example_decoder");
    let unicode = json!({"😀":"Straße","number":1e-7})
        .as_object()
        .unwrap()
        .clone();
    assert_eq!(
        candidate_key(&unicode).unwrap(),
        r#"{"number":1e-07,"\ud83d\ude00":"Stra\u00dfe"}"#
    );
}

#[test]
fn literal_argv_and_environment_are_not_interpreted_as_shell() {
    let dir = tempfile::tempdir().unwrap();
    let segment = capture(dir.path());
    let marker = dir.path().join("must-not-exist");
    let hostile = format!("; touch {}", marker.display());
    let result = run_command(&config(), &segment, &["/usr/bin/printf", "%s", &hostile]);
    assert!(result.succeeded(), "{result:?}");
    assert_eq!(result.stdout, hostile.as_bytes());
    assert!(!marker.exists());
    let mut cfg = config();
    cfg.environment_overrides
        .insert("TELEMETRY_YIELD_TEST_MODE".into(), "offline".into());
    let result = run_command(
        &cfg,
        &segment,
        &["/usr/bin/printenv", "TELEMETRY_YIELD_TEST_MODE"],
    );
    assert!(result.succeeded());
    assert_eq!(result.stdout, b"offline\n");
}

#[test]
fn timeout_combined_output_nonzero_launch_and_parser_failures_are_structured() {
    let dir = tempfile::tempdir().unwrap();
    let segment = capture(dir.path());
    let mut cfg = config();
    cfg.timeout_seconds = 0.03;
    let result = run_command(&cfg, &segment, &["/bin/sleep", "10"]);
    assert_eq!(result.failure.unwrap().code, FailureCode::Timeout);
    assert!(result.candidates.is_empty());
    let mut cfg = config();
    cfg.max_output_bytes = 64;
    let result = run_external_backend(
        &cfg,
        &segment,
        |_| {
            Ok(vec!["/bin/sh".into(),"-c".into(),"printf 1234567890123456789012345678901234567890; printf 1234567890123456789012345678901234567890 >&2".into()])
        },
        |_, _| panic!("parser must not run after output limit"),
    );
    assert_eq!(result.failure.unwrap().code, FailureCode::OutputLimit);
    assert_eq!(result.stdout.len() + result.stderr.len(), 64);
    let result = run_command(&config(), &segment, &["/bin/sh", "-c", "exit 7"]);
    assert_eq!(result.failure.unwrap().code, FailureCode::NonzeroExit);
    assert_eq!(result.returncode, Some(7));
    let result = run_command(&config(), &segment, &["/does-not-exist/decoder"]);
    assert_eq!(result.failure.unwrap().code, FailureCode::LaunchError);
    let result = run_external_backend(
        &config(),
        &segment,
        |_| Ok(vec!["/bin/true".into()]),
        |_, _| Err("bad decoder output".into()),
    );
    assert_eq!(result.failure.unwrap().code, FailureCode::ParseError);
}

#[test]
fn invalid_contracts_fail_without_launch() {
    let dir = tempfile::tempdir().unwrap();
    let segment = capture(dir.path());
    let result = run_external_backend(&config(), &segment, |_| Ok(vec![]), |_, _| Ok(vec![]));
    assert_eq!(result.failure.unwrap().code, FailureCode::InvocationError);
    let mut bad = segment.clone();
    bad.sample_rate_hz = f64::NAN;
    assert!(bad.validate().is_err());
    bad.sample_rate_hz = 0.0;
    assert!(bad.validate().is_err());
    bad.sample_rate_hz = 1.0;
    bad.sample_count = Some(0);
    assert!(bad.validate().is_err());
    bad.sample_count = Some(u64::MAX);
    bad.start_sample = 1;
    assert!(bad.validate().is_err());
    let mut bad = segment.clone();
    bad.path = dir.path().join("missing");
    assert_eq!(
        run_command(&config(), &bad, &["/bin/true"])
            .failure
            .unwrap()
            .code,
        FailureCode::InputError
    );
    let mut cfg = config();
    cfg.environment_overrides
        .insert("bad=name".into(), "1".into());
    assert!(cfg.validate().is_err());
    let mut cfg = config();
    cfg.max_output_bytes = 0;
    assert!(cfg.validate().is_err());
    let mut cfg = config();
    cfg.timeout_seconds = f64::INFINITY;
    assert!(cfg.validate().is_err());
    let mut cfg = config();
    cfg.capabilities.fec.push("none".into());
    assert!(cfg.validate().is_err());
}

#[test]
fn gr_argv_maps_only_explicit_supported_full_file_formats() {
    let dir = tempfile::tempdir().unwrap();
    let mut segment = capture(dir.path());
    let backend = gr_backend();
    let output = dir.path().join("decoded.kiss");
    let cf32 = backend.build_argv(&segment, &output).unwrap();
    assert!(cf32.contains(&"--rawfile".into()));
    segment.sample_format = "ci16_le".into();
    segment.sample_rate_hz = 57600.0;
    segment
        .hints
        .insert("start_time".into(), json!("2026-09-08T00:00:00Z"));
    let argv = backend.build_argv(&segment, &output).unwrap();
    assert_eq!(
        &argv[..3],
        ["/usr/bin/gr_satellites", "CANVAS", "--rawint16"]
    );
    assert_eq!(argv[5], "57600");
    assert_eq!(&argv[10..], ["--start_time", "2026-09-08T00:00:00Z"]);
    assert_eq!(backend.capabilities().framing, ["AX.25 G3RUH"]);
    assert!(!backend.supports_sample_ranges());
    segment.sample_count = Some(4);
    let result = backend.run(&segment);
    assert_eq!(result.failure.unwrap().code, FailureCode::InputError);
    assert!(result.argv.is_empty());
    segment.sample_count = None;
    segment.sample_format = "cf64_le".into();
    assert!(backend.validate_segment(&segment).is_err());
    segment.sample_format = "ci16_le".into();
    let mut bad = backend.clone();
    bad.profile.selector = "--help".into();
    assert!(bad.validate_segment(&segment).is_err());
}

#[cfg(unix)]
fn fake_gr(root: &Path, body: &str) -> GrSatellitesBackend {
    use std::os::unix::fs::PermissionsExt;
    let executable = root.join("fake-gr-satellites");
    fs::write(&executable,format!("#!/bin/sh\n[ \"$GR_SATELLITES_SUBMIT_TLM\" = 0 ] || exit 90\nwhile [ \"$#\" -gt 0 ]; do\n  if [ \"$1\" = --kiss_out ]; then shift; out=$1; fi\n  shift\ndone\n{body}\n")).unwrap();
    fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
    let mut backend = gr_backend();
    backend.executable = executable.to_str().unwrap().into();
    backend
}

#[test]
fn gr_controlled_kiss_preserves_candidate_only_provenance_and_empty_success() {
    let dir = tempfile::tempdir().unwrap();
    let segment = capture(dir.path());
    let stream = [
        formats::encode_kiss_record(0, 9, &123456789u64.to_be_bytes()).unwrap(),
        formats::encode_kiss_record(0, 0, &[0x10, 0xc0, 0xdb, 0x20]).unwrap(),
    ]
    .concat();
    let octal = stream
        .iter()
        .map(|b| format!("\\{b:03o}"))
        .collect::<String>();
    let backend = fake_gr(dir.path(), &format!("printf '{octal}' > \"$out\""));
    let result = backend.run(&segment);
    assert!(result.succeeded(), "{result:?}");
    assert_eq!(result.candidates.len(), 1);
    let candidate = &result.candidates[0];
    assert_eq!(candidate.payload, [0x10, 0xc0, 0xdb, 0x20]);
    assert_eq!(candidate.provenance.parser["kiss_timestamp_ms"], 123456789);
    assert_eq!(
        candidate.provenance.parser["candidate_validation"],
        "pending_downstream_validator"
    );
    assert!(candidate.provenance.parser["transmitter_attribution"].is_null());
    assert_eq!(
        candidate.provenance.parser["profile_transmitters"][0]["transmitter_id"],
        "UHF telemetry"
    );
    let backend = fake_gr(dir.path(), ": > \"$out\"");
    let result = backend.run(&segment);
    assert!(result.succeeded());
    assert!(result.candidates.is_empty());
}

#[test]
fn gr_missing_oversized_fifo_and_symlink_outputs_are_not_candidates() {
    let dir = tempfile::tempdir().unwrap();
    let segment = capture(dir.path());
    for body in ["exit 0", "mkfifo \"$out\"", "ln -s /etc/passwd \"$out\""] {
        let backend = fake_gr(dir.path(), body);
        let result = backend.run(&segment);
        assert_eq!(
            result.failure.unwrap().code,
            FailureCode::ParseError,
            "{body}"
        );
        assert!(result.candidates.is_empty());
    }
    let mut backend = fake_gr(dir.path(), "printf 12345 > \"$out\"");
    backend.max_kiss_bytes = 4;
    let result = backend.run(&segment);
    assert_eq!(result.failure.unwrap().code, FailureCode::ParseError);
}

#[cfg(target_os = "linux")]
#[test]
fn descendant_inherited_pipes_and_closed_pipes_are_cleaned_on_timeout_and_success() {
    let dir = tempfile::tempdir().unwrap();
    let segment = capture(dir.path());
    for redirect in ["", ">/dev/null 2>&1"] {
        let pid_path = dir.path().join("descendant.pid");
        let script = format!("sleep 30 {redirect} & printf '%s' $! > \"$1\"; exit 0");
        let mut cfg = config();
        cfg.timeout_seconds = 0.05;
        let result = run_command(
            &cfg,
            &segment,
            &["/bin/sh", "-c", &script, "test", pid_path.to_str().unwrap()],
        );
        if redirect.is_empty() {
            assert_eq!(result.failure.unwrap().code, FailureCode::Timeout);
        } else {
            assert!(result.succeeded(), "{result:?}");
        }
        let pid = fs::read_to_string(&pid_path).unwrap();
        let stat_path = format!("/proc/{}/stat", pid.trim());
        let started = Instant::now();
        loop {
            let running = fs::read_to_string(&stat_path).ok().is_some_and(|s| {
                s.rsplit_once(") ")
                    .and_then(|(_, rest)| rest.chars().next())
                    .is_some_and(|state| state != 'Z' && state != 'X')
            });
            if !running {
                break;
            }
            assert!(
                started.elapsed() < Duration::from_secs(1),
                "descendant still running: {pid}"
            );
            std::thread::sleep(Duration::from_millis(5));
        }
    }
}
