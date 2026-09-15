//! Event-local yield accounting; input trust is never upgraded here.

use super::nonempty;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct FrameRecord {
    pub transmission_event_id: String,
    pub payload_sha256: String,
    pub crc_valid: bool,
}

pub fn unique_crc_frame_keys(frames: &[FrameRecord]) -> Result<BTreeSet<(String, String)>, String> {
    let mut keys = BTreeSet::new();
    for frame in frames {
        nonempty("transmission_event_id", &frame.transmission_event_id)?;
        nonempty("payload_sha256", &frame.payload_sha256)?;
        if frame.crc_valid {
            keys.insert((
                frame.transmission_event_id.clone(),
                frame.payload_sha256.clone(),
            ));
        }
    }
    Ok(keys)
}

pub fn unique_crc_frames(frames: &[FrameRecord]) -> Result<usize, String> {
    Ok(unique_crc_frame_keys(frames)?.len())
}

fn rate(count: u64, exposure: f64, name: &str) -> Result<f64, String> {
    if !exposure.is_finite() || exposure <= 0.0 {
        return Err(format!("{name} must be finite and greater than zero"));
    }
    let result = count as f64 / exposure;
    if !result.is_finite() {
        return Err("rate exceeds finite numeric range".into());
    }
    Ok(result)
}

pub fn false_accepts_per_hour(count: u64, hours: f64) -> Result<f64, String> {
    rate(count, hours, "total_hours_negative")
}

pub fn frames_per_cpu_second(count: u64, cpu_seconds: f64) -> Result<f64, String> {
    rate(count, cpu_seconds, "cpu_seconds")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn repeated_decodes_do_not_inflate_event_local_yield() {
        let frames: Vec<FrameRecord> = serde_json::from_value(json!([
            {"transmission_event_id":"pass-1","payload_sha256":"hash-a","crc_valid":true},
            {"transmission_event_id":"pass-1","payload_sha256":"hash-a","crc_valid":true},
            {"transmission_event_id":"pass-1","payload_sha256":"hash-b","crc_valid":true},
            {"transmission_event_id":"pass-2","payload_sha256":"hash-a","crc_valid":true},
            {"transmission_event_id":"pass-2","payload_sha256":"hash-c","crc_valid":false}
        ]))
        .unwrap();
        assert_eq!(unique_crc_frames(&frames).unwrap(), 3);
        assert_eq!(unique_crc_frames(&[]).unwrap(), 0);
    }

    #[test]
    fn rates_require_positive_finite_exposure() {
        assert_eq!(false_accepts_per_hour(3, 1.5).unwrap(), 2.0);
        assert_eq!(frames_per_cpu_second(10, 2.5).unwrap(), 4.0);
        assert_eq!(false_accepts_per_hour(0, 2.0).unwrap(), 0.0);
        for exposure in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(false_accepts_per_hour(1, exposure).is_err());
            assert!(frames_per_cpu_second(1, exposure).is_err());
        }
        assert!(false_accepts_per_hour(u64::MAX, f64::MIN_POSITIVE).is_err());
    }

    #[test]
    fn frame_validation_is_not_bypassed_by_false_crc() {
        let frame = FrameRecord {
            transmission_event_id: " ".into(),
            payload_sha256: "hash".into(),
            crc_valid: false,
        };
        assert!(unique_crc_frames(&[frame]).is_err());
        assert!(
            serde_json::from_value::<FrameRecord>(
                json!({"transmission_event_id":"a","payload_sha256":"b","crc_valid":1})
            )
            .is_err()
        );
    }
}
