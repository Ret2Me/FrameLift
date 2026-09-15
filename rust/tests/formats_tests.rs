use super::*;
use serde_json::json;

#[test]
fn frozen_python_sigmf_core_summary_matches_for_all_requested_widths() {
    let fixture: Value = serde_json::from_str(include_str!("formats_sigmf_oracle.json")).unwrap();
    let dir = tempfile::tempdir().unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let data = hex::decode(case["data_hex"].as_str().unwrap()).unwrap();
        fs::write(dir.path().join("test.sigmf-data"), data).unwrap();
        let path = dir.path().join("test.sigmf-meta");
        write_meta(&path, &case["document"]);
        let actual = parse_sigmf(&path).unwrap();
        assert_eq!(
            actual.datatype,
            case["expected"]["datatype"].as_str().unwrap()
        );
        assert_eq!(
            actual.sample_rate_hz,
            case["expected"]["sample_rate_hz"].as_f64().unwrap()
        );
        assert_eq!(
            actual.complex_sample_count,
            case["expected"]["sample_count"].as_u64().unwrap()
        );
        assert_eq!(
            actual.verified_sha512.as_deref(),
            case["expected"]["sha512"].as_str()
        );
    }
}

#[test]
fn frozen_python_kiss_cases_preserve_records_errors_and_full_u64_timestamps() {
    let fixture: Value = serde_json::from_str(include_str!("formats_kiss_oracle.json")).unwrap();
    assert_eq!(fixture["cases"].as_array().unwrap().len(), 258);
    for (index, case) in fixture["cases"].as_array().unwrap().iter().enumerate() {
        let input = hex::decode(case["input_hex"].as_str().unwrap()).unwrap();
        let expected: KissResult = serde_json::from_value(case["result"].clone()).unwrap();
        assert_eq!(parse_kiss(&input).unwrap(), expected, "case {index}");
    }
}

#[test]
fn kiss_encoder_roundtrips_every_port_command_escape_and_timestamp() {
    let payload: Vec<u8> = (0..=255).collect();
    for port in 0..16 {
        for command in 0..16 {
            let data = if command == 9 {
                u64::MAX.to_be_bytes().to_vec()
            } else {
                payload.clone()
            };
            let parsed = parse_kiss(&encode_kiss_record(port, command, &data).unwrap()).unwrap();
            assert_eq!(parsed.malformed_records, 0);
            match command {
                0 => {
                    assert_eq!(parsed.data_frames[0].port, port);
                    assert_eq!(parsed.data_frames[0].payload, payload);
                }
                9 => assert_eq!(parsed.timestamp_commands[0].timestamp_ms, u64::MAX),
                _ => {
                    assert_eq!(parsed.other_commands[0].command, command);
                    assert_eq!(parsed.other_commands[0].payload, payload);
                }
            }
        }
    }
    assert!(encode_kiss_record(16, 0, &[1]).is_err());
    assert!(encode_kiss_record(0, 0, &[]).is_err());
    assert!(encode_kiss_record(0, 9, &[1]).is_err());
    assert!(parse_kiss(&vec![1; MAX_KISS_RECORD_BYTES + 1]).is_err());
}

#[test]
fn raw_manifest_python_acceptance_and_clipping_oracle() {
    let fixture: Value = serde_json::from_str(include_str!("formats_raw_oracle.json")).unwrap();
    let dir = tempfile::tempdir().unwrap();
    for case in fixture["cases"].as_array().unwrap() {
        let data = hex::decode(case["data_hex"].as_str().unwrap()).unwrap();
        fs::write(dir.path().join("capture.raw"), &data).unwrap();
        let actual = validate_raw_manifest(&case["manifest"], dir.path());
        assert_eq!(
            actual.is_ok(),
            case["ready_for_decode"].as_bool().unwrap(),
            "{}: {actual:?}",
            case["name"]
        );
        if let Ok(metadata) = actual {
            let clipping: ClippingMetrics =
                serde_json::from_value(case["clipping"].clone()).unwrap();
            assert_eq!(metadata.clipping, Some(clipping));
            assert_eq!(metadata.verified_sha256, hex::encode(Sha256::digest(&data)));
        }
    }
}

