use super::*;

fn soft(bytes: &[u8]) -> Vec<f64> {
    bytes
        .iter()
        .flat_map(|b| {
            (0..8)
                .rev()
                .map(move |i| if b & (1 << i) != 0 { 4.0 } else { -4.0 })
        })
        .collect()
}
fn bits(bytes: &[u8]) -> Vec<u8> {
    soft(bytes).iter().map(|&x| u8::from(x > 0.0)).collect()
}
fn csp(bytes: usize) -> Vec<u8> {
    let mut frame = vec![0x44, 0x30, 0x41, 0x01];
    frame.extend((0..bytes - 8).map(|i| (i * 73 + 17) as u8));
    frame.extend(space_link::csp_crc32c(&frame).to_be_bytes());
    frame
}
fn config(bytes: usize, code: fec::FrameCode) -> CodedSyncConfig {
    CodedSyncConfig {
        syncword: bits(&[0x1a, 0xcf, 0xfc, 0x1d]),
        maximum_sync_hamming: 1,
        frame_bytes: bytes,
        code,
        randomizer: Randomizer::None,
        validator: FrameValidator::SpaceLink {
            config: space_link::SpaceLinkConfig::CspV1 {
                crc32: space_link::CspCrc32Mode::RequiredHeaderAndPayload,
            },
        },
        maximum_candidates: 8,
    }
}
fn stream(config: &CodedSyncConfig, word: &[u8]) -> Vec<f64> {
    let mut output = vec![-2.0; 13];
    output.extend(
        config
            .syncword
            .iter()
            .map(|&b| if b == 1 { 4.0 } else { -4.0 }),
    );
    let mut payload = soft(word);
    derandomize(&mut payload, &config.randomizer);
    output.extend(payload);
    output
}
#[test]
fn randomizer_matches_published_first_40_bits_and_periods() {
    for (kind, expected, period) in [
        (Randomizer::CcsdsTm255, "ff480ec09a", 255),
        (Randomizer::CcsdsTm131071, "1c71b91ba9", 131071),
        (Randomizer::CcsdsTc255, "ff399e5a68", 255),
    ] {
        let mut values = vec![-1.0; period + 40];
        derandomize(&mut values, &kind);
        let got: Vec<u8> = values[..40]
            .chunks_exact(8)
            .map(|c| c.iter().fold(0, |b, x| (b << 1) | u8::from(*x > 0.0)))
            .collect();
        assert_eq!(hex::encode(got), expected);
        assert_eq!(&values[..40], &values[period..]);
        derandomize(&mut values, &kind);
        assert!(values.iter().all(|&x| x == -1.0));
    }
}
#[test]
fn uncoded_sync_requires_independent_crc_and_rejects_nonfinite_or_truncated() {
    let c = config(32, fec::FrameCode::None);
    let frame = csp(32);
    let s = stream(&c, &frame);
    assert_eq!(decode_sync(&s, 0.0, &c).unwrap()[0].frame, frame);
    let mut bad = s.clone();
    bad[100] *= -1.0;
    assert!(decode_sync(&bad, 0.0, &c).unwrap().is_empty());
    assert!(decode_sync(&s[..s.len() - 1], 0.0, &c).unwrap().is_empty());
    for v in [f64::NAN, f64::INFINITY] {
        bad[100] = v;
        assert!(decode_sync(&bad, 0.0, &c).is_err());
    }
    let mut c = c;
    c.validator = FrameValidator::SpaceLink {
        config: space_link::SpaceLinkConfig::CspV1 {
            crc32: space_link::CspCrc32Mode::Absent,
        },
    };
    assert!(c.validate().is_err());
}
#[test]
fn rs_sync_derandomization_and_crc_recover_damaged_frames() {
    let rs = fec::ReedSolomonConfig::ccsds_rs255_223(1, 223 - 64);
    let frame = csp(64);
    let clean = rs.encode(&frame).unwrap();
    for randomizer in [
        Randomizer::None,
        Randomizer::CcsdsTm255,
        Randomizer::CcsdsTm131071,
    ] {
        let mut c = config(64, fec::FrameCode::ReedSolomon { config: rs.clone() });
        c.randomizer = randomizer;
        let mut word = clean.clone();
        for i in 0..16 {
            word[i * 5] ^= 0xa5;
        }
        let decoded = decode_sync(&stream(&c, &word), 0.0, &c).unwrap();
        assert_eq!(decoded.len(), 1);
        assert_eq!(decoded[0].frame, frame);
        assert!(
            decoded[0]
                .validation_layers
                .contains(&"reed_solomon_syndrome_verified".into())
        );
    }
    // A valid codeword containing a wrong packet CRC is NOT accepted.
    let mut bad = frame;
    bad[63] ^= 1;
    let c = config(64, fec::FrameCode::ReedSolomon { config: rs.clone() });
    assert!(
        decode_sync(&stream(&c, &rs.encode(&bad).unwrap()), 0.0, &c)
            .unwrap()
            .is_empty()
    );
}
// Independent published generator (CCSDS231.0-B-4 table4-1), not H-solving.
fn tc128_encode(bytes: &[u8]) -> Vec<u8> {
    let message = u64::from_be_bytes(bytes.try_into().unwrap());
    let rows = [
        0x0e69166bef4c0bc2u64,
        0x7766137ebb248418,
        0xc480feb9cd53a713,
        0x4eaa22fa465eea11,
    ];
    let mut parity = 0u64;
    for bit in 0..64 {
        if (message >> (63 - bit)) & 1 == 1 {
            for block in 0..4 {
                let chunk = (rows[bit / 16] >> (48 - block * 16)) as u16;
                parity ^= (chunk.rotate_right((bit % 16) as u32) as u64) << (48 - block * 16);
            }
        }
    }
    [bytes, parity.to_be_bytes().as_slice()].concat()
}
#[test]
fn ldpc_soft_correction_flows_through_packet_crc() {
    let frame = csp(8);
    let word = tc128_encode(&frame);
    let c = config(
        8,
        fec::FrameCode::Ldpc {
            config: fec::LdpcConfig::ccsds_tc128(),
        },
    );
    let mut s = stream(&c, &word);
    s[13 + 32 + 31] *= -0.1;
    let decoded = decode_sync(&s, 0.0, &c).unwrap();
    assert_eq!(decoded.len(), 1);
    assert_eq!(decoded[0].frame, frame);
    assert!(
        decoded[0]
            .validation_layers
            .contains(&"ldpc_syndrome_verified".into())
    );
    let mut bad = frame;
    bad[7] ^= 1;
    assert!(
        decode_sync(&stream(&c, &tc128_encode(&bad)), 0.0, &c)
            .unwrap()
            .is_empty()
    );
}
#[test]
fn budget_exhaustion_is_error_not_partial_success() {
    let mut c = config(8, fec::FrameCode::None);
    c.maximum_candidates = 1;
    let a = stream(&c, &csp(8));
    let s = [a.clone(), a].concat();
    assert!(decode_sync(&s, 0.0, &c).unwrap_err().contains("budget"));
    c.maximum_candidates = 2;
    assert_eq!(decode_sync(&s, 0.0, &c).unwrap().len(), 1);
}

