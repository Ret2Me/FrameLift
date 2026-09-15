use super::*;

#[test]
fn legacy_plugin_callback_signature_and_borrowed_state_remain_compatible() {
    struct LegacyPlugin;
    impl Demodulator for LegacyPlugin {
        fn accepts(&self, _: bool) -> bool {
            true
        }
        fn demodulate(&self, _: Signal<'_>, _: u32, _: &Waveform) -> Result<dsp::Frontend, String> {
            Err("this plugin supplies soft streams directly".into())
        }
        // Keep the pre-refactor public signature, not the new alias, to catch
        // accidental lifetime/API incompatibility with downstream plugins.
        fn visit_soft_symbols(
            &self,
            _: Signal<'_>,
            _: u32,
            _: &Waveform,
            visit: &mut dyn FnMut(&[f64], f64, f64, Option<&str>) -> Result<(), String>,
        ) -> Result<usize, String> {
            visit(&[1.0, -1.0], 0.0, 1.0, Some("legacy"))?;
            Ok(1)
        }
    }
    let config = plan(InputFormat::Audio, "legacy");
    let receiver: Box<dyn Demodulator> = Box::new(LegacyPlugin);
    let mut captured = Vec::new();
    let count = receiver
        .visit_soft_symbols(
            Signal::Pcm(&[]),
            48000,
            &config.hypotheses[0].waveform,
            &mut |soft, threshold, score, label| {
                captured.extend_from_slice(soft);
                assert_eq!((threshold, score, label), (0.0, 1.0, Some("legacy")));
                Ok(())
            },
        )
        .unwrap();
    assert_eq!(count, 1);
    assert_eq!(captured, [1.0, -1.0]);
}

fn positive_samples() -> (Vec<f64>, Vec<String>) {
    // Immutable protocol oracle is source bytes, not a receiver search hint.
    let oracle: Value = serde_json::from_str(include_str!("protocol_oracle.json")).unwrap();
    let case = &oracle["cases"][0];
    let bits = hex::decode(case["levels_hex"].as_str().unwrap()).unwrap();
    let count = case["symbol_count"].as_u64().unwrap() as usize;
    let mut samples = Vec::new();
    // Repeat a full trace: enough independent window starts, no perfect-clock hints.
    for _ in 0..3 {
        for n in 0..count {
            let level = if (bits[n / 8] >> (7 - n % 8)) & 1 == 1 {
                0.75
            } else {
                -0.75
            };
            samples.extend(std::iter::repeat_n(level, 5));
        }
    }
    let expected = serde_json::from_value(case["expected"][0]["frames_hex"].clone()).unwrap();
    (samples, expected)
}

fn plan(format: InputFormat, demodulator: &str) -> Plan {
    Plan {
        format,
        sample_rate_hz: 48000,
        window_seconds: 0.4,
        hop_seconds: 0.2,
        segment_start_sample: 0,
        segment_sample_count: None,
        protocols: BTreeMap::from([(
            "ax25".into(),
            ProtocolConfig::Ax25 {
                g3ruh_modes: vec![false, true],
            },
        )]),
        hypotheses: vec![PlannedHypothesis {
            protocol_id: "ax25".into(),
            waveform: Waveform {
                hypothesis_id: "synthetic-fsk".into(),
                demodulator_id: demodulator.into(),
                dsp: dsp::DspConfig::default(),
                decimation: 2,
                cutoff_hz: None,
                carrier_hz: None,
                mark_hz: 1200.0,
                space_hz: 2200.0,
                psk: None,
            },
        }],
    }
}

