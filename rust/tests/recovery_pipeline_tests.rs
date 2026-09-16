use crate::advanced_iq;
#[path = "../../examples/support/recovery_pipeline_fixture.rs"]
mod fixture;
use fixture::{LENGTH, RATE, base};
const SOURCE: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

#[test]
fn recovery_pipeline_independent_fec_iq_matrix_decodes_clean_frames() {
    let mut failed = Vec::new();
    for (mode, family) in fixture::matrix() {
        let c = fixture::config(mode, family, 1, false);
        let bytes = base::frame(17);
        let mut iq = base::Noise(0x20260917).fill(LENGTH, 0.001);
        fixture::add(&mut iq, &c, &bytes, 0.);
        let report = advanced_iq::decode(&iq, RATE, &c, SOURCE).unwrap();
        let actual = report
            .frames
            .iter()
            .map(|f| f.frame_hex.clone())
            .collect::<Vec<_>>();
        if actual != vec![hex::encode(&bytes)] {
            failed.push(format!(
                "{mode}/{family}: frames={actual:?}, candidates={}",
                report.candidates
            ));
        }
    }
    assert!(failed.is_empty(), "clean IQ failures: {failed:?}");
}

#[test]
fn recovery_pipeline_coherent_lane_keeps_baseline_and_worker_reports_are_identical() {
    for (mode, family) in fixture::matrix()
        .into_iter()
        .filter(|(mode, _)| fixture::is_cpm(mode))
    {
        let bytes = base::frame(17);
        let mut iq = base::Noise(0x2037).fill(LENGTH, 0.001);
        let baseline = fixture::config(mode, family, 1, false);
        fixture::add(&mut iq, &baseline, &bytes, 0.);
        let before = advanced_iq::decode(&iq, RATE, &baseline, SOURCE).unwrap();
        let serial =
            advanced_iq::decode(&iq, RATE, &fixture::config(mode, family, 1, true), SOURCE)
                .unwrap();
        let parallel =
            advanced_iq::decode(&iq, RATE, &fixture::config(mode, family, 4, true), SOURCE)
                .unwrap();
        assert_eq!(
            serde_json::to_value(&serial).unwrap(),
            serde_json::to_value(&parallel).unwrap(),
            "worker mismatch {mode}/{family}"
        );
        assert!(
            before
                .frames
                .iter()
                .all(|a| serial.frames.iter().any(|b| a.frame_hex == b.frame_hex)),
            "baseline regression {mode}/{family}"
        );
        assert!(
            serial
                .frames
                .iter()
                .any(|f| f.provenance.iter().any(|p| p.lane == "coherent_cpm")),
            "coherent lane failed {mode}/{family}: {:?}",
            serial.coherent
        );
    }
}

#[test]
fn recovery_pipeline_wrong_crc_and_noise_never_become_validated_frames() {
    for (mode, family) in fixture::matrix() {
        let c = fixture::config(mode, family, 2, fixture::is_cpm(mode));
        let mut invalid = base::frame(17);
        invalid[7] ^= 1;
        let mut iq = base::Noise(0x1812).fill(LENGTH, 0.001);
        fixture::add(&mut iq, &c, &invalid, 0.);
        for (label, input) in [
            ("wrong_crc", iq),
            ("noise", base::Noise(0x1248).fill(LENGTH, 1.)),
        ] {
            let report = advanced_iq::decode(&input, RATE, &c, SOURCE).unwrap();
            assert!(
                report.frames.is_empty(),
                "{mode}/{family}/{label}: {report:?}"
            );
        }
    }
}

#[test]
fn recovery_pipeline_frozen_last_crc_bit_negatives_remain_rejected() {
    use num_complex::Complex64;
    use sha2::{Digest, Sha256};
    let mut rng = base::Noise(0x2026_0917_1375);
    let mut checked = 0;
    for (mode, family) in fixture::matrix() {
        for group in ["clean", "wrong_crc", "noise"] {
            let mut iq = rng.fill(LENGTH, if group == "noise" { 1. } else { 0.001 });
            if family != "uncoded" || group != "wrong_crc" || !matches!(mode, "bpsk" | "gmsk") {
                continue;
            }
            let c = fixture::config(mode, family, 1, fixture::is_cpm(mode));
            let mut frame = base::frame(17);
            frame[7] ^= 1;
            fixture::add(&mut iq, &c, &frame, 0.);
            let raw: Vec<_> = iq
                .iter()
                .flat_map(|z| {
                    (z.re as f32)
                        .to_le_bytes()
                        .into_iter()
                        .chain((z.im as f32).to_le_bytes())
                })
                .collect();
            let hash = hex::encode(Sha256::digest(&raw));
            assert_eq!(
                hash,
                if mode == "bpsk" {
                    "f9e5655623fa9626bf63036a8780513b09cbff9ad23120169368d4b6307c3611"
                } else {
                    "b37f790aca730c024e74249d703dee99220c1a5305b59ed347072b4a12f8a9fd"
                }
            );
            let samples: Vec<_> = raw
                .as_chunks::<8>()
                .0
                .iter()
                .map(|v| {
                    Complex64::new(
                        f32::from_le_bytes(v[..4].try_into().unwrap()) as f64,
                        f32::from_le_bytes(v[4..].try_into().unwrap()) as f64,
                    )
                })
                .collect();
            let report = advanced_iq::decode(&samples, RATE, &c, &hash).unwrap();
            assert!(
                report.frames.is_empty(),
                "frozen {mode} malformedCRC accepted: {report:?}"
            );
            checked += 1;
        }
    }
    assert_eq!(checked, 2);
}
