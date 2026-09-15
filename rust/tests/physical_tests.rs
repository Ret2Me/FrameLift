use super::*;
use sha2::{Digest, Sha256};

fn lcg(seed: u64, count: usize) -> Vec<f64> {
    let mut state = seed;
    (0..count)
        .map(|_| {
            state = state
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            ((state >> 32) as f64 - 2_147_483_648.0) / 2_147_483_648.0
        })
        .collect()
}
fn compare_diagnostics(actual: &Value, expected: &Value) {
    match (actual, expected) {
        (Value::Object(a), Value::Object(b)) => {
            assert_eq!(a.len(), b.len());
            for (key, value) in b {
                compare_diagnostics(a.get(key).unwrap(), value);
            }
        }
        (Value::Number(a), Value::Number(b)) => {
            let x = a.as_f64().unwrap();
            let y = b.as_f64().unwrap();
            assert!((x - y).abs() <= 2e-8 * (1.0 + y.abs()), "metric {x} != {y}");
        }
        _ => assert_eq!(actual, expected),
    }
}

#[test]
fn fixed_python_physical_and_alignment_oracles() {
    let document: Value = serde_json::from_str(include_str!("physical_oracle.json")).unwrap();
    for case in document["physical"].as_array().unwrap() {
        let seed = case["seed"].as_u64().unwrap();
        let count = case["sample_count"].as_u64().unwrap() as usize;
        let scalars = lcg(seed, 2 * count);
        let iq: Vec<_> = scalars
            .as_chunks::<2>()
            .0
            .iter()
            .map(|x| Complex64::new(x[0], x[1]))
            .collect();
        let config: PhysicalConfig = serde_json::from_value(case["config"].clone()).unwrap();
        let result = demodulate_unaligned(&iq, &config).unwrap();
        assert_eq!(
            json!(result.variant_names),
            case["variant_names"],
            "{config:?}"
        );
        assert_eq!(
            json!(result.bit_variants.iter().map(Vec::len).collect::<Vec<_>>()),
            case["lengths"],
            "{config:?}"
        );
        assert_eq!(
            json!(
                result
                    .bit_variants
                    .iter()
                    .map(|b| hex::encode(Sha256::digest(b)))
                    .collect::<Vec<_>>()
            ),
            case["bit_sha256"],
            "seed={seed} {config:?}"
        );
        compare_diagnostics(&result.diagnostics, &case["diagnostics"]);
    }
    for case in document["alignment"].as_array().unwrap() {
        let truth: Vec<u8> = serde_json::from_value(case["truth"].clone()).unwrap();
        let policy: AlignmentPolicy = serde_json::from_value(case["policy"].clone()).unwrap();
        let actual = if case["qpsk"] == true {
            let candidate: UnalignedBits =
                serde_json::from_value(case["candidate"].clone()).unwrap();
            align_qpsk_for_ber(&candidate, &truth, &policy)
        } else {
            let candidate: Vec<u8> = serde_json::from_value(case["candidate"].clone()).unwrap();
            align_for_ber(&candidate, &truth, &policy)
        };
        if case.get("error").is_some() {
            assert!(actual.is_err());
            continue;
        }
        let result = actual.unwrap();
        assert_eq!(json!(result.aligned), case["aligned"]);
        assert_eq!(result.diagnostics, case["diagnostics"]);
    }
}

#[test]
fn fixed_python_positive_iq_and_complete_bit_output_oracles() {
    let document: Value =
        serde_json::from_str(include_str!("physical_positive_oracle.json")).unwrap();
    for case in document["records"].as_array().unwrap() {
        let raw = hex::decode(case["iq_f32le_hex"].as_str().unwrap()).unwrap();
        assert_eq!(
            hex::encode(Sha256::digest(&raw)),
            case["iq_sha256"].as_str().unwrap()
        );
        let iq: Vec<_> = raw
            .as_chunks::<8>()
            .0
            .iter()
            .map(|pair| {
                Complex64::new(
                    f32::from_le_bytes(pair[..4].try_into().unwrap()) as f64,
                    f32::from_le_bytes(pair[4..].try_into().unwrap()) as f64,
                )
            })
            .collect();
        for oracle in case["versions"].as_array().unwrap() {
            let config: PhysicalConfig = serde_json::from_value(oracle["config"].clone()).unwrap();
            // Physical API receives only IQ and nominal configuration; truth
            // is not read until demodulation and bit-hash assertions finish.
            let result = demodulate_unaligned(&iq, &config).unwrap();
            assert_eq!(
                json!(result.variant_names),
                oracle["variant_names"],
                "{config:?}"
            );
            assert_eq!(
                json!(result.bit_variants.iter().map(Vec::len).collect::<Vec<_>>()),
                oracle["lengths"],
                "{config:?}"
            );
            assert_eq!(
                json!(
                    result
                        .bit_variants
                        .iter()
                        .map(|b| hex::encode(Sha256::digest(b)))
                        .collect::<Vec<_>>()
                ),
                oracle["bit_sha256"],
                "positive {config:?}"
            );
            compare_diagnostics(&result.diagnostics, &oracle["diagnostics"]);
            let truth: Vec<u8> = serde_json::from_value(case["truth"].clone()).unwrap();
            let policy: AlignmentPolicy = serde_json::from_value(oracle["policy"].clone()).unwrap();
            let aligned = if matches!(config.modulation.as_str(), "QPSK" | "OQPSK") {
                align_qpsk_for_ber(&result, &truth, &policy)
            } else {
                align_for_ber(&result.bit_variants[0], &truth, &policy)
            }
            .unwrap();
            assert_eq!(aligned.diagnostics, oracle["alignment"]);
            assert_eq!(
                hex::encode(Sha256::digest(&aligned.aligned)),
                oracle["aligned_sha256"].as_str().unwrap()
            );
        }
    }
}

