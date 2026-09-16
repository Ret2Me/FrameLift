use super::*;
use std::f64::consts::TAU;

fn config() -> Config {
    Config {
        waveform: generic::Waveform {
            hypothesis_id: "test-fsk".into(),
            demodulator_id: "phase_fsk".into(),
            dsp: dsp::DspConfig {
                baud: 1200.,
                mode: "fsk".into(),
                rate_errors_ppm: vec![0.],
                phase_bins: 8,
                top_timing: 8,
                bank: "full".into(),
            },
            decimation: 1,
            cutoff_hz: Some(900.),
            carrier_hz: None,
            mark_hz: 1200.,
            space_hz: 2200.,
            psk: None,
        },
        line_coding: LineCoding::Nrzi,
        scrambler: Scrambler::None,
        sequence: Some(SequenceConfig {
            maximum_training_frames: 1,
        }),
        workers: 1,
        work_budget: 2_000_000_000,
    }
}

fn frame(n: usize, seed: u8) -> Vec<u8> {
    let mut bytes = hex::decode("94a662b2a0826094a662b29eb2e103f0").unwrap();
    bytes.extend((0..n).map(|i| (i as u8).wrapping_mul(37).wrapping_add(seed)));
    // Independent bit-at-a-time transmit FCS, not the receiver table.
    let mut crc = 0xffff_u16;
    for byte in &bytes {
        for k in 0..8 {
            let feedback = (crc ^ u16::from(byte >> k)) & 1;
            crc >>= 1;
            if feedback != 0 {
                crc ^= 0x8408;
            }
        }
    }
    bytes.extend_from_slice(&(crc ^ 0xffff).to_le_bytes());
    bytes
}

