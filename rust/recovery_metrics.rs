//! Paired recovery-study receipts. Accounting does not certify input provenance,
//! mission coverage, independence, or the integrity of an external decoder.
use crate::{archive::uncertainty, coded, protocol};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Validation {
    ReceivedAx25Fcs {
        frame_hex: String,
    },
    /// Complete frame, including received check bytes, is the comparison key.
    ReceivedFrameCheck {
        validator: coded::FrameValidator,
    },
    ExternalDecoderAttested {
        decoder: String,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Frame {
    /// AX.25 keys exclude FCS; other checked protocols retain the complete frame.
    pub payload_hex: String,
    pub validation: Validation,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Completed,
    Failed,
    Unsupported,
    TimedOut,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Arm {
    pub name: String,
    pub status: Status,
    pub input_sha256: String,
    pub runtime_sha256: String,
    pub profile_sha256: String,
    pub wall_seconds: f64,
    pub cpu_seconds: Option<f64>,
    pub error: Option<String>,
    pub frames: Vec<Frame>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Exposure {
    Development,
    HeldOut,
    Unknown,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Observation {
    pub id: String,
    pub mission: String,
    pub station: u64,
    pub pass_group: String,
    pub input_sha256: String,
    pub exposure: Exposure,
    pub confirmed_signal: Option<bool>,
    pub signal_evidence: Option<String>,
    pub negative_control: bool,
    pub duration_seconds: f64,
    pub arms: Vec<Arm>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Study {
    pub schema: String,
    pub baseline_arm: String,
    pub candidate_arms: Vec<String>,
    pub observations: Vec<Observation>,
}

fn text_ok(s: &str) -> bool {
    !s.trim().is_empty() && s.len() <= 2048
}
fn hash_ok(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn finite_duration(s: f64) -> bool {
    s.is_finite() && (0. ..=1e9).contains(&s)
}

impl Frame {
    fn key(&self) -> Result<String, String> {
        if self.payload_hex.is_empty() || self.payload_hex.len() > 131072 {
            return Err("empty or oversized frame key".into());
        }
        let payload = hex::decode(&self.payload_hex).map_err(|e| e.to_string())?;
        match &self.validation {
            Validation::ReceivedAx25Fcs { frame_hex } => {
                if frame_hex.len() != self.payload_hex.len() + 4 {
                    return Err("AX.25 payload/FCS length mismatch".into());
                }
                let frame = hex::decode(frame_hex).map_err(|e| e.to_string())?;
                if !protocol::valid_ax25_fcs(&frame)
                    || !protocol::valid_ax25_ui(&payload)
                    || frame[..frame.len() - 2] != payload
                {
                    return Err("received AX.25 integrity, structure or payload mismatch".into());
                }
            }
            Validation::ReceivedFrameCheck { validator } => {
                if matches!(validator, coded::FrameValidator::Ax25) {
                    return Err(
                        "AX.25 receipts must use received_ax25_fcs with an FCS-free key".into(),
                    );
                }
                validator.validate()?;
                validator.decode(&payload)?;
            }
            Validation::ExternalDecoderAttested { decoder } if !text_ok(decoder) => {
                return Err("external decoder attestation needs a name/version".into());
            }
            Validation::ExternalDecoderAttested { .. } => {}
        }
        Ok(hex::encode(payload))
    }
}

#[derive(Default)]
struct Aggregate {
    observations: usize,
    baseline: usize,
    candidate: usize,
    added: usize,
    lost: usize,
    added_bytes: usize,
    lost_bytes: usize,
    improved: usize,
    baseline_wall: f64,
    candidate_wall: f64,
    pairs: Vec<uncertainty::Pair>,
}
impl Aggregate {
    fn add(
        &mut self,
        o: &Observation,
        b: &BTreeSet<String>,
        c: &BTreeSet<String>,
        ba: &Arm,
        ca: &Arm,
    ) {
        self.observations += 1;
        self.baseline += b.len();
        self.candidate += c.len();
        let added: Vec<_> = c.difference(b).collect();
        let lost: Vec<_> = b.difference(c).collect();
        self.improved += usize::from(!added.is_empty());
        self.added += added.len();
        self.lost += lost.len();
        self.added_bytes += added.iter().map(|s| s.len() / 2).sum::<usize>();
        self.lost_bytes += lost.iter().map(|s| s.len() / 2).sum::<usize>();
        self.baseline_wall += ba.wall_seconds;
        self.candidate_wall += ca.wall_seconds;
        self.pairs.push(uncertainty::Pair {
            station: o.station,
            group: o.pass_group.clone(),
            reference: b.len(),
            candidate: c.len(),
        });
    }
    fn value(&self) -> Value {
        json!({"paired_observations":self.observations,"baseline_unique":self.baseline,"candidate_unique":self.candidate,
            "added_unique":self.added,"lost_unique":self.lost,"net_unique":self.added as i64 - self.lost as i64,
            "added_bytes":self.added_bytes,"lost_bytes":self.lost_bytes,"observations_with_additions":self.improved,
            "net_gain_percent":if self.baseline == 0 { None } else { Some(100. * (self.candidate as f64 - self.baseline as f64) / self.baseline as f64) },
            "baseline_wall_seconds":self.baseline_wall,"candidate_wall_seconds":self.candidate_wall,
            "descriptive_uncertainty":uncertainty::bootstrap(&self.pairs)})
    }
}

/// Input hashes bind receipts to a declared numeric input, but this function
/// does not reopen source files or verify that a process actually ran.
pub fn summarize(study: &Study) -> Result<Value, String> {
    let names: BTreeSet<_> = std::iter::once(&study.baseline_arm)
        .chain(&study.candidate_arms)
        .collect();
    if study.schema != "framelift-recovery-study-v1"
        || study.candidate_arms.is_empty()
        || study.candidate_arms.len() > 32
        || names.len() != study.candidate_arms.len() + 1
        || names.iter().any(|s| !text_ok(s))
        || study.observations.is_empty()
        || study.observations.len() > 65536
    {
        return Err("invalid study schema, arm names or observation count".into());
    }
    let mut ids = BTreeSet::new();
    let mut inputs = BTreeSet::new();
    let mut aggregates = BTreeMap::<(String, String), Aggregate>::new();
    let mut rows = Vec::new();
    let mut failures = Vec::new();
    let mut negative = BTreeMap::<String, (usize, usize, f64)>::new();
    let mut external_attested = 0usize;
    for o in &study.observations {
        if !text_ok(&o.id)
            || !text_ok(&o.mission)
            || !text_ok(&o.pass_group)
            || !ids.insert(&o.id)
            || !hash_ok(&o.input_sha256)
            || !inputs.insert(&o.input_sha256)
            || !finite_duration(o.duration_seconds)
            || o.duration_seconds < 1e-9
            || o.confirmed_signal.is_some()
                && !o.signal_evidence.as_ref().is_some_and(|s| text_ok(s))
            || o.negative_control && o.confirmed_signal == Some(true)
        {
            return Err(format!(
                "invalid/duplicate observation, input or signal evidence: {}",
                o.id
            ));
        }
        let mut arms = BTreeMap::new();
        let mut sets = BTreeMap::new();
        for a in &o.arms {
            if !names.contains(&a.name)
                || arms.insert(&a.name, a).is_some()
                || a.input_sha256 != o.input_sha256
                || !hash_ok(&a.runtime_sha256)
                || !hash_ok(&a.profile_sha256)
                || !finite_duration(a.wall_seconds)
                || a.cpu_seconds.is_some_and(|s| !finite_duration(s))
                || a.status != Status::Completed
                    && (!a.frames.is_empty() || !a.error.as_ref().is_some_and(|s| text_ok(s)))
                || a.status == Status::Completed && a.error.is_some()
            {
                return Err(format!("invalid arm receipt: {}/{}", o.id, a.name));
            }
            let mut keys = BTreeSet::new();
            for f in &a.frames {
                keys.insert(f.key()?);
                external_attested += usize::from(matches!(
                    f.validation,
                    Validation::ExternalDecoderAttested { .. }
                ));
            }
            if a.status == Status::Completed {
                if o.negative_control {
                    let counts = negative.entry(a.name.clone()).or_default();
                    counts.0 += 1;
                    counts.1 += keys.len();
                    counts.2 += o.duration_seconds;
                }
                sets.insert(&a.name, keys);
            } else {
                failures.push(json!({"id":o.id,"arm":a.name,"status":a.status,"error":a.error}));
            }
        }
        for name in &names {
            if !arms.contains_key(name) {
                failures.push(json!({"id":o.id,"arm":name,"status":"missing"}));
            }
        }
        let mut comparisons = Vec::new();
        if !o.negative_control {
            for name in &study.candidate_arms {
                if let Some((b, c)) = sets.get(&study.baseline_arm).zip(sets.get(name)) {
                    let ba = arms[&study.baseline_arm];
                    let ca = arms[name];
                    let mut strata =
                        vec!["all_valid_pairs".into(), format!("mission/{}", o.mission)];
                    strata.push(format!(
                        "exposure/{}",
                        match o.exposure {
                            Exposure::Development => "development",
                            Exposure::HeldOut => "held_out",
                            Exposure::Unknown => "unknown",
                        }
                    ));
                    if o.confirmed_signal == Some(true) {
                        strata.push("confirmed_signal".into());
                    }
                    if !b.is_empty() {
                        strata.push("baseline_positive".into());
                    }
                    for stratum in strata {
                        aggregates
                            .entry((name.clone(), stratum))
                            .or_default()
                            .add(o, b, c, ba, ca);
                    }
                    comparisons.push(json!({"candidate":name,"added":c.difference(b).collect::<Vec<_>>(),"lost":b.difference(c).collect::<Vec<_>>(),
                        "baseline_unique":b.len(),"candidate_unique":c.len()}));
                }
            }
        }
        rows.push(json!({"id":o.id,"mission":o.mission,"station":o.station,"pass_group":o.pass_group,"exposure":o.exposure,
            "confirmed_signal":o.confirmed_signal,"signal_evidence":o.signal_evidence,"negative_control":o.negative_control,
            "comparisons":comparisons,"arms":o.arms.iter().map(|a|json!({"name":a.name,"status":a.status,"wall_seconds":a.wall_seconds,"cpu_seconds":a.cpu_seconds})).collect::<Vec<_>>()}));
    }
    let aggregates: Vec<_> = aggregates.into_iter().map(|((candidate,stratum), a)| json!({"candidate":candidate,"stratum":stratum,"metrics":a.value()})).collect();
    let negative: Vec<_> = negative.into_iter().map(|(arm,(n,accepted,seconds))| json!({"arm":arm,"completed_controls":n,"accepted_unique":accepted,"hours":seconds/3600.,"accepted_per_hour":accepted as f64*3600./seconds})).collect();
    Ok(
        json!({"schema":"framelift-recovery-study-summary-v1","selected_observations":study.observations.len(),
        "baseline_arm":study.baseline_arm,"candidate_arms":study.candidate_arms,"aggregates":aggregates,
        "negative_controls":negative,"failures_and_missing":failures,"observations":rows,"external_attested_frame_records":external_attested,
        "publication_ready":false,"equal_compute_certified":false,"source_provenance_verified":false,
        "limits":["Observation-local unique payloads; equal bytes in different observations count separately.",
            "External decoder attestation is not an independently checked received FCS.",
            "CRC-valid does not prove transmitter truth; negative controls and protocol corroboration remain necessary.",
            "Signal/exposure labels come from the declared study, not candidate outcomes, and are not certified here.",
            "Failures/timeouts/unsupported/missing arms are attrition, not zero-yield successes.",
            "Bootstrap connects shared stations and pass groups; intervals are descriptive, not proof of independent sampling.",
            "Totals and timing compare only completed pairs; selection, budgets and runtime provenance require separate audit."]}),
    )
}

#[cfg(test)]
#[path = "tests/recovery_metrics_tests.rs"]
mod tests;
