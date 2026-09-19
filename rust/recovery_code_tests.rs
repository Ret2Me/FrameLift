use super::*;
use crate::{
    fec::SymbolBasis,
    protocol,
    space_link::{CspCrc32Mode, SpaceLinkConfig},
};

fn convolutional(termination: ConvolutionalTermination) -> ConvolutionalConfig {
    ConvolutionalConfig {
        generators: [79, 109],
        invert: [false, false],
        initial_state: 0,
        termination,
    }
}

fn ax25() -> Vec<u8> {
    let mut bytes: Vec<u8> = b"GROUND".iter().map(|b| b << 1).collect();
    bytes.push(0x60);
    bytes.extend(b"TEST  ".iter().map(|b| b << 1));
    bytes.extend([0x61, 3, 0xf0]);
    bytes.extend(b"Telemetry");
    bytes.extend(protocol::crc16_x25(&bytes).to_le_bytes());
    bytes
}

fn llr(bits: &[u8]) -> Vec<f64> {
    bits.iter().map(|&b| 9.0 * (2.0 * b as f64 - 1.0)).collect()
}

fn tc128_oracle(bytes: &[u8; 8]) -> Vec<u8> {
    // Published CCSDS TC128 generator circulants, independent of parity H.
    let rows = [
        0x0e69166bef4c0bc2_u64,
        0x7766137ebb248418,
        0xc480feb9cd53a713,
        0x4eaa22fa465eea11,
    ];
    let mut parity = 0_u64;
    for bit in 0..64 {
        if bytes[bit / 8] & (1 << (7 - bit % 8)) != 0 {
            for column in 0..4 {
                let chunk = (rows[bit / 16] >> (48 - column * 16)) as u16;
                parity ^= u64::from(chunk.rotate_right((bit % 16) as u32)) << (48 - column * 16);
            }
        }
    }
    bits_from_bytes(&[bytes.as_slice(), &parity.to_be_bytes()].concat())
}

/// Test-only feed-forward tapped delay line, no production encoder calls.
fn conv_oracle(bits: &[u8], config: &ConvolutionalConfig) -> Vec<u8> {
    let mut delay: Vec<u8> = (0..6).map(|i| (config.initial_state >> i) & 1).collect();
    let tail = if config.termination == ConvolutionalTermination::ZeroTail {
        6
    } else {
        0
    };
    let mut encoded = Vec::new();
    for bit in bits.iter().copied().chain(std::iter::repeat_n(0, tail)) {
        delay.insert(0, bit);
        for j in 0..2 {
            let out = (0..7).fold(u8::from(config.invert[j]), |p, k| {
                p ^ (delay[k] & ((config.generators[j] >> k) & 1))
            });
            encoded.push(out);
        }
        delay.pop();
    }
    encoded
}

fn csp_validator() -> FrameValidator {
    FrameValidator::SpaceLink {
        config: SpaceLinkConfig::CspV1 {
            crc32: CspCrc32Mode::RequiredHeaderAndPayload,
        },
    }
}

fn rs_config(bytes: usize) -> ReedSolomonConfig {
    ReedSolomonConfig {
        field_polynomial: 0x11d,
        first_root: 0,
        primitive_step: 1,
        parity_symbols: 16,
        shortening: 255 - 16 - bytes,
        interleaving: 1,
        basis: SymbolBasis::Conventional,
    }
}

