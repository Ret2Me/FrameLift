//! Conservative grouping: time overlap alone is not physical evidence.

use chrono::{DateTime, Duration, FixedOffset};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventCandidate {
    pub observation_id: String,
    pub norad_id: u64,
    pub transmitter_uuid: String,
    pub start_utc: DateTime<FixedOffset>,
    pub end_utc: DateTime<FixedOffset>,
    pub station_id: String,
    #[serde(default)]
    pub payload_hashes: BTreeSet<String>,
}

impl EventCandidate {
    pub fn validate(&self) -> Result<(), String> {
        if self.start_utc.offset().local_minus_utc() != 0
            || self.end_utc.offset().local_minus_utc() != 0
        {
            return Err("event timestamps must use UTC offset +00:00".into());
        }
        if self.end_utc <= self.start_utc {
            return Err("end_utc must be after start_utc".into());
        }
        super::nonempty("observation_id", &self.observation_id)?;
        super::nonempty("transmitter_uuid", &self.transmitter_uuid)?;
        super::nonempty("station_id", &self.station_id)?;
        if self.norad_id == 0 {
            return Err("norad_id must be positive".into());
        }
        Ok(())
    }
}

pub fn same_transmission_event(
    left: &EventCandidate,
    right: &EventCandidate,
    time_tolerance: Duration,
    doppler_tracks_consistent: bool,
) -> Result<bool, String> {
    left.validate()?;
    right.validate()?;
    if time_tolerance < Duration::zero() {
        return Err("time tolerance must not be negative".into());
    }
    if left.norad_id != right.norad_id || left.transmitter_uuid != right.transmitter_uuid {
        return Ok(false);
    }
    // Subtraction avoids overflow when a valid timestamp is close to MAX_UTC.
    let overlaps = left.start_utc.signed_duration_since(right.end_utc) <= time_tolerance
        && right.start_utc.signed_duration_since(left.end_utc) <= time_tolerance;
    Ok(overlaps
        && (doppler_tracks_consistent || !left.payload_hashes.is_disjoint(&right.payload_hashes)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event() -> EventCandidate {
        EventCandidate {
            observation_id: "1".into(),
            norad_id: 123,
            transmitter_uuid: "tx".into(),
            start_utc: "2026-01-01T00:00:00Z".parse().unwrap(),
            end_utc: "2026-01-01T00:05:00Z".parse().unwrap(),
            station_id: "a".into(),
            payload_hashes: BTreeSet::new(),
        }
    }

    #[test]
    fn temporal_overlap_requires_independent_evidence() {
        let mut a = event();
        let mut b = event();
        assert!(!same_transmission_event(&a, &b, Duration::seconds(30), false).unwrap());
        assert!(same_transmission_event(&a, &b, Duration::seconds(30), true).unwrap());
        a.payload_hashes.insert("abc".into());
        b.payload_hashes.insert("abc".into());
        assert!(same_transmission_event(&a, &b, Duration::seconds(30), false).unwrap());
        b.transmitter_uuid = "other".into();
        assert!(!same_transmission_event(&a, &b, Duration::seconds(30), true).unwrap());
    }

    #[test]
    fn tolerance_boundary_and_invalid_utc_are_explicit() {
        let a = event();
        let mut b = event();
        b.start_utc = a.end_utc + Duration::seconds(30);
        b.end_utc = b.start_utc + Duration::minutes(1);
        assert!(same_transmission_event(&a, &b, Duration::seconds(30), true).unwrap());
        assert!(!same_transmission_event(&a, &b, Duration::seconds(29), true).unwrap());
        assert!(same_transmission_event(&a, &b, Duration::seconds(-1), true).is_err());
        b.start_utc = "2026-01-01T00:00:00+01:00".parse().unwrap();
        assert!(b.validate().is_err());
        b = a.clone();
        b.end_utc = b.start_utc;
        assert!(b.validate().is_err());
    }
}