fn stuffed(frame: &[u8]) -> Vec<u8> {
    let mut bits = Vec::new();
    let mut ones = 0;
    for byte in frame {
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
    bits
}

fn levels(frames: &[Vec<u8>], c: &Config) -> (Vec<u8>, Vec<(usize, usize)>) {
    // The unchanged FSK frontend deliberately removes 512 symbols for its DC
    // estimate plus 32 timing guards. Put test packets after that warm-up.
    let mut bits = FLAG.repeat(80);
    let mut spans = vec![];
    for frame in frames {
        let start = bits.len();
        bits.extend(stuffed(frame));
        spans.push((start, bits.len()));
        bits.extend(FLAG.repeat(16));
    }
    while bits.len() < 2200 {
        bits.extend(FLAG);
    }
    if c.scrambler == Scrambler::G3ruh {
        for i in 0..bits.len() {
            if i >= 12 {
                bits[i] ^= bits[i - 12];
            }
            if i >= 17 {
                bits[i] ^= bits[i - 17];
            }
        }
    }
    if c.line_coding == LineCoding::Nrzi {
        let mut level = 1;
        for bit in &mut bits {
            level ^= 1 - *bit;
            *bit = level;
        }
    }
    (bits, spans)
}

fn modulate(levels: &[u8], mode: &str, sps: usize) -> Vec<Complex64> {
    match mode {
        "fsk" => {
            let mut phase = 0.;
            levels
                .iter()
                .flat_map(|bit| (0..sps).map(move |_| *bit))
                .map(|bit| {
                    phase += TAU * 0.2 / sps as f64 * (2. * f64::from(bit) - 1.);
                    Complex64::from_polar(1., phase)
                })
                .collect()
        }
        "bpsk" => levels
            .iter()
            .flat_map(|bit| (0..sps).map(move |_| Complex64::new(2. * f64::from(*bit) - 1., 0.)))
            .collect(),
        "qpsk" | "oqpsk" => {
            let mut out = vec![Complex64::default(); levels.len().div_ceil(2) * sps + sps];
            for (i, pair) in levels.chunks(2).enumerate() {
                for k in 0..sps {
                    out[i * sps + k].re = 2. * f64::from(pair[0]) - 1.;
                    out[i * sps + k + if mode == "oqpsk" { sps / 2 } else { 0 }].im =
                        2. * f64::from(*pair.get(1).unwrap_or(&0)) - 1.;
                }
            }
            out
        }
        _ => unreachable!(),
    }
}

#[test]
fn exact_variable_lengths_stuffing_and_explicit_line_coding() {
    let frames = vec![frame(0, 0), frame(1, 255), frame(37, 0xff), frame(301, 19)];
    for line in [LineCoding::Direct, LineCoding::Nrzi] {
        for scramble in [Scrambler::None, Scrambler::G3ruh] {
            let mut c = config();
            c.line_coding = line;
            c.scrambler = scramble;
            let (bits, coordinates) = levels(&frames, &c);
            let soft: Vec<_> = bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
            let decoded = spans(&soft, 0., &c);
            assert_eq!(decoded.len(), frames.len());
            for ((span, expected), bounds) in decoded.iter().zip(&frames).zip(&coordinates) {
                assert_eq!(&span.frame, expected);
                assert_eq!((span.start, span.end), *bounds);
            }
        }
    }
}

#[test]
fn negative_crc_truncation_structure_and_random_controls() {
    for scramble in [Scrambler::None, Scrambler::G3ruh] {
        let mut c = config();
        c.scrambler = scramble;
        let mut bad = frame(45, 9);
        *bad.last_mut().unwrap() ^= 128;
        let (bits, _) = levels(&[bad], &c);
        let soft: Vec<_> = bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
        assert!(spans(&soft, 0., &c).is_empty());
        let (bits, bounds) = levels(&[frame(40, 91)], &c);
        let soft: Vec<_> = bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
        assert!(spans(&soft[..bounds[0].1 + 7], 0., &c).is_empty());
        assert!(spans(&soft[bounds[0].0 + 1..], 0., &c).is_empty());
    }
    let c = config();
    let mut random = 4711_u64;
    let soft: Vec<_> = (0..200000)
        .map(|_| {
            random ^= random << 13;
            random ^= random >> 7;
            random ^= random << 17;
            if random & 1 == 1 { 1. } else { -1. }
        })
        .collect();
    assert!(spans(&soft, 0., &c).is_empty());
    let mut invalid = vec![0xff; 100];
    invalid.extend(protocol::crc16_x25(&invalid).to_le_bytes());
    let (bits, _) = levels(&[invalid], &c);
    let soft: Vec<_> = bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
    assert!(spans(&soft, 0., &c).is_empty());
}

#[test]
fn iq_fsk_roundtrip_exact_workers_and_baseline_union() {
    for scramble in [Scrambler::None, Scrambler::G3ruh] {
        let mut c = config();
        c.scrambler = scramble;
        let expected = vec![frame(32, 1), frame(113, 29)];
        let (bits, _) = levels(&expected, &c);
        let iq = modulate(&bits, "fsk", 8);
        let serial = decode(&iq, 9600, &c, &"a".repeat(64)).unwrap();
        assert_eq!(
            serial.frames.len(),
            2,
            "scrambler={scramble:?}, baseline={:?}, frames={:?}",
            serial.baseline_frames,
            serial.frames
        );
        assert!(
            serial
                .frames
                .iter()
                .all(|f| expected.iter().any(|x| hex::encode(x) == f.hex))
        );
        c.workers = 4;
        let parallel = decode(&iq, 9600, &c, &"a".repeat(64)).unwrap();
        assert_eq!(
            serde_json::to_value(&serial).unwrap(),
            serde_json::to_value(&parallel).unwrap()
        );
        c.sequence = None;
        let baseline = decode(&iq, 9600, &c, &"a".repeat(64)).unwrap();
        assert_eq!(serial.baseline_frames, baseline.baseline_frames);
        assert!(
            baseline
                .frames
                .iter()
                .all(|f| serial.frames.iter().any(|x| x.hex == f.hex))
        );
    }
}

#[test]
fn iq_bpsk_qpsk_oqpsk_variable_frame_roundtrip() {
    for mode in ["bpsk", "qpsk", "oqpsk"] {
        let mut c = config();
        c.waveform.demodulator_id = format!("iq_{mode}");
        c.waveform.dsp.mode = mode.into();
        c.waveform.cutoff_hz = None;
        c.waveform.dsp.phase_bins = 4;
        c.waveform.psk = Some(crate::psk::PskConfig {
            loop_bandwidth: 0.,
            max_carrier_offset_hz: Some(100.),
            ..Default::default()
        });
        let expected = vec![frame(42, 11), frame(71, 91)];
        let (bits, _) = levels(&expected, &c);
        let iq = modulate(&bits, mode, 8);
        let report = decode(&iq, 9600, &c, &"b".repeat(64)).unwrap();
        assert_eq!(report.frames.len(), 2, "{mode}");
        assert!(
            report
                .frames
                .iter()
                .all(|f| expected.iter().any(|x| hex::encode(x) == f.hex))
        );
    }
}

#[test]
fn existing_fsk_warmup_boundary_is_not_claimed_as_observed_payload() {
    let mut c = config();
    c.sequence = None;
    let expected = vec![frame(32, 1), frame(113, 29)];
    let (bits, _) = levels(&expected, &c);
    // Move the first transmission back to symbol 128, before the existing
    // frontend's 512-symbol DC warm-up. The second remains fully observable.
    let iq = modulate(&bits[512..], "fsk", 8);
    let report = decode(&iq, 9600, &c, &"d".repeat(64)).unwrap();
    assert_eq!(report.baseline_frames, vec![hex::encode(&expected[1])]);
    assert!(report.added_frames.is_empty());
}

#[test]
fn iq_bad_crc_truncation_and_noise_do_not_produce_telemetry() {
    let mut c = config();
    c.scrambler = Scrambler::G3ruh;
    let mut damaged = frame(110, 19);
    *damaged.last_mut().unwrap() ^= 128;
    let (bits, _) = levels(&[damaged], &c);
    let iq = modulate(&bits, "fsk", 8);
    assert!(
        decode(&iq, 9600, &c, &"e".repeat(64))
            .unwrap()
            .frames
            .is_empty()
    );
    let (bits, bounds) = levels(&[frame(110, 19)], &c);
    let iq = modulate(&bits[..bounds[0].1 + 7], "fsk", 8);
    assert!(
        decode(&iq, 9600, &c, &"f".repeat(64))
            .unwrap()
            .frames
            .is_empty()
    );
    let mut state = 129819_u64;
    let mut uniform = || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        (state >> 11) as f64 / (1_u64 << 53) as f64 - 0.5
    };
    let iq: Vec<_> = (0..32768)
        .map(|_| Complex64::new(uniform(), uniform()))
        .collect();
    assert!(
        decode(&iq, 9600, &c, &"0".repeat(64))
            .unwrap()
            .frames
            .is_empty()
    );
}

