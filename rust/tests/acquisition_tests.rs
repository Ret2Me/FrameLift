use super::*;
use crate::advanced_iq::{self, RecoveryOptions};
#[path = "../../examples/support/recovery_pipeline_fixture.rs"]
#[allow(dead_code)]
mod fixture;

const SOURCE: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn config() -> Config {
    Config {
        minimum_correlation: 0.70,
        maximum_hard_errors: 12,
        maximum_candidates: 32,
        maximum_work: 10_000_000,
    }
}

fn marker() -> Vec<u8> {
    fixture::base::config().syncword
}

#[test]
fn centered_gate_preserves_gain_offset_and_unresolved_bpsk_phase() {
    let c = config();
    let bits = marker();
    let raw: Vec<_> = bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
    for (gain, offset) in [(0.01, 7.), (2., -13.), (80., 0.)] {
        let samples: Vec<_> = raw.iter().map(|x| x * gain + offset).collect();
        let mut search = Search::new(&c, &bits, 0).unwrap();
        let score = search.real(&samples, 0).unwrap().unwrap();
        assert!((score.normalized_correlation - 1.).abs() < 1e-12);
        let samples: Vec<_> = samples
            .iter()
            .map(|x| Complex64::from_polar(*x, 1.37))
            .collect();
        let score = search.complex(&samples, 0).unwrap().unwrap();
        assert!((score.normalized_correlation - 1.).abs() < 1e-12);
    }
}

#[test]
fn gate_rejects_constant_wrong_polarity_noise_and_invalid_limits() {
    let c = config();
    let bits = marker();
    let mut search = Search::new(&c, &bits, 0).unwrap();
    assert!(search.real(&vec![7.; bits.len()], 0).unwrap().is_none());
    let reversed: Vec<_> = bits.iter().map(|b| 1. - 2. * f64::from(*b)).collect();
    assert!(search.real(&reversed, 0).unwrap().is_none());
    let samples = fixture::base::Noise(17).fill(bits.len(), 1.);
    assert!(search.complex(&samples, 0).unwrap().is_none());
    assert!(search.real(&[0.; 4], 0).is_err());
    for threshold in [f64::NAN, 0., 1.01] {
        let mut invalid = config();
        invalid.minimum_correlation = threshold;
        assert!(Search::new(&invalid, &bits, 0).is_err());
    }
    assert!(Search::new(&c, &[0; 64], 0).is_err());
}

#[test]
fn gate_limits_fail_explicitly_instead_of_pruning() {
    let mut c = config();
    c.maximum_work = 1;
    let bits = marker();
    let values: Vec<_> = bits.iter().map(|b| 2. * f64::from(*b) - 1.).collect();
    assert!(
        Search::new(&c, &bits, 0)
            .unwrap()
            .real(&values, 0)
            .unwrap_err()
            .contains("budget")
    );
    let mut c = config();
    c.maximum_candidates = 1;
    let mut search = Search::new(&c, &bits, 0).unwrap();
    let score = search.real(&values, 0).unwrap().unwrap();
    search.select(0., 0., score.clone()).unwrap();
    assert!(
        search
            .select(1000., 0., score)
            .unwrap_err()
            .contains("budget")
    );
}

fn enabled(mut c: advanced_iq::Config) -> advanced_iq::Config {
    c.recovery
        .get_or_insert_with(RecoveryOptions::default)
        .soft_acquisition = Some(config());
    c
}

fn corrupted_marker(c: &advanced_iq::Config) -> advanced_iq::Config {
    let mut tx = c.clone();
    for index in [4, 11, 19, 28, 39, 51] {
        tx.syncword[index] ^= 1;
    }
    tx
}