#[test]
fn aggregate_fec_candidate_product_is_bounded_even_for_valid_codewords() {
    let mut ldpc = fec::LdpcConfig::ccsds_tc128();
    ldpc.checks = (0..1000)
        .flat_map(|_| ldpc.checks.iter().cloned())
        .collect();
    ldpc.max_iterations = 100;
    ldpc.validate().unwrap();
    let c = config(8, fec::FrameCode::Ldpc { config: ldpc });
    let one = stream(&c, &tc128_encode(&csp(8)));
    let repeated = one.repeat(4);
    assert!(
        decode_sync(&repeated, 0.0, &c)
            .unwrap_err()
            .contains("aggregate coded-candidate work")
    );
}

fn legacy_sync_starts(
    soft: &[f64],
    threshold: f64,
    syncword: &[u8],
    maximum_hamming: usize,
    end_exclusive: usize,
) -> Vec<usize> {
    (0..end_exclusive)
        .filter(|&start| {
            soft[start..start + syncword.len()]
                .iter()
                .zip(syncword)
                .filter(|(value, bit)| u8::from(**value >= threshold) != **bit)
                .take(maximum_hamming + 1)
                .count()
                <= maximum_hamming
        })
        .collect()
}

#[test]
fn rolling_sync_matches_legacy_at_every_start_and_inclusive_threshold() {
    let mut state = 0x5b90_4da7_f11c_022du64;
    for width in [32usize, 33, 63, 64, 65, 127, 128] {
        let syncword: Vec<u8> = (0..width)
            .map(|i| u8::from((i * 73 + i / 7) % 11 < 5))
            .collect();
        for extra in [0usize, 1, 2, 7, 137] {
            let soft: Vec<f64> = (0..width + extra)
                .map(|i| {
                    state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
                    match (state as usize + i) % 6 {
                        0 => -1.0,
                        1 => -0.0,
                        2 => 0.0,
                        3 => 1.0,
                        4 => -0.25,
                        _ => 0.25,
                    }
                })
                .collect();
            let end = extra + 1;
            for threshold in [-0.25, -0.0, 0.0, 0.25] {
                for maximum in 0..=2 {
                    assert_eq!(
                        SyncStarts::new(&soft, threshold, &syncword, maximum, end)
                            .collect::<Vec<_>>(),
                        legacy_sync_starts(&soft, threshold, &syncword, maximum, end),
                        "width={width} extra={extra} threshold={threshold:?} maximum={maximum}"
                    );
                }
            }
        }
    }
    // Dense and edge-position controls exercise all matches and the 128-bit
    // mask without changing candidate order.
    let syncword = vec![0u8; 128];
    let mut soft = vec![-1.0; 512];
    for index in [0usize, 127, 128, 255, 511] {
        soft[index] = 1.0;
    }
    let end = soft.len() - syncword.len() + 1;
    for maximum in 0..=2 {
        assert_eq!(
            SyncStarts::new(&soft, 0.0, &syncword, maximum, end).collect::<Vec<_>>(),
            legacy_sync_starts(&soft, 0.0, &syncword, maximum, end)
        );
    }
}