#[test]
fn positive_pcm_and_ci16_file_paths_preserve_frames_and_worker_order() {
    let temp = tempfile::tempdir().unwrap();
    let (samples, expected) = positive_samples();
    let audio = temp.path().join("positive.wav");
    let mut wav = hound::WavWriter::create(
        &audio,
        hound::WavSpec {
            channels: 1,
            sample_rate: 48000,
            bits_per_sample: 32,
            sample_format: hound::SampleFormat::Float,
        },
    )
    .unwrap();
    for x in &samples {
        wav.write_sample(*x as f32).unwrap();
    }
    wav.finalize().unwrap();
    let iq_path = temp.path().join("positive.ci16");
    let mut iq = File::create(&iq_path).unwrap();
    let mut phase = 0.0f64;
    for sample in &samples {
        phase += sample * std::f64::consts::TAU * 4800.0 / 48000.0;
        for component in [phase.cos(), phase.sin()] {
            iq.write_all(&((component * 16000.0).round() as i16).to_le_bytes())
                .unwrap();
        }
    }
    drop(iq);
    for (label, path, p) in [
        ("pcm", audio, plan(InputFormat::Audio, "pcm_fsk")),
        ("iq", iq_path, plan(InputFormat::Ci16Le, "phase_fsk")),
    ] {
        let mut p = p;
        let mut second = p.hypotheses[0].clone();
        second.waveform.hypothesis_id = "second-waveform".into();
        p.hypotheses.push(second);
        let mut one = decode_file(&path, &temp.path().join(format!("{label}-one")), &p, 1).unwrap();
        let mut four =
            decode_file(&path, &temp.path().join(format!("{label}-four")), &p, 4).unwrap();
        assert_eq!(one["status"], "complete");
        for frame in one["frames"].as_array().unwrap() {
            let provenance = frame["provenance"].as_array().unwrap();
            assert!(provenance.iter().any(|p| p["waveform"] == "synthetic-fsk"));
            assert!(
                provenance
                    .iter()
                    .any(|p| p["waveform"] == "second-waveform")
            );
        }
        let mut got: Vec<_> = one["frames"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| f["frame_hex"].as_str().unwrap().to_string())
            .collect();
        let mut wanted = expected.clone();
        got.sort();
        wanted.sort();
        assert_eq!(got, wanted, "{label}");
        one.as_object_mut().unwrap().remove("elapsed_seconds");
        four.as_object_mut().unwrap().remove("elapsed_seconds");
        assert_eq!(
            one, four,
            "{label}: discrete frames, scores and provenance identical"
        );
        assert_eq!(
            std::fs::read(temp.path().join(format!("{label}-one/windows.jsonl"))).unwrap(),
            std::fs::read(temp.path().join(format!("{label}-four/windows.jsonl"))).unwrap()
        );
    }
}

#[test]
fn input_failure_is_persisted_not_counted_as_no_telemetry() {
    let temp = tempfile::tempdir().unwrap();
    let output = temp.path().join("result");
    assert!(
        decode_file(
            &temp.path().join("missing"),
            &output,
            &plan(InputFormat::Ci16Le, "phase_fsk"),
            1
        )
        .is_err()
    );
    let value = input::read_json(&output.join("result.json")).unwrap();
    assert_eq!(value["status"], "failed");
    assert!(value.get("unique_frame_count").is_none());
}

#[test]
fn metadata_windows_preserve_endian_origin_order_scale_and_q_polarity() {
    let temp = tempfile::tempdir().unwrap();
    let data = temp.path().join("capture.sigmf-data");
    let mut raw = Vec::new();
    for (i, q) in [(16384i16, -8192i16), (-32768, 32767)] {
        raw.extend(i.to_be_bytes());
        raw.extend(q.to_be_bytes());
    }
    std::fs::write(&data, &raw).unwrap();
    let meta_path = temp.path().join("capture.sigmf-meta");
    input::write_json_new(&meta_path,&json!({"global":{"core:datatype":"ci16_be","core:version":"1.2.6","core:sample_rate":48000,
        "core:sha512":hex::encode(sha2::Sha512::digest(&raw)),"core:offset":1000},"captures":[{"core:sample_start":1000}],"annotations":[]})).unwrap();
    let mut metadata = formats::parse_sigmf(&meta_path).unwrap();
    assert_eq!(
        read_metadata_window(&metadata, 0, 1).unwrap(),
        vec![Complex64::new(0.5, -0.25)]
    );
    // These layout changes exercise the raw-manifest adapter, not SigMF defaults.
    metadata.interleaving = formats::Interleaving::Qi;
    metadata.q_sign = -1;
    assert_eq!(
        read_metadata_window(&metadata, 0, 1).unwrap(),
        vec![Complex64::new(-0.25, -0.5)]
    );
    metadata.byte_offset = 4;
    metadata.complex_sample_count = 1;
    assert_eq!(
        read_metadata_window(&metadata, 0, 1).unwrap(),
        vec![Complex64::new(32767.0 / 32768.0, 1.0)]
    );
    assert!(read_metadata_window(&metadata, 1, 1).is_err());
}