#[test]
fn physical_iq_corrupted_marker_is_additive_and_workers_are_identical() {
    for mode in fixture::MODES {
        let c = fixture::config(mode, "ldpc", 1, false);
        let frame = fixture::base::frame(17);
        let mut iq = fixture::base::Noise(9171).fill(fixture::LENGTH, 0.001);
        fixture::add(&mut iq, &corrupted_marker(&c), &frame, 0.);
        let hard = advanced_iq::decode(&iq, fixture::RATE, &c, SOURCE).unwrap();
        assert!(
            hard.frames.is_empty(),
            "hard marker must reject the fixed corruption: {mode} {hard:?}"
        );
        let mut soft = enabled(c);
        let actual = advanced_iq::decode(&iq, fixture::RATE, &soft, SOURCE).unwrap();
        assert_eq!(actual.frames.len(), 1, "{mode}: {actual:?}");
        assert_eq!(actual.frames[0].frame_hex, hex::encode(&frame));
        assert!(actual.baseline_frames.is_empty());
        assert_eq!(actual.added_frames, vec![hex::encode(frame)]);
        assert!(
            actual.frames[0]
                .provenance
                .iter()
                .all(|p| p.lane.starts_with("soft_acquisition/"))
        );
        assert!(!actual.soft_acquisition[0].selected.is_empty());
        soft.recovery.as_mut().unwrap().workers = 4;
        let parallel = advanced_iq::decode(&iq, fixture::RATE, &soft, SOURCE).unwrap();
        assert_eq!(
            serde_json::to_value(actual).unwrap(),
            serde_json::to_value(parallel).unwrap()
        );
    }
}

#[test]
fn physical_iq_soft_candidates_do_not_accept_bad_crc_or_noise() {
    for mode in fixture::MODES {
        let c = enabled(fixture::config(mode, "ldpc", 1, false));
        let mut frame = fixture::base::frame(17);
        frame[7] ^= 1;
        let mut iq = fixture::base::Noise(9171).fill(fixture::LENGTH, 0.001);
        fixture::add(&mut iq, &corrupted_marker(&c), &frame, 0.);
        let report = advanced_iq::decode(&iq, fixture::RATE, &c, SOURCE).unwrap();
        assert!(report.frames.is_empty(), "{mode}: {report:?}");
        for seed in 0..4 {
            let noise = fixture::base::Noise(9171 + seed).fill(fixture::LENGTH, 1.);
            let report = advanced_iq::decode(&noise, fixture::RATE, &c, SOURCE).unwrap();
            assert!(report.frames.is_empty(), "{mode}, seed {seed}: {report:?}");
        }
    }
}

#[test]
fn legacy_bpsk_soft_acquisition_rejects_bad_crc_and_noise() {
    let mut c = fixture::base::config();
    c.joint = None;
    let mut frame = fixture::base::frame(23);
    let mut iq = fixture::base::Noise(711).fill(2048, 0.001);
    fixture::base::add(
        &mut iq,
        &fixture::base::symbols(&corrupted_marker(&c), &frame, None),
        128,
        1.,
        0.,
    );
    assert!(
        advanced_iq::decode(&iq, 4000, &c, SOURCE)
            .unwrap()
            .frames
            .is_empty()
    );
    let c = enabled(c);
    let result = advanced_iq::decode(&iq, 4000, &c, SOURCE).unwrap();
    assert_eq!(result.frames.len(), 1, "{result:?}");
    frame[7] ^= 1;
    let mut wrong = fixture::base::Noise(711).fill(2048, 0.001);
    fixture::base::add(
        &mut wrong,
        &fixture::base::symbols(&corrupted_marker(&c), &frame, None),
        128,
        1.,
        0.,
    );
    assert!(
        advanced_iq::decode(&wrong, 4000, &c, SOURCE)
            .unwrap()
            .frames
            .is_empty()
    );
    for seed in 0..8 {
        let noise = fixture::base::Noise(711 + seed).fill(2048, 1.);
        assert!(
            advanced_iq::decode(&noise, 4000, &c, SOURCE)
                .unwrap()
                .frames
                .is_empty()
        );
    }
}