/// Independent shift-register RS encoder using bitwise polynomial multiply;
/// no production field lookup table, generator or codec method is called.
fn rs_oracle(bytes: &[u8], config: &ReedSolomonConfig) -> Vec<u8> {
    fn multiply(mut a: u16, mut b: u8, polynomial: u16) -> u8 {
        let mut out = 0_u16;
        for _ in 0..8 {
            if b & 1 != 0 {
                out ^= a;
            }
            b >>= 1;
            a <<= 1;
            if a & 0x100 != 0 {
                a ^= polynomial;
            }
        }
        out as u8
    }
    let mut coefficients = vec![1];
    for j in 0..config.parity_symbols {
        let mut root = 1;
        for _ in 0..((config.first_root + j) * config.primitive_step) % 255 {
            root = multiply(root as u16, 2, config.field_polynomial);
        }
        let mut next = vec![0; coefficients.len() + 1];
        for (i, &coefficient) in coefficients.iter().enumerate() {
            next[i] ^= coefficient;
            next[i + 1] ^= multiply(coefficient as u16, root, config.field_polynomial);
        }
        coefficients = next;
    }
    let mut shift = vec![0; config.parity_symbols];
    for &byte in bytes {
        let feedback = byte ^ shift[0];
        shift.rotate_left(1);
        *shift.last_mut().unwrap() = 0;
        for (i, x) in shift.iter_mut().enumerate() {
            *x ^= multiply(
                feedback as u16,
                coefficients[i + 1],
                config.field_polynomial,
            );
        }
    }
    [bytes, &shift].concat()
}

#[test]
fn received_ax25_fcs_required_and_uncoded_has_no_fake_feedback() {
    let mut frame = ax25();
    let code = CodeProfile::Uncoded {
        frame_bytes: frame.len(),
    };
    let decoded = code
        .decode(&llr(&bits_from_bytes(&frame)), &FrameValidator::Ax25)
        .unwrap();
    let accepted = decoded.accepted.unwrap();
    assert_eq!(accepted.bytes, frame);
    assert_eq!(accepted.validated_codeword, bits_from_bytes(&frame));
    assert!(decoded.posterior_llr.is_none() && decoded.extrinsic_llr.is_none());
    assert!(!decoded.parity_verified);
    frame[16] ^= 1;
    assert!(
        code.decode(&llr(&bits_from_bytes(&frame)), &FrameValidator::Ax25)
            .unwrap()
            .accepted
            .is_none()
    );
}

#[test]
fn k7_independent_impulse_and_damaged_ax25_round_trip() {
    let config = convolutional(ConvolutionalTermination::Unconstrained);
    assert_eq!(
        conv_oracle(&[1, 0, 0, 0, 0, 0, 0], &config),
        [1, 1, 1, 0, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1]
    );
    let frame = ax25();
    for termination in [
        ConvolutionalTermination::Unconstrained,
        ConvolutionalTermination::ZeroTail,
    ] {
        for invert in [[false, false], [true, false], [false, true], [true, true]] {
            for initial_state in [0, 17, 63] {
                let config = ConvolutionalConfig {
                    invert,
                    initial_state,
                    ..convolutional(termination)
                };
                let word = conv_oracle(&bits_from_bytes(&frame), &config);
                let mut input = llr(&word);
                for position in (9..input.len() - 12).step_by(97) {
                    input[position] *= -0.2;
                }
                let code = CodeProfile::ConvolutionalK7 {
                    frame_bytes: frame.len(),
                    config,
                };
                let output = code.decode(&input, &FrameValidator::Ax25).unwrap();
                assert_eq!(output.accepted.as_ref().unwrap().bytes, frame);
                assert_eq!(output.accepted.unwrap().validated_codeword, word);
                assert_eq!(output.extrinsic_llr.unwrap().len(), input.len());
                assert!(output.posterior_llr.unwrap().iter().all(|x| x.is_finite()));
            }
        }
    }
}