#[test]
fn physical_rejects_invalid_rates_unsupported_modes_and_silence() {
    let iq = vec![Complex64::new(1.0, 0.0); 256];
    assert!(demodulate_unaligned(&iq, &PhysicalConfig::default()).is_err());
    let base = PhysicalConfig {
        modulation: "BPSK".into(),
        sample_rate_hz: 48_000.0,
        symbol_rate_hz: 6_000.0,
        ..Default::default()
    };
    assert!(demodulate_unaligned(&iq, &base).is_err());
    for modulation in ["8PSK", "16QAM", "SOQPSK-TG", "FM"] {
        let config = PhysicalConfig {
            modulation: modulation.into(),
            ..base.clone()
        };
        assert!(demodulate_unaligned(&iq, &config).is_err());
    }
    let scalars = lcg(91, 512);
    let signal: Vec<_> = scalars
        .as_chunks::<2>()
        .0
        .iter()
        .map(|x| Complex64::new(x[0], x[1]))
        .collect();
    for config in [
        PhysicalConfig {
            symbol_rate_hz: f64::NAN,
            ..base.clone()
        },
        PhysicalConfig {
            symbol_rate_hz: 48_000.0,
            ..base.clone()
        },
        PhysicalConfig {
            delayed_branch: Some("q".into()),
            ..base.clone()
        },
        PhysicalConfig {
            recovery_version: "carrier_timing_v2".into(),
            max_carrier_offset_hz: 12_000.0,
            ..base.clone()
        },
    ] {
        assert!(demodulate_unaligned(&signal, &config).is_err());
    }
}

#[test]
fn explicit_oqpsk_branch_is_a_subset_not_a_hidden_truth_selection() {
    let scalars = lcg(17, 1024);
    let iq: Vec<_> = scalars
        .as_chunks::<2>()
        .0
        .iter()
        .map(|x| Complex64::new(x[0], x[1]))
        .collect();
    let config = PhysicalConfig {
        modulation: "OQPSK".into(),
        sample_rate_hz: 48_000.0,
        symbol_rate_hz: 6_000.0,
        recovery_version: "carrier_timing_v2".into(),
        ..Default::default()
    };
    let full = demodulate_unaligned(&iq, &config).unwrap();
    assert_eq!(full.bit_variants.len(), 6);
    for (branch, start) in [("q", 0), ("i", 3)] {
        let selected = demodulate_unaligned(
            &iq,
            &PhysicalConfig {
                delayed_branch: Some(branch.into()),
                ..config.clone()
            },
        )
        .unwrap();
        assert_eq!(selected.bit_variants, full.bit_variants[start..start + 3]);
        assert_eq!(selected.variant_names, full.variant_names[start..start + 3]);
    }
}

#[test]
fn alignment_is_only_scoring_and_missing_bits_remain_explicit() {
    let result = align_for_ber(&[0, 1], &[0, 1, 0, 1], &AlignmentPolicy::default()).unwrap();
    assert_eq!(result.diagnostics["errors_including_missing"], 2);
    assert_eq!(result.diagnostics["overlap_bits"], 2);
    assert_eq!(result.aligned, vec![0, 1, 1, 0]);
    assert!(align_for_ber(&[], &[0], &AlignmentPolicy::default()).is_err());
    assert!(align_for_ber(&[0], &[], &AlignmentPolicy::default()).is_err());
}
