//! Cross-fragment and cross-station soft combining for one independently
//! identified immutable codeword. This module consumes aligned coded-bit LLRs;
//! it does not infer that unrelated bursts carry the same payload.

use crate::{coded::FrameValidator, recovery_code, repetition};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

fn default_maximum_copies() -> usize {
    8
}
fn default_minimum_correlation() -> f64 {
    0.2
}
fn default_minimum_distinct_sources() -> usize {
    1
}
fn default_minimum_distinct_stations() -> usize {
    1
}
fn default_maximum_work() -> u64 {
    100_000_000_000
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Copy {
    pub source_sha256: String,
    pub observation_id: String,
    pub station_id: String,
    pub start_sample: usize,
    pub end_sample: usize,
    /// Existing mission-protected repeat identity, not a receiver-made label.
    pub header: Vec<u8>,
    /// One finite log-likelihood ratio per aligned coded bit; positive means 1.
    pub llr: Vec<f64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    pub minimum_correlation: f64,
    pub maximum_copies: usize,
    pub minimum_distinct_sources: usize,
    pub minimum_distinct_stations: usize,
    pub maximum_work: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub header_layout: Option<repetition::HeaderLayout>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub soft_list: Option<recovery_code::ListConfig>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            minimum_correlation: default_minimum_correlation(),
            maximum_copies: default_maximum_copies(),
            minimum_distinct_sources: default_minimum_distinct_sources(),
            minimum_distinct_stations: default_minimum_distinct_stations(),
            maximum_work: default_maximum_work(),
            header_layout: None,
            soft_list: None,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    #[serde(default)]
    pub config: Config,
    pub code: recovery_code::CodeProfile,
    pub validator: FrameValidator,
    pub copies: Vec<Copy>,
}

#[derive(Clone, Debug, Serialize)]
pub struct Contribution {
    pub source_sha256: String,
    pub observation_id: String,
    pub station_id: String,
    pub start_sample: usize,
    pub end_sample: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct Report {
    pub schema: &'static str,
    pub status: &'static str,
    pub accepted_frame_hex: Option<String>,
    pub validation_layers: Vec<String>,
    pub copies: usize,
    pub distinct_sources: usize,
    pub distinct_stations: usize,
    pub contributions: Vec<Contribution>,
    pub list_hypotheses: usize,
    pub accepted_hypothesis: Option<usize>,
    pub work_reserved: u64,
    pub experimental: bool,
}

impl Config {
    fn fec_work(&self, code: &recovery_code::CodeProfile) -> Result<u64, String> {
        if !(2..=16).contains(&self.maximum_copies)
            || !(1..=self.maximum_copies).contains(&self.minimum_distinct_sources)
            || !(1..=self.maximum_copies).contains(&self.minimum_distinct_stations)
            || !self.minimum_correlation.is_finite()
            || !(0.1..=0.99).contains(&self.minimum_correlation)
            || self.maximum_work == 0
        {
            return Err("invalid soft-combine copy, diversity, correlation or work limits".into());
        }
        if let Some(layout) = &self.header_layout {
            layout.validate()?;
        }
        code.validate()?;
        let work = if let Some(list) = &self.soft_list {
            list.validate(code)?;
            code.work_per_pass()?
                .checked_add(list.work_per_call(code)?)
                .ok_or("soft-combine work overflow")?
        } else {
            code.work_per_pass()?
        };
        Ok(work)
    }
}

pub fn decode(plan: &Plan) -> Result<Report, String> {
    let fec_work = plan.config.fec_work(&plan.code)?;
    plan.validator.validate()?;
    if !(2..=plan.config.maximum_copies).contains(&plan.copies.len()) {
        return Err("soft-combine input must contain 2..maximum_copies copies".into());
    }
    let expected_bits = plan.code.encoded_bits()?;
    let copies = plan.copies.len() as u64;
    let pairs = copies
        .checked_mul(copies.saturating_sub(1))
        .and_then(|value| value.checked_div(2))
        .ok_or("soft-combine work overflow")?;
    // Each pair computes a dot product and two squared norms. The final pass
    // adds every contribution. This is a conservative arithmetic work proxy.
    let combine_work = (expected_bits as u64)
        .checked_mul(
            pairs
                .checked_mul(3)
                .and_then(|value| value.checked_add(copies))
                .ok_or("soft-combine work overflow")?,
        )
        .ok_or("soft-combine work overflow")?;
    let work = fec_work
        .checked_add(combine_work)
        .ok_or("soft-combine work overflow")?;
    if work > plan.config.maximum_work {
        return Err("soft-combine worst-case work exceeds its explicit budget".into());
    }
    let mut observations = BTreeSet::new();
    let mut stations = BTreeSet::new();
    let mut identities = BTreeSet::new();
    let mut copies = Vec::with_capacity(plan.copies.len());
    for copy in &plan.copies {
        if copy.observation_id.is_empty()
            || copy.observation_id.len() > 128
            || copy.station_id.is_empty()
            || copy.station_id.len() > 128
            || copy.llr.len() != expected_bits
            || !identities.insert((
                copy.source_sha256.clone(),
                copy.start_sample,
                copy.end_sample,
            ))
        {
            return Err("invalid or duplicate soft-combine contribution identity".into());
        }
        observations.insert(copy.source_sha256.clone());
        stations.insert(copy.station_id.clone());
        copies.push(repetition::Copy {
            source_sha256: copy.source_sha256.clone(),
            start_sample: copy.start_sample,
            end_sample: copy.end_sample,
            header: copy.header.clone(),
            llr: copy.llr.clone(),
        });
    }
    if observations.len() < plan.config.minimum_distinct_sources
        || stations.len() < plan.config.minimum_distinct_stations
    {
        return Err("soft-combine diversity requirement is not met".into());
    }
    let combined = repetition::combine_with_layout(
        &copies,
        plan.config.minimum_correlation,
        plan.config.header_layout.as_ref(),
    )?;
    let output = if let Some(list) = &plan.config.soft_list {
        plan.code.decode_list(&combined, &plan.validator, list)?
    } else {
        plan.code.decode(&combined, &plan.validator)?
    };
    let (accepted_frame_hex, validation_layers, status) = if let Some(frame) = output.accepted {
        (
            Some(hex::encode(frame.bytes)),
            frame.validation_layers,
            "accepted",
        )
    } else {
        (None, Vec::new(), "rejected")
    };
    Ok(Report {
        schema: "framelift-soft-combine-v1",
        status,
        accepted_frame_hex,
        validation_layers,
        copies: plan.copies.len(),
        distinct_sources: observations.len(),
        distinct_stations: stations.len(),
        contributions: plan
            .copies
            .iter()
            .map(|copy| Contribution {
                source_sha256: copy.source_sha256.clone(),
                observation_id: copy.observation_id.clone(),
                station_id: copy.station_id.clone(),
                start_sample: copy.start_sample,
                end_sample: copy.end_sample,
            })
            .collect(),
        list_hypotheses: output.list_hypotheses,
        accepted_hypothesis: output.accepted_hypothesis,
        work_reserved: work,
        experimental: true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ax25() -> Vec<u8> {
        let mut frame = vec![
            0x82, 0xa0, 0xa4, 0xa6, 0x40, 0x40, 0x60, 0x9c, 0x60, 0x86, 0x82, 0x98, 0x98, 0x61,
            0x03, 0xf0, 1, 2, 3, 4,
        ];
        let fcs = crate::protocol::crc16_x25(&frame);
        frame.extend(fcs.to_le_bytes());
        frame
    }

    fn header() -> Vec<u8> {
        let mut value = vec![0, 0, 0, 7];
        value.extend(crate::space_link::csp_crc32c(&value).to_be_bytes());
        value
    }

    fn copy(source: u8, station: &str, llr: Vec<f64>) -> Copy {
        Copy {
            source_sha256: format!("{source:02x}").repeat(32),
            observation_id: source.to_string(),
            station_id: station.into(),
            start_sample: 0,
            end_sample: 100,
            header: header(),
            llr,
        }
    }

    #[test]
    fn two_stations_resolve_opposite_low_confidence_bit_errors() {
        let frame = ax25();
        let bits: Vec<u8> = frame
            .iter()
            .flat_map(|byte| (0..8).rev().map(move |bit| (byte >> bit) & 1))
            .collect();
        let mut a: Vec<f64> = bits
            .iter()
            .map(|&bit| if bit == 1 { 2.0 } else { -2.0 })
            .collect();
        let mut b = a.clone();
        a[17] = -0.4 * a[17].signum();
        b[93] = -0.4 * b[93].signum();
        let code = recovery_code::CodeProfile::Uncoded {
            frame_bytes: frame.len(),
        };
        for received in [&a, &b] {
            assert!(
                code.decode(received, &FrameValidator::Ax25)
                    .unwrap()
                    .accepted
                    .is_none()
            );
        }
        let mut plan = Plan {
            config: Config {
                minimum_distinct_sources: 2,
                minimum_distinct_stations: 2,
                ..Config::default()
            },
            code,
            validator: FrameValidator::Ax25,
            copies: vec![copy(1, "A", a), copy(2, "B", b)],
        };
        let report = decode(&plan).unwrap();
        assert_eq!(report.status, "accepted");
        assert_eq!(report.accepted_frame_hex, Some(hex::encode(frame)));
        assert_eq!((report.distinct_sources, report.distinct_stations), (2, 2));
        plan.config.maximum_work = 1;
        assert!(
            decode(&plan)
                .unwrap_err()
                .contains("worst-case work exceeds")
        );
    }

    #[test]
    fn same_source_overlap_and_fake_station_diversity_fail_closed() {
        let llr = vec![1.0; ax25().len() * 8];
        let a = copy(1, "A", llr.clone());
        let mut b = copy(1, "B", llr);
        b.observation_id = "other-label".into();
        let plan = Plan {
            config: Config {
                minimum_distinct_sources: 2,
                minimum_distinct_stations: 2,
                ..Config::default()
            },
            code: recovery_code::CodeProfile::Uncoded {
                frame_bytes: ax25().len(),
            },
            validator: FrameValidator::Ax25,
            copies: vec![a, b],
        };
        assert!(decode(&plan).is_err());
    }
}
