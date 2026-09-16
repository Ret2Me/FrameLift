use super::*;

fn receiver() -> super::super::Config {
    serde_json::from_value(serde_json::json!({
        "waveform":{"hypothesis_id":"memory-test","demodulator_id":"phase_fsk",
        "dsp":{"baud":1200.,"mode":"fsk","rate_errors_ppm":[0.],"phase_bins":8,"top_timing":2,"bank":"full"},
        "decimation":1,"cutoff_hz":900.,"carrier_hz":null,"mark_hz":1200.,"space_hz":2200.,"psk":null},
        "line_coding":"nrzi","scrambler":"none","sequence":{"maximum_training_frames":1},
        "workers":1,"work_budget":2000000000u64
    })).unwrap()
}

fn options() -> Config {
    Config {
        maximum_age_samples: 100_000,
        maximum_models: 4,
        blind_bootstrap: false,
        work_budget: 2_000_000_000,
    }
}

// Independent transmitter: bitwise polynomial, stuffing and NRZI.
fn physical(seed: u8, repetitions: usize) -> Vec<f64> {
    let mut frame = hex::decode("94a662b2a0826094a662b29eb2e103f0").unwrap();
    frame.extend((0..100).map(|n: u8| n.wrapping_mul(37).wrapping_add(seed)));
    let mut state = 0xffffu16;
    for byte in &frame {
        for k in 0..8 {
            let feedback = (state ^ u16::from(byte >> k)) & 1;
            state >>= 1;
            if feedback != 0 {
                state ^= 0x8408;
            }
        }
    }
    frame.extend_from_slice(&(!state).to_le_bytes());
    let mut bits = FLAG.repeat(80);
    for _ in 0..repetitions {
        let mut ones = 0;
        for byte in &frame {
            for k in 0..8 {
                let bit = (byte >> k) & 1;
                bits.push(bit);
                ones = if bit == 1 { ones + 1 } else { 0 };
                if ones == 5 {
                    bits.push(0);
                    ones = 0;
                }
            }
        }
        bits.extend(FLAG.repeat(16));
    }
    bits.extend(FLAG.repeat(200));
    let mut level = 1;
    bits.into_iter()
        .map(|bit| {
            level ^= 1 - bit;
            2. * f64::from(level) - 1.
        })
        .collect()
}

fn iq(soft: &[f64]) -> Vec<Complex64> {
    let mut phase = 0.;
    soft.iter()
        .flat_map(|x| [*x; 8])
        .map(|x| {
            phase += std::f64::consts::TAU * 0.2 / 8. * x;
            Complex64::from_polar(1., phase)
        })
        .collect()
}

#[test]
fn source_order_budget_and_failed_window_transaction() {
    let identity = "a".repeat(64);
    let samples = iq(&physical(3, 1));
    let mut s = Session::new(&identity, 9600, receiver(), options()).unwrap();
    assert!(s.decode_window(&"b".repeat(64), 0, &samples).is_err());
    assert!(s.models.is_empty());
    let first = s.decode_window(&identity, 0, &samples).unwrap();
    assert!(!first.frames.is_empty());
    assert!(first.receipts.is_empty());
    assert!(first.retained_models > 0);
    assert!(s.decode_window(&identity, 0, &samples).is_err());
    let before = s.models.len();
    s.options.work_budget = 1;
    assert!(
        s.decode_window(&identity, samples.len() as u64, &samples)
            .is_err()
    );
    assert_eq!(s.previous_start, Some(0));
    assert_eq!(s.models.len(), before);
}

#[test]
fn overlapping_windows_never_transfer_and_expired_models_are_removed() {
    let identity = "a".repeat(64);
    let samples = iq(&physical(19, 1));
    let mut s = Session::new(&identity, 9600, receiver(), options()).unwrap();
    s.decode_window(&identity, 0, &samples).unwrap();
    let overlap = s.decode_window(&identity, 1, &samples).unwrap();
    assert!(overlap.receipts.is_empty());
    let report = s.decode_window(&identity, 200_000, &samples).unwrap();
    assert!(report.expired_models > 0);
    assert!(report.receipts.is_empty());
}