#[test]
fn rolling_sync_accepts_planted_zero_one_two_but_not_three_mismatches() {
    let mut state = 0x2cf4_1992_75a8_d36bu64;
    for width in [32usize, 33, 63, 64, 65, 127, 128] {
        let syncword: Vec<u8> = (0..width)
            .map(|i| u8::from((i * 29 + i / 5) % 13 < 6))
            .collect();
        let soft_len = width + 137;
        for planted_start in [0usize, soft_len - width] {
            for threshold in [-0.0, 0.0] {
                for mismatches in 0..=3 {
                    let mut soft: Vec<f64> = (0..soft_len)
                        .map(|_| {
                            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
                            if state >> 63 == 1 { 1.0 } else { -1.0 }
                        })
                        .collect();
                    let mismatch_positions = [0usize, width / 2, width - 1];
                    for (offset, &expected) in syncword.iter().enumerate() {
                        let observed =
                            expected ^ u8::from(mismatch_positions[..mismatches].contains(&offset));
                        soft[planted_start + offset] = if observed == 1 { threshold } else { -1.0 };
                    }
                    let end = soft.len() - width + 1;
                    for maximum in 0..=2 {
                        let rolling = SyncStarts::new(&soft, threshold, &syncword, maximum, end)
                            .collect::<Vec<_>>();
                        assert_eq!(
                            rolling,
                            legacy_sync_starts(&soft, threshold, &syncword, maximum, end,),
                            "width={width} start={planted_start} threshold={threshold:?} mismatches={mismatches} maximum={maximum}"
                        );
                        assert_eq!(
                            rolling.contains(&planted_start),
                            mismatches <= maximum,
                            "planted width={width} start={planted_start} threshold={threshold:?} mismatches={mismatches} maximum={maximum}"
                        );
                    }
                }
            }
        }
    }
}

#[test]
fn rolling_sync_preserves_full_result_order_dedup_and_error_precedence() {
    let mut c = config(12, fec::FrameCode::None);
    c.syncword = vec![1; 32];
    c.maximum_sync_hamming = 0;
    let first = csp(12);
    let mut second = first.clone();
    second[4] ^= 0x20;
    let payload_end = second.len() - 4;
    let crc = space_link::csp_crc32c(&second[..payload_end]);
    second[payload_end..].copy_from_slice(&crc.to_be_bytes());
    let combined = [stream(&c, &first), stream(&c, &second), stream(&c, &first)].concat();
    for threshold in [-0.0, 0.0] {
        let decoded = decode_sync(&combined, threshold, &c).unwrap();
        assert_eq!(
            decoded.iter().map(|frame| &frame.frame).collect::<Vec<_>>(),
            vec![&first, &second]
        );
    }

    let mut invalid = first.clone();
    invalid[7] ^= 1;
    let repeated_invalid = stream(&c, &invalid).repeat(3);
    c.maximum_candidates = 2;
    assert_eq!(
        decode_sync(&repeated_invalid, 0.0, &c).unwrap_err(),
        "sync candidate budget exceeded; no partial result accepted"
    );

    let mut malformed = c.clone();
    malformed.syncword.pop();
    let nonfinite = vec![f64::NAN; 256];
    assert_eq!(
        decode_sync(&nonfinite, 0.0, &malformed).unwrap_err(),
        "invalid sync, frame length, Hamming or candidate budget"
    );
    assert_eq!(
        decode_sync(&nonfinite, 0.0, &c).unwrap_err(),
        "coded sync requires bounded finite soft bits/threshold"
    );
}