#[test]
fn independent_anchor_complete_bcjr_recovers_corrupted_target_decision() {
    let c = config();
    let expected = vec![frame(170, 37), frame(91, 83)];
    let (bits, bounds) = levels(&expected, &c);
    let x: Vec<_> = bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
    let mut soft: Vec<_> = (0..x.len())
        .map(|i| 0.32 * x[i.saturating_sub(1)] + x[i] + 0.32 * x[(i + 1).min(x.len() - 1)])
        .collect();
    // A local sign error with substantial independent neighboring observations.
    let target = (bounds[1].0 + 30..bounds[1].1 - 30)
        .find(|i| bits[*i - 1] != bits[*i] && bits[*i + 1] != bits[*i])
        .unwrap();
    soft[target] = -0.015 * x[target];
    let baseline = spans(&soft, 0., &c);
    assert_eq!(baseline.len(), 1);
    assert_eq!(baseline[0].frame, expected[0]);
    let stream = Stream {
        soft,
        threshold: 0.,
        lane: "independent-synthetic-channel".into(),
    };
    let result = sequence_stream(&stream, &baseline, &c, 0);
    assert!(
        result.supplemental.iter().any(|s| s.frame == expected[1]),
        "{:?}",
        result.receipt
    );
    assert!(!result.supplemental.iter().any(|s| s.frame == expected[0]));
    let receipt = result.receipt.unwrap();
    assert_eq!(receipt.training_spans, vec![bounds[0]]);
    assert_eq!(receipt.channels.len(), 1);
    for (a, b) in receipt.channels[0].taps.iter().zip([0.32, 1., 0.32]) {
        assert!((*a - b).abs() < 1e-4);
    }
}

#[test]
fn profile_bounds_and_budget_are_fail_closed() {
    let mut c = config();
    assert!(c.validate(9600, 8192).is_ok());
    c.workers = 0;
    assert!(c.validate(9600, 8192).is_err());
    c.workers = 1;
    c.sequence.as_mut().unwrap().maximum_training_frames = 33;
    assert!(c.validate(9600, 8192).is_err());
    c.sequence = None;
    c.work_budget = 1;
    assert!(
        decode(
            &vec![Complex64::new(1., 0.); 8192],
            9600,
            &c,
            &"c".repeat(64)
        )
        .unwrap_err()
        .contains("budget")
    );
    c = config();
    c.waveform.psk = Some(Default::default());
    assert!(c.validate(9600, 8192).is_err());
}