#[test]
fn exact_bcjr_matches_exhaustive_independent_path_probabilities() {
    for termination in [
        ConvolutionalTermination::Unconstrained,
        ConvolutionalTermination::ZeroTail,
    ] {
        let config = ConvolutionalConfig {
            initial_state: 17,
            invert: [true, false],
            ..convolutional(termination)
        };
        let n = 4;
        let len = 2 * (n + config.tail_bits());
        let input: Vec<f64> = (0..len)
            .map(|i| ((i * 13 % 11) as f64 - 5.0) * 0.13)
            .collect();
        let decoded = bcjr(&input, n, &config);
        for wire in 0..len {
            let mut weights = [0.0; 2];
            let mut external_weights = [0.0; 2];
            for value in 0..1 << n {
                let bits: Vec<u8> = (0..n).map(|i| ((value >> i) & 1) as u8).collect();
                let word = conv_oracle(&bits, &config);
                let score: f64 = word
                    .iter()
                    .zip(&input)
                    .map(|(&b, &x)| 0.5 * x * (2.0 * b as f64 - 1.0))
                    .sum();
                let bit = word[wire] as usize;
                weights[bit] += score.exp();
                external_weights[bit] +=
                    (score - 0.5 * input[wire] * (2.0 * bit as f64 - 1.0)).exp();
            }
            let expected = (weights[1].ln() - weights[0].ln()).clamp(-LLR_LIMIT, LLR_LIMIT);
            let external =
                (external_weights[1].ln() - external_weights[0].ln()).clamp(-LLR_LIMIT, LLR_LIMIT);
            assert!(
                (decoded.posterior[wire] - expected).abs() < 1e-10,
                "{termination:?} wire {wire}"
            );
            assert!((decoded.extrinsic[wire] - external).abs() < 1e-10);
        }
    }
}

#[test]
fn own_channel_llr_is_not_recycled_as_convolutional_extrinsic() {
    let config = convolutional(ConvolutionalTermination::ZeroTail);
    let input: Vec<f64> = (0..76)
        .map(|i| ((i * 11 % 9) as f64 - 4.0) * 0.21)
        .collect();
    let original = bcjr(&input, 32, &config);
    for i in 0..input.len() {
        let mut changed = input.clone();
        changed[i] += 2.7;
        let compared = bcjr(&changed, 32, &config);
        assert!(
            (original.extrinsic[i] - compared.extrinsic[i]).abs() < 1e-10,
            "wire {i}"
        );
    }
}

#[test]
fn reed_solomon_corrects_independent_encoded_frame_without_soft_fabrication() {
    let frame = ax25();
    let config = rs_config(frame.len());
    let word = rs_oracle(&frame, &config);
    assert_eq!(word, config.encode(&frame).unwrap());
    let code = CodeProfile::ReedSolomon { config };
    let mut damaged = word.clone();
    for i in 0..8 {
        damaged[i * 3] ^= (13 + i) as u8;
    }
    let output = code
        .decode(&llr(&bits_from_bytes(&damaged)), &FrameValidator::Ax25)
        .unwrap();
    assert_eq!(output.corrected_symbols, Some(8));
    assert!(output.parity_verified);
    assert!(output.posterior_llr.is_none() && output.extrinsic_llr.is_none());
    let accepted = output.accepted.unwrap();
    assert_eq!(accepted.bytes, frame);
    assert_eq!(accepted.validated_codeword, bits_from_bytes(&word));
}

#[test]
fn concatenated_rs_convolutional_uses_received_integrity_and_only_inner_extrinsic() {
    let frame = ax25();
    let reed_solomon = rs_config(frame.len());
    let convolutional = convolutional(ConvolutionalTermination::ZeroTail);
    let rs = rs_oracle(&frame, &reed_solomon);
    let word = conv_oracle(&bits_from_bytes(&rs), &convolutional);
    let code = CodeProfile::ConvolutionalReedSolomon {
        convolutional: convolutional.clone(),
        reed_solomon,
        interstage_randomizer: crate::coded::Randomizer::None,
    };
    let mut soft = llr(&word);
    for i in (17..soft.len()).step_by(91) {
        soft[i] *= -0.1;
    }
    let inner = bcjr(&soft, rs.len() * 8, &convolutional);
    let output = code.decode(&soft, &FrameValidator::Ax25).unwrap();
    assert_eq!(output.extrinsic_llr.unwrap(), inner.extrinsic);
    assert_eq!(output.accepted.as_ref().unwrap().bytes, frame);
    assert_eq!(output.accepted.unwrap().validated_codeword, word);
}