#[test]
fn hard_recovery_is_preserved_and_disabled_serialization_is_unchanged() {
    let mut c = fixture::config("gmsk", "ldpc", 1, false);
    let frame = fixture::base::frame(3);
    let mut iq = fixture::base::Noise(111).fill(fixture::LENGTH, 0.001);
    fixture::add(&mut iq, &c, &frame, 0.);
    let serialized = serde_json::to_value(&c).unwrap();
    assert!(serialized["recovery"].get("soft_acquisition").is_none());
    let roundtrip: advanced_iq::Config = serde_json::from_value(serialized).unwrap();
    let before = advanced_iq::decode(&iq, fixture::RATE, &c, SOURCE).unwrap();
    let roundtrip = advanced_iq::decode(&iq, fixture::RATE, &roundtrip, SOURCE).unwrap();
    let before_value = serde_json::to_value(&before).unwrap();
    assert_eq!(before_value, serde_json::to_value(roundtrip).unwrap());
    assert!(before_value.get("soft_acquisition").is_none());
    c = enabled(c);
    let after = advanced_iq::decode(&iq, fixture::RATE, &c, SOURCE).unwrap();
    assert_eq!(before.baseline_frames, after.baseline_frames);
    assert_eq!(before.frames[0].frame_hex, after.frames[0].frame_hex);
    let mut aggregate_exhausted = c.clone();
    aggregate_exhausted.maximum_work = 1;
    assert_eq!(
        advanced_iq::decode(&iq, fixture::RATE, &aggregate_exhausted, SOURCE).unwrap_err(),
        "soft acquisition work budget exceeded",
        "remaining aggregate budget must cap search before scanning, not afterward"
    );
    let mut exhausted = c;
    exhausted
        .recovery
        .as_mut()
        .unwrap()
        .soft_acquisition
        .as_mut()
        .unwrap()
        .maximum_work = 1;
    assert!(
        advanced_iq::decode(&iq, fixture::RATE, &exhausted, SOURCE)
            .unwrap_err()
            .contains("budget")
    );
}

#[test]
fn soft_candidates_preserve_baseline_cancellation_and_repeat_decisions() {
    let mut c = fixture::base::config();
    c.cancellation = Some(crate::interference::Config {
        rounds: 2,
        minimum_holdout_reduction: 0.2,
    });
    let mut iq = fixture::base::Noise(9921).fill(2048, 0.005);
    for (frame, start, amplitude) in [(7, 128, 3.), (19, 160, 1.)] {
        fixture::base::add(
            &mut iq,
            &fixture::base::symbols(&c, &fixture::base::frame(frame), None),
            start,
            amplitude,
            0.,
        );
    }
    let hard = advanced_iq::decode(&iq, 4000, &c, SOURCE).unwrap();
    let soft = advanced_iq::decode(&iq, 4000, &enabled(c), SOURCE).unwrap();
    assert_eq!(hard.frames.len(), 2);
    assert_eq!(hard.baseline_frames, soft.baseline_frames);
    assert_eq!(
        serde_json::to_value(&hard.cancellation).unwrap(),
        serde_json::to_value(&soft.cancellation).unwrap()
    );
    for frame in hard.frames {
        assert!(soft.frames.iter().any(|f| f.frame_hex == frame.frame_hex));
    }

    let mut c = fixture::base::config();
    fixture::waveform::repetition(&mut c);
    let mut iq = fixture::base::Noise(119).fill(8192, 0.0001);
    for copy in 0..4 {
        let mut symbols = fixture::base::symbols(&c, &fixture::base::frame(23), Some(23));
        for i in 16..128 {
            if (i - 16) % 4 != copy {
                symbols[128 + i] = 0.;
            }
        }
        fixture::base::add(&mut iq, &symbols, 128 + copy * 1536, 1., 0.);
    }
    let hard = advanced_iq::decode(&iq, 4000, &c, SOURCE).unwrap();
    let soft = advanced_iq::decode(&iq, 4000, &enabled(c), SOURCE).unwrap();
    assert!(hard.combined_groups > 0);
    assert_eq!(hard.combined_groups, soft.combined_groups);
    assert_eq!(hard.rejected_groups, soft.rejected_groups);
    assert_eq!(hard.baseline_frames, soft.baseline_frames);
    let repeats = |report: &advanced_iq::Report| {
        report
            .frames
            .iter()
            .flat_map(|frame| {
                frame
                    .provenance
                    .iter()
                    .filter(|p| p.lane == "independent_repeats")
                    .map(move |p| serde_json::json!({"frame":frame.frame_hex,"provenance":p}))
            })
            .collect::<Vec<_>>()
    };
    assert_eq!(repeats(&hard), repeats(&soft));
}