#[test]
fn causal_transfer_preserves_baseline_and_worker_parity() {
    let identity = "a".repeat(64);
    let samples = iq(&physical(37, 1));
    let mut reports = vec![];
    for workers in [1, 2] {
        let mut c = receiver();
        c.workers = workers;
        let mut s = Session::new(&identity, 9600, c, options()).unwrap();
        s.decode_window(&identity, 0, &samples).unwrap();
        let report = s
            .decode_window(&identity, samples.len() as u64, &samples)
            .unwrap();
        assert!(!report.receipts.is_empty());
        assert!(
            report
                .local
                .frames
                .iter()
                .all(|f| report.frames.iter().any(|g| g.hex == f.hex))
        );
        assert!(
            report
                .receipts
                .iter()
                .all(|r| r.source_window.unwrap().1 <= report.start_sample)
        );
        reports.push(serde_json::to_value(report).unwrap());
    }
    assert_eq!(reports[0], reports[1]);
}

#[test]
fn transferred_isi_model_recovers_crc_valid_target_without_target_anchors() {
    let c = receiver();
    let bits = physical(91, 1);
    let channel = soft_sequence::Channel {
        taps: [0.65, 1., 0.65],
        bias: 0.1,
        noise_variance: 0.001,
    };
    let soft = (0..bits.len())
        .map(|i| {
            channel.bias
                + channel.taps[0] * bits[i.saturating_sub(1)]
                + bits[i]
                + channel.taps[2] * bits[(i + 1).min(bits.len() - 1)]
        })
        .collect();
    let stream = Stream {
        soft,
        threshold: 0.,
        lane: "test".into(),
    };
    assert!(spans(&stream.soft, 0., &c).is_empty());
    let mut receipt = Receipt {
        target_stream: 0,
        kind: "cross_window_bcjr".into(),
        source_window: Some((0, 1)),
        source_stream: Some(0),
        channels: vec![channel],
        excluded_symbol_spans: vec![],
        blind_fits: vec![],
        accepted_frames: vec![],
        reason: "unit supplied channel, not learned end-to-end".into(),
    };
    let mut frames = BTreeMap::new();
    accept(&stream, &mut receipt, &c, 2, 3, &mut frames).unwrap();
    assert_eq!(frames.len(), 1);
    assert_eq!(receipt.accepted_frames.len(), 1);
    receipt.excluded_symbol_spans = vec![(0, bits.len())];
    let mut rejected = BTreeMap::new();
    accept(&stream, &mut receipt, &c, 2, 3, &mut rejected).unwrap();
    assert!(rejected.is_empty());
}

#[test]
fn blind_fit_excludes_both_training_and_validation() {
    let stream = Stream {
        soft: physical(61, 12),
        threshold: 0.,
        lane: "test".into(),
    };
    let (channels, fits, excluded) = blind(&stream, 1).unwrap();
    assert_eq!(channels.len(), 1);
    assert!(
        fits.iter()
            .all(|f| !f.crc_or_reference_bits_used && f.training_symbols_are_inferred)
    );
    assert_eq!(
        excluded[0],
        (fits[0].training_start_symbol, fits[0].validation_end_symbol)
    );
    let mut receipt = Receipt {
        target_stream: 0,
        kind: "blind_bcjr".into(),
        source_window: None,
        source_stream: None,
        channels,
        excluded_symbol_spans: excluded.clone(),
        blind_fits: fits,
        accepted_frames: vec![],
        reason: "test".into(),
    };
    let mut all = BTreeMap::new();
    accept(&stream, &mut receipt, &receiver(), 0, 100, &mut all).unwrap();
    assert!(!all.is_empty());
    for frame in all.values() {
        for p in &frame.provenance {
            assert!(
                excluded
                    .iter()
                    .all(|(a, b)| p.end_symbol <= *a || p.start_symbol >= *b)
            );
        }
    }
}

#[test]
fn transferred_models_and_blind_fits_do_not_accept_random_control() {
    let identity = "a".repeat(64);
    let clean = iq(&physical(41, 1));
    let mut config = options();
    config.blind_bootstrap = true;
    let mut session = Session::new(&identity, 9600, receiver(), config).unwrap();
    session.decode_window(&identity, 0, &clean).unwrap();
    let retained = session.models.len();
    let mut state = 88172645463325252u64;
    let random: Vec<_> = (0..4096)
        .map(|_| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            if state & 1 == 0 { -1. } else { 1. }
        })
        .collect();
    let report = session
        .decode_window(&identity, clean.len() as u64, &iq(&random))
        .unwrap();
    assert!(report.frames.is_empty());
    assert!(report.local.frames.is_empty());
    assert!(!report.receipts.is_empty());
    assert_eq!(session.models.len(), retained);
}