#[test]
fn valid_fec_never_overrules_invalid_received_crc() {
    let mut bytes = ax25();
    *bytes.last_mut().unwrap() ^= 1;
    let rs = rs_config(bytes.len());
    let conv = convolutional(ConvolutionalTermination::ZeroTail);
    let words = [
        bits_from_bytes(&bytes),
        conv_oracle(&bits_from_bytes(&bytes), &conv),
        bits_from_bytes(&rs_oracle(&bytes, &rs)),
        conv_oracle(&bits_from_bytes(&rs_oracle(&bytes, &rs)), &conv),
    ];
    let profiles = [
        CodeProfile::Uncoded {
            frame_bytes: bytes.len(),
        },
        CodeProfile::ConvolutionalK7 {
            frame_bytes: bytes.len(),
            config: conv.clone(),
        },
        CodeProfile::ReedSolomon { config: rs.clone() },
        CodeProfile::ConvolutionalReedSolomon {
            convolutional: conv,
            reed_solomon: rs,
            interstage_randomizer: crate::coded::Randomizer::None,
        },
    ];
    for (code, word) in profiles.iter().zip(words) {
        let output = code.decode(&llr(&word), &FrameValidator::Ax25).unwrap();
        assert!(output.accepted.is_none());
        assert!(output.rejection_reason.is_some());
    }
}

#[test]
fn structural_only_validator_and_no_evidence_never_accept() {
    let profile = CodeProfile::Uncoded { frame_bytes: 8 };
    let absent = FrameValidator::SpaceLink {
        config: SpaceLinkConfig::CspV1 {
            crc32: CspCrc32Mode::Absent,
        },
    };
    assert!(profile.decode(&[1.0; 64], &absent).is_err());
    let profiles = [
        profile,
        CodeProfile::Ldpc {
            config: LdpcConfig::ccsds_tc128(),
        },
        CodeProfile::ConvolutionalK7 {
            frame_bytes: 8,
            config: convolutional(ConvolutionalTermination::ZeroTail),
        },
        CodeProfile::ReedSolomon {
            config: rs_config(8),
        },
    ];
    for code in profiles {
        let output = code
            .decode(&vec![0.0; code.encoded_bits().unwrap()], &csp_validator())
            .unwrap();
        assert!(output.accepted.is_none());
        assert!(output.extrinsic_llr.is_none());
    }
}

#[test]
fn all_profiles_reject_nan_wrong_lengths_and_invalid_dimensions() {
    let profiles = [
        CodeProfile::Uncoded { frame_bytes: 8 },
        CodeProfile::ConvolutionalK7 {
            frame_bytes: 8,
            config: convolutional(ConvolutionalTermination::ZeroTail),
        },
        CodeProfile::ReedSolomon {
            config: rs_config(8),
        },
        CodeProfile::ConvolutionalReedSolomon {
            convolutional: convolutional(ConvolutionalTermination::ZeroTail),
            reed_solomon: rs_config(8),
            interstage_randomizer: crate::coded::Randomizer::None,
        },
        CodeProfile::Ldpc {
            config: LdpcConfig::ccsds_tc128(),
        },
    ];
    for code in profiles {
        let n = code.encoded_bits().unwrap();
        assert!(code.work_per_pass().unwrap() >= n as u64);
        assert!(code.decode(&vec![1.0; n - 1], &csp_validator()).is_err());
        for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut input = vec![1.0; n];
            input[n / 2] = value;
            assert!(code.decode(&input, &csp_validator()).is_err());
        }
    }
    for n in [0, 8193, usize::MAX] {
        assert!(CodeProfile::Uncoded { frame_bytes: n }.validate().is_err());
        assert!(
            CodeProfile::ConvolutionalK7 {
                frame_bytes: n,
                config: convolutional(ConvolutionalTermination::ZeroTail)
            }
            .validate()
            .is_err()
        );
    }
    assert!(
        CodeProfile::Uncoded { frame_bytes: 8192 }
            .validate()
            .is_ok()
    );
    assert!(
        CodeProfile::ConvolutionalK7 {
            frame_bytes: 4096,
            config: convolutional(ConvolutionalTermination::ZeroTail)
        }
        .validate()
        .is_err()
    );
    for generators in [[0, 109], [128, 79], [79, 79], [3, 5], [127, 127]] {
        assert!(
            ConvolutionalConfig {
                generators,
                ..convolutional(ConvolutionalTermination::ZeroTail)
            }
            .validate()
            .is_err()
        );
    }
    assert!(
        ConvolutionalConfig {
            initial_state: 64,
            ..convolutional(ConvolutionalTermination::ZeroTail)
        }
        .validate()
        .is_err()
    );
}