fn sigmf_pair(dir: &Path, datatype: &str, data: &[u8]) -> (PathBuf, Value) {
    let path = dir.join("test.sigmf-meta");
    fs::write(dir.join("test.sigmf-data"), data).unwrap();
    let value = json!({"global":{"core:datatype":datatype,"core:sample_rate":48000,"core:version":"1.2.0","core:sha512":hex::encode(Sha512::digest(data))},"captures":[{"core:sample_start":0,"core:frequency":437500000,"core:datetime":"2026-09-08T00:00:00Z"}],"annotations":[]});
    fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
    (path, value)
}
fn write_meta(path: &Path, value: &Value) {
    fs::write(path, serde_json::to_vec(value).unwrap()).unwrap();
}

#[test]
fn sigmf_explicit_i16_f32_f64_byte_width_and_endian() {
    let dir = tempfile::tempdir().unwrap();
    for (datatype, encoding, raw, first) in [
        (
            "ci16_le",
            ScalarEncoding::I16Le,
            vec![2, 1, 0xff, 0xfe],
            258.0,
        ),
        (
            "ci16_be",
            ScalarEncoding::I16Be,
            vec![1, 2, 0xfe, 0xff],
            258.0,
        ),
        (
            "cf32_le",
            ScalarEncoding::F32Le,
            [1.25f32.to_le_bytes(), (-2.5f32).to_le_bytes()].concat(),
            1.25,
        ),
        (
            "cf32_be",
            ScalarEncoding::F32Be,
            [1.25f32.to_be_bytes(), (-2.5f32).to_be_bytes()].concat(),
            1.25,
        ),
        (
            "cf64_le",
            ScalarEncoding::F64Le,
            [1.25f64.to_le_bytes(), (-2.5f64).to_le_bytes()].concat(),
            1.25,
        ),
        (
            "cf64_be",
            ScalarEncoding::F64Be,
            [1.25f64.to_be_bytes(), (-2.5f64).to_be_bytes()].concat(),
            1.25,
        ),
    ] {
        let (path, _) = sigmf_pair(dir.path(), datatype, &raw);
        let parsed = parse_sigmf(&path).unwrap();
        assert_eq!(parsed.complex_sample_count, 1);
        assert_eq!(parsed.scalar_encoding, encoding);
        assert_eq!(
            encoding
                .decode_scalar(&raw[..encoding.scalar_bytes() as usize])
                .unwrap(),
            first
        );
    }
}

#[test]
fn sigmf_no_silent_format_defaults_or_offset_unit_confusion() {
    let dir = tempfile::tempdir().unwrap();
    let (path, base) = sigmf_pair(dir.path(), "ci16_le", &[0; 16]);
    for field in [
        "core:datatype",
        "core:sample_rate",
        "core:sha512",
        "core:version",
    ] {
        let mut d = base.clone();
        d["global"].as_object_mut().unwrap().remove(field);
        write_meta(&path, &d);
        assert!(parse_sigmf(&path).is_err(), "{field}");
    }
    for (field, bad) in [
        ("core:datatype", json!("ci16")),
        ("core:sample_rate", json!(true)),
        ("core:sample_rate", json!(0)),
        ("core:num_channels", json!(2)),
        ("core:sha512", json!("0".repeat(128))),
    ] {
        let mut d = base.clone();
        d["global"][field] = bad;
        write_meta(&path, &d);
        assert!(parse_sigmf(&path).is_err(), "{field}");
    }
    let mut d = base.clone();
    d["captures"][0]["core:frequency"] = 0.into();
    write_meta(&path, &d);
    assert_eq!(
        parse_sigmf(&path).unwrap().captures[0].frequency_hz,
        Some(0.0)
    );
    let mut d = base.clone();
    d["global"]["core:offset"] = 1000.into();
    d["captures"][0]["core:sample_start"] = 1000.into();
    d["captures"]
        .as_array_mut()
        .unwrap()
        .push(json!({"core:sample_start":1002,"core:global_index":1004}));
    write_meta(&path, &d);
    let parsed = parse_sigmf(&path).unwrap();
    assert_eq!(parsed.byte_offset, 0);
    assert_eq!(parsed.sample_index_offset, 1000);
    assert_eq!(parsed.complex_sample_count, 4);
    assert_eq!(parsed.captures[1].sample_start, 2);
    assert_eq!(parsed.captures[1].global_index, Some(1004));
    d["captures"][1]["core:sample_start"] = 1004.into();
    write_meta(&path, &d);
    assert!(parse_sigmf(&path).is_err());
    let mut d = base.clone();
    d["captures"] = json!([]);
    write_meta(&path, &d);
    assert_eq!(parse_sigmf(&path).unwrap().captures[0].sample_start, 0);
    let mut d = base.clone();
    d["captures"][0]["core:header_bytes"] = 4.into();
    write_meta(&path, &d);
    assert!(parse_sigmf(&path).is_err());
    let mut d = base.clone();
    d["global"]["core:extensions"] = json!([{"name":"unknown","version":"1.0.0","optional":false}]);
    write_meta(&path, &d);
    assert!(parse_sigmf(&path).is_err());
    let (mut_path, _) = sigmf_pair(dir.path(), "cf64_le", &[0; 17]);
    assert!(parse_sigmf(&mut_path).is_err());
}

