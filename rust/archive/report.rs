//! Paired packet-set metrics; failures are attrition, not negative receptions.
use super::uncertainty::{self, Pair};
use super::{
    Sealed,
    cohort::Cohort,
    runner::{self, Arm, Release, Row},
    transport,
};
use crate::input;
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

#[derive(Default, Clone, Debug, Serialize)]
struct Totals {
    observations: usize,
    observations_with_additions: usize,
    reference_pdus: usize,
    candidate_pdus: usize,
    added_pdus: usize,
    missed_pdus: usize,
    added_payload_bytes: usize,
    missed_payload_bytes: usize,
}

impl Totals {
    fn add(&mut self, reference: &BTreeSet<String>, candidate: &BTreeSet<String>) {
        self.observations += 1;
        self.reference_pdus += reference.len();
        self.candidate_pdus += candidate.len();
        let added: Vec<_> = candidate.difference(reference).collect();
        let missed: Vec<_> = reference.difference(candidate).collect();
        self.observations_with_additions += usize::from(!added.is_empty());
        self.added_pdus += added.len();
        self.missed_pdus += missed.len();
        self.added_payload_bytes += added.iter().map(|s| s.len() / 2).sum::<usize>();
        self.missed_payload_bytes += missed.iter().map(|s| s.len() / 2).sum::<usize>();
    }
    fn value(&self) -> Value {
        let mut result = json!(self);
        result["net_pdus"] = json!(self.added_pdus as i64 - self.missed_pdus as i64);
        result["net_gain_percent"] = if self.reference_pdus == 0 {
            Value::Null
        } else {
            json!(
                100.0 * (self.candidate_pdus as f64 - self.reference_pdus as f64)
                    / self.reference_pdus as f64
            )
        };
        result
    }
}