#[test]
fn serde_preserves_legacy_ldpc_and_rejects_unknown_fields() {
    let config = LdpcConfig::ccsds_tc128();
    let raw = serde_json::to_string(&config).unwrap();
    let profile: CodeProfile = serde_json::from_str(&raw).unwrap();
    assert_eq!(serde_json::to_string(&profile).unwrap(), raw);
    assert_eq!(profile.as_ldpc().unwrap().codeword_bits, 128);
    for text in [
        r#"{"kind":"uncoded","frame_bytes":20,"extra":1}"#,
        r#"{"kind":"unknown"}"#,
    ] {
        assert!(serde_json::from_str::<CodeProfile>(text).is_err());
    }
    let conv = CodeProfile::ConvolutionalK7 {
        frame_bytes: 27,
        config: convolutional(ConvolutionalTermination::ZeroTail),
    };
    let text = serde_json::to_string(&conv).unwrap();
    assert_eq!(
        serde_json::from_str::<CodeProfile>(&text)
            .unwrap()
            .encoded_bits()
            .unwrap(),
        444
    );
}

#[test]
fn extreme_finite_channel_values_do_not_produce_nan() {
    let frame = ax25();
    let config = convolutional(ConvolutionalTermination::ZeroTail);
    let soft: Vec<_> = conv_oracle(&bits_from_bytes(&frame), &config)
        .iter()
        .map(|&b| if b == 1 { f64::MAX } else { -f64::MAX })
        .collect();
    let profile = CodeProfile::ConvolutionalK7 {
        frame_bytes: frame.len(),
        config,
    };
    let output = profile.decode(&soft, &FrameValidator::Ax25).unwrap();
    assert!(output.accepted.is_some());
    assert!(output.posterior_llr.unwrap().iter().all(|x| x.is_finite()));
    assert!(output.extrinsic_llr.unwrap().iter().all(|x| x.is_finite()));
}

#[test]
fn ldpc_matches_existing_soft_decoder_and_needs_received_crc() {
    let mut bytes = [0_u8, 1, 0, 1, 0, 0, 0, 0];
    let crc = crate::space_link::csp_crc32c(&bytes[..4]);
    bytes[4..].copy_from_slice(&crc.to_be_bytes());
    let profile = CodeProfile::from(LdpcConfig::ccsds_tc128());
    for bad_crc in [false, true] {
        let mut frame = bytes;
        if bad_crc {
            frame[7] ^= 1;
        }
        // Published TC128 generator, already independent of the H decoder.
        let word = tc128_oracle(&frame);
        let mut soft = llr(&word);
        soft[9] *= -0.1;
        let expected = profile.as_ldpc().unwrap().decode_soft(&soft).unwrap();
        let output = profile.decode(&soft, &csp_validator()).unwrap();
        assert!(expected.converged && output.parity_verified);
        assert_eq!(output.posterior_llr.unwrap(), expected.posterior_llr);
        assert_eq!(output.extrinsic_llr.unwrap(), expected.extrinsic_llr);
        assert_eq!(output.accepted.is_some(), !bad_crc);
        if let Some(accepted) = output.accepted {
            assert_eq!(accepted.bytes, frame);
            assert_eq!(accepted.validated_codeword, word);
        }
    }
}