fn profile_document(name: &str, norad: u64) -> Value {
    json!({"name":name,"norad":norad,"transmitters":{"telemetry":{"frequency":437500000,"modulation":"BPSK","baudrate":9600,"framing":"CCSDS Concatenated","fec":["convolutional-r1/2","reed-solomon"]}}})
}

#[test]
fn satyaml_registry_is_fault_isolated_conflicts_excluded_unicode_casefold() {
    let root = tempfile::tempdir().unwrap();
    for (file, name, norad) in [
        ("one.yml", "Straße", 1),
        ("two.yml", "STRASSE", 2),
        ("three.yml", "unique", 2),
        ("four.yml", "usable", 4),
    ] {
        // JSON is a strict YAML subset and avoids coupling test source encoding
        // to the serializer library under test.
        fs::write(
            root.path().join(file),
            serde_json::to_vec(&profile_document(name, norad)).unwrap(),
        )
        .unwrap();
    }
    fs::write(root.path().join("bad.yml"), b"name: [broken").unwrap();
    fs::write(
        root.path().join("unsafe.yml"),
        b"!!python/object/apply:os.system [\"touch forbidden\"]",
    )
    .unwrap();
    let registry = build_satyaml_registry(&[root.path().to_path_buf()], 1024 * 1024).unwrap();
    assert_eq!(registry.profiles.len(), 4);
    assert_eq!(registry.conflicted_names, vec!["strasse"]);
    assert_eq!(registry.conflicted_norads, vec![2]);
    assert!(registry.get_by_name("Straße").is_none());
    assert!(registry.get_by_norad(2).is_none());
    assert_eq!(registry.get_by_name("USABLE").unwrap().norad_id, 4);
    assert!(registry.diagnostics.iter().any(|d| d.code == "yaml_error"));
    assert!(!root.path().join("forbidden").exists());
    assert!(parse_satyaml_document(&profile_document("valid", 1), "profile.yml").is_ok());
    let mut duplicate_fec = profile_document("valid", 1);
    duplicate_fec["transmitters"]["telemetry"]["fec"] = json!(["rs", "rs"]);
    assert!(parse_satyaml_document(&duplicate_fec, "profile.yml").is_err());
    let mut absent_framing = profile_document("valid", 1);
    absent_framing["transmitters"]["telemetry"]
        .as_object_mut()
        .unwrap()
        .remove("framing");
    assert!(parse_satyaml_document(&absent_framing, "profile.yml").is_err());
}

#[test]
fn satyaml_original_fixture_catalogue_loads_profiles_without_claiming_runtime_support() {
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/satyaml_registry");
    let registry = build_satyaml_registry(&[fixture], 1024 * 1024).unwrap();
    assert_eq!(registry.profiles.len(), 4);
    assert!(
        registry
            .profiles
            .iter()
            .any(|p| p.fec.contains(&"convolutional-r1/2".into()))
    );
    assert!(
        registry
            .diagnostics
            .iter()
            .any(|d| d.code == "duplicate_name")
    );
    assert!(
        registry
            .diagnostics
            .iter()
            .any(|d| d.code == "duplicate_norad")
    );
}