pub fn create(
    cohort_path: &Path,
    release_path: &Path,
    root: &Path,
    output: &Path,
) -> Result<Value, String> {
    let root = std::fs::canonicalize(root).map_err(|e| e.to_string())?;
    let cohort = Sealed::<Cohort>::read(cohort_path)?;
    let release = Sealed::<Release>::read(release_path)?;
    cohort.content.validate()?;
    release.content.validate()?;
    if release.content.cohort_sha256 != cohort.sha256 {
        return Err("report release/cohort mismatch".into());
    }
    let mut totals = BTreeMap::<String, Totals>::new();
    let mut paired = BTreeMap::<String, Vec<Pair>>::new();
    let mut global = BTreeMap::<String, BTreeSet<String>>::new();
    let mut records = Vec::new();
    let mut missing = 0;
    let mut invalid = 0;
    let mut terminal = 0;
    let mut processes = Vec::new();
    let mut attempted_arms = 0;
    for observation in &cohort.content.observations {
        let dir = root.join(observation.id.to_string());
        let path = dir.join("row.json");
        if !path.exists() {
            missing += 1;
            records.push(json!({"id":observation.id,"status":"not_yet_terminal"}));
            continue;
        }
        let row = Sealed::<Row>::read(&path)?;
        if row.content.id != observation.id || row.content.release_sha256 != release.sha256 {
            return Err("row bound to another observation or release".into());
        }
        terminal += 1;
        for identity in [&row.content.source, &row.content.pcm]
            .into_iter()
            .flatten()
        {
            runner::verify_identity(identity)?;
        }
        let mut arm_keys = BTreeSet::new();
        for arm in &row.content.arms {
            attempted_arms += 1;
            let planned = match arm.budget_ms {
                Some(ms) => {
                    ["fixed", "marginal-yield"].contains(&arm.name.as_str())
                        && cohort.content.protocol.budgets_ms.contains(&ms)
                }
                None => ["direwolf", "gr_satellites"].contains(&arm.name.as_str()),
            };
            if !planned {
                return Err("unregistered arm name or budget".into());
            }
            if !arm_keys.insert((arm.name.clone(), arm.budget_ms)) {
                return Err("duplicate arm".into());
            }
            runner::audit_arm(
                arm,
                &dir,
                row.content.pcm.as_ref(),
                &release.content,
                cohort.content.protocol.threads,
            )?;
            if !arm.valid() {
                invalid += 1;
            }
            if let Some(process) = &arm.process {
                processes.push(json!({"id":observation.id,"arm":arm.name,"budget_ms":arm.budget_ms,"status":arm.status,
                    "wall_seconds":process["wall_seconds"],"user_cpu_seconds":process["user_cpu_seconds"],
                    "system_cpu_seconds":process["system_cpu_seconds"],"max_rss_kib":process["maximum_rss_kib"],
                    "cpu_usage_incomplete_on_timeout":process["cpu_usage_incomplete_on_timeout"]}));
            }
        }
        let find = |name: &str, budget: Option<u64>| -> Option<&Arm> {
            row.content
                .arms
                .iter()
                .find(|a| a.name == name && a.budget_ms == budget && a.valid())
        };
        let baseline = find("direwolf", None)
            .zip(find("gr_satellites", None))
            .map(|(a, b)| {
                a.payload_hex
                    .union(&b.payload_hex)
                    .cloned()
                    .collect::<BTreeSet<_>>()
            });
        let mut comparisons = Vec::new();
        for budget in &cohort.content.protocol.budgets_ms {
            let fixed = find("fixed", Some(*budget));
            let adaptive = find("marginal-yield", Some(*budget));
            let mut pairs = Vec::new();
            if let (Some(fixed), Some(adaptive)) = (fixed, adaptive) {
                pairs.push((
                    "adaptive_vs_fixed",
                    &fixed.payload_hex,
                    &adaptive.payload_hex,
                ));
            }
            if let Some(reference) = &baseline {
                for (name, candidate) in [
                    ("adaptive_vs_baseline_union", adaptive),
                    ("fixed_vs_baseline_union", fixed),
                ] {
                    if let Some(candidate) = candidate {
                        pairs.push((name, reference, &candidate.payload_hex));
                    }
                }
            }
            for (name, reference, candidate) in pairs {
                let mut one = Totals::default();
                one.add(reference, candidate);
                comparisons.push(
                    json!({"comparison":name,"budget_ms":budget,"counts":one.value(),
                    "added_payload_hex":candidate.difference(reference).collect::<Vec<_>>(),
                    "missed_payload_hex":reference.difference(candidate).collect::<Vec<_>>()}),
                );
                let mut strata = vec!["all_valid_pairs"];
                if observation.reported_signal == Some(true) {
                    strata.push("metadata_reported_signal");
                }
                if baseline.as_ref().is_some_and(|set| !set.is_empty()) {
                    strata.push("reference_decoder_positive");
                }
                for stratum in strata {
                    let key = format!("{name}/{budget}/{stratum}");
                    totals
                        .entry(key.clone())
                        .or_default()
                        .add(reference, candidate);
                    paired.entry(key.clone()).or_default().push(Pair {
                        station: observation.station,
                        group: cohort.content.group_ids[&observation.id].clone(),
                        reference: reference.len(),
                        candidate: candidate.len(),
                    });
                    global
                        .entry(format!("{key}/reference"))
                        .or_default()
                        .extend(reference.iter().cloned());
                    global
                        .entry(format!("{key}/candidate"))
                        .or_default()
                        .extend(candidate.iter().cloned());
                }
            }
        }
        records.push(json!({"id":observation.id,"norad":observation.norad,"station":observation.station,
            "group_id":cohort.content.group_ids[&observation.id],"reported_signal":observation.reported_signal,
            "status":if row.content.error.is_some(){"incomplete_observation"}else{"terminal"},
            "error":row.content.error,"arms":row.content.arms.iter().map(|a|json!({"name":a.name,"budget_ms":a.budget_ms,"status":a.status,"error":a.error})).collect::<Vec<_>>(),
            "row_identity":input::identity(&path)?,"comparisons":comparisons}));
    }
    let aggregates: BTreeMap<_, _> = totals
        .iter()
        .map(|(k, v)| {
            let mut value = v.value();
            value["descriptive_uncertainty"] = uncertainty::bootstrap(&paired[k]);
            (k.clone(), value)
        })
        .collect();
    let global_counts: BTreeMap<_, _> = global.iter().map(|(k, v)| (k.clone(), v.len())).collect();
    let report = json!({"schema":"framelift-archive-report-v1","generated_utc":transport::utc(),
        "cohort_sha256":cohort.sha256,"release_sha256":release.sha256,"selected":cohort.content.observations.len(),
        "terminal_observations":terminal,"not_yet_terminal":missing,"invalid_arms":invalid,
        "attempted_arms":attempted_arms,"planned_arms":cohort.content.observations.len()*(2+2*cohort.content.protocol.budgets_ms.len()),
        "packet_endpoint":"observation-local unique strict AX.25 UI payloads, FCS excluded",
        "aggregates":aggregates,"globally_distinct_payload_counts":global_counts,"observations":records,"processes":processes,
        "publication_ready":false,"independence_certified":false,
        "independently_selected_within_declared_inventory_scope":cohort.content.independently_selected_within_inventory_scope,
        "limits":["Adaptive/fixed arms share wall budgets, thread count, task bank and prepared samples; scheduling/OS cost can differ.",
            "External baselines run to completion with a separate 120-second guard, not a matched-compute claim.",
            "Task preparation common to all arms is recorded separately; process wall includes each decoder startup and validation.",
            "Metadata reported signal is not transmitter ground truth; reference-positive strata are decoder-conditioned.",
            "Missing or invalid arms are excluded from paired estimates and retained explicitly; partial verified arms remain eligible.",
            "No population confidence interval, global nonexposure proof or false-acceptance rate is inferred from these counts.",
            "Received FCS is checked independently for native frames; external baseline FCS is stripped and decoder-attested.",
            "This FSK9600 AX.25 G3RUH component benchmark is not universal modulation qualification."]});
    input::write_json_new(output, &report)?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn gains_losses_bytes_and_zero_denominators_stay_separate() {
        let mut totals = Totals::default();
        totals.add(
            &BTreeSet::from(["aa".into(), "bbbb".into()]),
            &BTreeSet::from(["aa".into(), "ccddff".into()]),
        );
        let report = totals.value();
        assert_eq!(report["added_pdus"], 1);
        assert_eq!(report["missed_pdus"], 1);
        assert_eq!(report["added_payload_bytes"], 3);
        assert_eq!(report["missed_payload_bytes"], 2);
        assert_eq!(report["net_gain_percent"], 0.0);
        assert!(Totals::default().value()["net_gain_percent"].is_null());
    }
}