#[test]
fn ldpc_reliability_list_has_a_deterministic_trapping_set_escape() {
    let mut frame = [0_u8, 1, 0, 1, 0, 0, 0, 0];
    let crc = crate::space_link::csp_crc32c(&frame[..4]);
    frame[4..].copy_from_slice(&crc.to_be_bytes());
    let word = tc128_oracle(&frame);
    let mut wrong_frame = frame;
    wrong_frame[0] ^= 0x80;
    let wrong_word = tc128_oracle(&wrong_frame);
    let profile = CodeProfile::from(LdpcConfig::ccsds_tc128());
    // Two legitimate LDPC words share all strong equal positions. Differing
    // positions are weakly biased toward the word with the invalid received
    // CRC. The bounded channel list must be able to leave that local decision.
    let soft: Vec<f64> = word
        .iter()
        .zip(&wrong_word)
        .map(|(&valid, &wrong)| {
            if valid == wrong {
                if valid == 1 { 8.0 } else { -8.0 }
            } else if wrong == 1 {
                0.05
            } else {
                -0.05
            }
        })
        .collect();
    assert!(
        profile
            .decode(&soft, &csp_validator())
            .unwrap()
            .accepted
            .is_none()
    );
    let list = ListConfig {
        maximum_hypotheses: 256,
        unreliable_bits: 8,
        maximum_work: u64::MAX,
        ..ListConfig::default()
    };
    let output = profile.decode_list(&soft, &csp_validator(), &list).unwrap();
    assert_eq!(output.accepted.as_ref().unwrap().bytes, frame);
    assert!(output.accepted_hypothesis.is_some());
    assert!(output.parity_verified);
}

#[test]
fn beyond_rs_radius_is_rejected_without_aborting_capture() {
    let bytes = ax25();
    let config = rs_config(bytes.len());
    let mut word = rs_oracle(&bytes, &config);
    for (i, byte) in word[..9].iter_mut().enumerate() {
        *byte ^= 13 + i as u8;
    }
    let profile = CodeProfile::ReedSolomon { config };
    let output = profile
        .decode(&llr(&bits_from_bytes(&word)), &FrameValidator::Ax25)
        .unwrap();
    assert!(output.accepted.is_none());
    assert!(output.rejection_reason.is_some());
    assert!(output.extrinsic_llr.is_none());
}

#[test]
fn rs_reliability_list_recovers_one_symbol_beyond_hard_radius() {
    let frame = ax25();
    let config = rs_config(frame.len());
    let mut word = rs_oracle(&frame, &config);
    for (i, byte) in word[..9].iter_mut().enumerate() {
        *byte ^= 1 + i as u8;
    }
    let profile = CodeProfile::ReedSolomon { config };
    let mut soft = llr(&bits_from_bytes(&word));
    // Make all changed bits in the first damaged symbol uniquely uncertain.
    // Flipping them leaves the independently exercised eight-symbol pattern.
    for bit in [4, 5, 7] {
        soft[bit] = soft[bit].signum() * 0.01;
    }
    assert!(
        profile
            .decode(&soft, &FrameValidator::Ax25)
            .unwrap()
            .accepted
            .is_none()
    );
    let output = profile
        .decode_list(
            &soft,
            &FrameValidator::Ax25,
            &ListConfig {
                maximum_hypotheses: 8,
                unreliable_bits: 3,
                ..ListConfig::default()
            },
        )
        .unwrap();
    assert_eq!(output.accepted.as_ref().unwrap().bytes, frame);
    assert_eq!(output.accepted_hypothesis, Some(3));
    assert_eq!(output.list_hypotheses, 3);
    assert!(output.accepted.unwrap().validation_layers[0].contains("reliability_list"));
}