#[test]
fn satyaml_duplicate_keys_and_nested_object_tags_fail_closed() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bad.yml");
    fs::write(&path,b"name: first\nname: second\nnorad: 1\ntransmitters: {x: {modulation: FSK, framing: AX25}}\n").unwrap();
    assert!(parse_satyaml(&path).is_err());
    fs::write(&path,b"name: first\nnorad: 1\ntransmitters:\n  x:\n    modulation: FSK\n    framing: AX25\n    metadata: !!python/object/apply:os.system [\"touch forbidden\"]\n").unwrap();
    assert!(parse_satyaml(&path).is_err());
    let registry = build_satyaml_registry(&[path], 1024 * 1024).unwrap();
    assert!(registry.profiles.is_empty());
    assert_eq!(registry.diagnostics[0].code, "yaml_error");
}

#[cfg(unix)]
#[test]
fn metadata_and_raw_input_fifos_are_rejected_without_blocking() {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;
    let dir = tempfile::tempdir().unwrap();
    let fifo = dir.path().join("input.yml");
    let cpath = CString::new(fifo.as_os_str().as_bytes()).unwrap();
    // mkfifo is only used in this bounded isolated test directory.
    assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);
    assert!(read_bounded(&fifo, 10).is_err());
    assert!(hashes(&fifo).is_err());
    assert!(range_sha256(&fifo, 0, 1).is_err());
    assert!(parse_satyaml(&fifo).is_err());
    assert!(parse_sigmf(&fifo).is_err());
}

#[test]
fn catalogue_routing_requires_identity_frequency_and_unique_profile_no_aliases() {
    let profile = parse_satyaml_document(
        &profile_document("must not identity-match this name", 42),
        "profile.yml",
    )
    .unwrap();
    let registry = SatYamlRegistry {
        profiles: vec![profile],
        norad_index: BTreeMap::from([(42, "profile.yml".into())]),
        ..Default::default()
    };
    let obs = CatalogObservation {
        observation_id: 1,
        satellite_id: "UUID".into(),
        frequency_hz: 437500000.0,
        mode: Some("FSK".into()),
        iq_url: "local-recording".into(),
    };
    let tx = CatalogTransmitter {
        transmitter_uuid: "TX".into(),
        satellite_id: "UUID".into(),
        norad_id: 42,
        frequency_hz: 437500000.0,
        mode: Some("FSK".into()),
        baud: Some(4800.0),
        status: None,
    };
    let routed = route_catalog_observations(
        std::slice::from_ref(&obs),
        std::slice::from_ref(&tx),
        &registry,
        0.0,
    )
    .unwrap();
    assert_eq!(routed[0].status, RoutingStatus::Routed);
    assert_eq!(routed[0].disagreements.len(), 2);
    let mut mismatch = obs.clone();
    mismatch.satellite_id = "must not identity-match this name".into();
    assert_eq!(
        route_catalog_observations(&[mismatch], std::slice::from_ref(&tx), &registry, 0.0).unwrap()
            [0]
        .status,
        RoutingStatus::UnmatchedSatelliteUuid
    );
    let mut mismatch = obs.clone();
    mismatch.frequency_hz += 1.0;
    assert_eq!(
        route_catalog_observations(
            &[mismatch.clone()],
            std::slice::from_ref(&tx),
            &registry,
            0.0
        )
        .unwrap()[0]
            .status,
        RoutingStatus::UnmatchedCatalogFrequency
    );
    assert_eq!(
        route_catalog_observations(&[mismatch], std::slice::from_ref(&tx), &registry, 1.0).unwrap()
            [0]
        .status,
        RoutingStatus::Routed
    );
    let mut conflict = tx.clone();
    conflict.norad_id = 43;
    assert_eq!(
        route_catalog_observations(
            std::slice::from_ref(&obs),
            &[tx.clone(), conflict],
            &registry,
            0.0
        )
        .unwrap()[0]
            .status,
        RoutingStatus::AmbiguousCatalogNorad
    );
    let mut ambiguous = registry.clone();
    ambiguous.conflicted_norads.push(42);
    assert_eq!(
        route_catalog_observations(
            std::slice::from_ref(&obs),
            std::slice::from_ref(&tx),
            &ambiguous,
            0.0
        )
        .unwrap()[0]
            .status,
        RoutingStatus::AmbiguousSatyamlProfile
    );
    assert!(route_catalog_observations(&[obs.clone(), obs], &[tx], &registry, 0.0).is_err());
}