#[test]
fn convolutional_list_uses_information_probability_and_received_crc() {
    let frame = ax25();
    let mut wrong = frame.clone();
    wrong[0] ^= 0x80;
    let convolutional = convolutional(ConvolutionalTermination::ZeroTail);
    let profile = CodeProfile::ConvolutionalK7 {
        frame_bytes: frame.len(),
        config: convolutional.clone(),
    };
    let soft = llr(&conv_oracle(&bits_from_bytes(&wrong), &convolutional));
    assert!(
        profile
            .decode(&soft, &FrameValidator::Ax25)
            .unwrap()
            .accepted
            .is_none()
    );
    let output = profile
        .decode_list(
            &soft,
            &FrameValidator::Ax25,
            &ListConfig {
                maximum_hypotheses: 2,
                unreliable_bits: 1,
                ..ListConfig::default()
            },
        )
        .unwrap();
    assert_eq!(output.accepted.as_ref().unwrap().bytes, frame);
    assert_eq!(output.accepted_hypothesis, Some(1));
    assert_eq!(
        output.accepted.unwrap().validated_codeword,
        conv_oracle(&bits_from_bytes(&frame), &convolutional)
    );
}

#[test]
fn list_configuration_is_explicitly_work_bounded() {
    let profile = CodeProfile::Ldpc {
        config: LdpcConfig::ccsds_tc128(),
    };
    let mut config = ListConfig::default();
    config.maximum_work = 1;
    assert!(config.validate(&profile).is_err());
    config.maximum_work = u64::MAX;
    for invalid in [0, 1, 257] {
        config.maximum_hypotheses = invalid;
        assert!(config.validate(&profile).is_err());
    }
    config.maximum_hypotheses = 2;
    config.unreliable_bits = 17;
    assert!(config.validate(&profile).is_err());
}

#[test]
fn concatenated_tm_randomizer_stage_matches_independent_recurrence() {
    let frame = ax25();
    let reed_solomon = rs_config(frame.len());
    let convolutional = convolutional(ConvolutionalTermination::ZeroTail);
    let outer = bits_from_bytes(&rs_oracle(&frame, &reed_solomon));
    // Independent unrolled x^8+x^7+x^5+x^3+1 recurrence, all-one seed.
    let mut sequence = vec![1_u8; 8];
    for i in 8..outer.len() {
        sequence.push(sequence[i - 8] ^ sequence[i - 5] ^ sequence[i - 3] ^ sequence[i - 1]);
    }
    assert_eq!(&bytes_from_bits(&sequence)[..4], &[0xff, 0x48, 0x0e, 0xc0]);
    for randomized in [false, true] {
        let input: Vec<_> = outer
            .iter()
            .zip(&sequence)
            .map(|(&b, &r)| b ^ if randomized { r } else { 0 })
            .collect();
        let word = conv_oracle(&input, &convolutional);
        let profile = CodeProfile::ConvolutionalReedSolomon {
            convolutional: convolutional.clone(),
            reed_solomon: reed_solomon.clone(),
            interstage_randomizer: if randomized {
                crate::coded::Randomizer::CcsdsTm255
            } else {
                crate::coded::Randomizer::None
            },
        };
        let output = profile.decode(&llr(&word), &FrameValidator::Ax25).unwrap();
        assert_eq!(output.accepted.as_ref().unwrap().bytes, frame);
        assert_eq!(output.accepted.unwrap().validated_codeword, word);
        let expected = bcjr(&llr(&word), input.len(), &convolutional);
        assert_eq!(output.extrinsic_llr.unwrap(), expected.extrinsic);
        let mut json = serde_json::to_value(&profile).unwrap();
        if !randomized {
            json.as_object_mut()
                .unwrap()
                .remove("interstage_randomizer");
        }
        let from_json: CodeProfile = serde_json::from_value(json).unwrap();
        assert!(
            from_json
                .decode(&llr(&word), &FrameValidator::Ax25)
                .unwrap()
                .accepted
                .is_some()
        );
    }
}
