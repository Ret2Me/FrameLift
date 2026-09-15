//! Read-only reconciliation of the two frozen private audio campaigns.
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};
use telemetry_yield_rs::{input, protocol};

fn checked(v: &Value) -> Result<Value, String> {
    let p = Path::new(v["path"].as_str().ok_or("identity path absent")?);
    let actual = serde_json::to_value(input::identity(p)?).map_err(|e| e.to_string())?;
    if actual != *v {
        return Err(format!("identity mismatch: {}", p.display()));
    }
    Ok(actual)
}
fn strings(v: &Value) -> Result<BTreeSet<String>, String> {
    let a = v.as_array().ok_or("PDU array absent")?;
    let mut s = BTreeSet::new();
    for x in a {
        if !s.insert(x.as_str().ok_or("PDU is not text")?.to_owned()) {
            return Err("duplicate PDU".into());
        }
    }
    Ok(s)
}
fn inc(map: &mut BTreeMap<String, u64>, name: &str, n: u64) {
    *map.entry(name.into()).or_default() += n;
}
fn audit(root: &Path) -> Result<(Value, BTreeMap<u64, Value>), String> {
    let summary_id = input::identity(&root.join("summary.json"))?;
    let summary = input::read_json(&root.join("summary.json"))?;
    let plan_id = input::identity(&root.join("plan.json"))?;
    let plan = input::read_json(&root.join("plan.json"))?;
    if summary["terminal_for_current_invocation"] != true
        || summary["plan_sha256"] != plan_id.sha256
    {
        return Err("campaign not terminal or plan mismatch".into());
    }
    let mut observations = BTreeMap::new();
    let mut statuses = BTreeMap::new();
    let mut totals = BTreeMap::<String, BTreeMap<String, u64>>::new();
    let mut artifacts = Vec::new();
    for row in summary["observations"]
        .as_array()
        .ok_or("summary observations absent")?
    {
        let id = row["observation_id"]
            .as_u64()
            .ok_or("invalid observation ID")?;
        let p = root.join(format!("obs-{id}/result.json"));
        let rid = input::identity(&p)?;
        let r = input::read_json(&p)?;
        if r["observation_id"] != id
            || r["instance"] != summary["instance"]
            || r["plan_sha256"] != plan_id.sha256
            || r["status"] != row["status"]
            || r["native_union_count"] != row["native_union_count"]
        {
            return Err(format!("observation binding mismatch {id}"));
        }
        checked(&r["source"])?;
        checked(&r["metadata"])?;
        inc(
            &mut statuses,
            r["status"].as_str().ok_or("status absent")?,
            1,
        );
        let mut native_union = BTreeSet::new();
        for (name, arm) in r["arms"].as_object().ok_or("arms absent")? {
            let total = totals.entry(name.clone()).or_default();
            let state = match arm["status"].as_str() {
                Some("complete") => "complete",
                Some(
                    "unsupported_profile"
                    | "unsupported_backend_contract"
                    | "unsupported_audio_representation",
                ) => "unsupported",
                _ => "incomplete",
            };
            inc(total, state, 1);
            // Historical unsupported declarations are nested in the bound
            // observation and intentionally have no fabricated execution record.
            if state != "unsupported"
                && (arm["source"] != r["source"] || arm["plan_sha256"] != plan_id.sha256)
            {
                return Err(format!("arm source/plan mismatch {id}/{name}"));
            }
            if state == "unsupported"
                && (!arm["candidate_unique_count"].is_null()
                    || !arm["strict_ui_unique_count"].is_null())
            {
                return Err("unsupported arm masquerades as measured count".into());
            }
            if state == "complete" {
                if arm["process"]["success"] != true {
                    return Err("complete arm lacks successful process".into());
                }
                let all = strings(&arm["pdus"])?;
                if arm["candidate_unique_count"].as_u64() != Some(all.len() as u64) {
                    return Err("candidate count mismatch".into());
                }
                inc(total, "candidate_observation_pdu_pairs", all.len() as u64);
                if let Some(n) = arm["strict_ui_unique_count"].as_u64() {
                    let strict = strings(&arm["strict_ui_pdus"])?;
                    if n != strict.len() as u64 || !strict.is_subset(&all) {
                        return Err("strict count/subset mismatch".into());
                    }
                    for pdu in &strict {
                        let b = hex::decode(pdu).map_err(|e| e.to_string())?;
                        if !protocol::valid_ax25_ui(&b) {
                            return Err("strict UI structure invalid".into());
                        }
                    }
                    inc(total, "strict_ui_validation_supported_complete", 1);
                    inc(total, "verified_strict_ui_observation_pdu_pairs", n);
                    if name == "native_audio" || name == "innovation_v2" {
                        native_union.extend(strict);
                    }
                }
                for artifact in arm["artifacts"]
                    .as_array()
                    .ok_or("complete arm artifacts absent")?
                {
                    checked(artifact)?;
                }
            }
        }
        if let Some(n) = r["native_union_count"].as_u64() {
            let declared = strings(&r["native_union_pdus"])?;
            if declared != native_union || n != native_union.len() as u64 {
                return Err("native union mismatch".into());
            }
        } else if !native_union.is_empty() {
            return Err("native union absent despite accepted packet".into());
        }
        if input::identity(&p)?.sha256 != rid.sha256 {
            return Err("result changed during audit".into());
        }
        artifacts.push(rid);
        if observations.insert(id, r).is_some() {
            return Err("duplicate observation ID".into());
        }
    }
    if serde_json::to_value(&statuses).map_err(|e| e.to_string())?
        != summary["observation_statuses"]
    {
        return Err("status summary mismatch".into());
    }
    for (name, total) in &totals {
        for key in [
            "complete",
            "unsupported",
            "incomplete",
            "candidate_observation_pdu_pairs",
            "strict_ui_validation_supported_complete",
            "verified_strict_ui_observation_pdu_pairs",
        ] {
            if summary["arms"][name][key].as_u64() != Some(*total.get(key).unwrap_or(&0)) {
                return Err(format!("arm total mismatch {name}/{key}"));
            }
        }
    }
    if let Some(selected) = plan["selected_ids"].as_array() {
        let ids = selected
            .iter()
            .map(|x| x.as_u64().ok_or("invalid selected ID"))
            .collect::<Result<BTreeSet<_>, _>>()?;
        if ids.len() != selected.len() || ids != observations.keys().copied().collect() {
            return Err("selected/result IDs differ".into());
        }
    }
    if input::identity(&root.join("summary.json"))?.sha256 != summary_id.sha256
        || input::identity(&root.join("plan.json"))?.sha256 != plan_id.sha256
    {
        return Err("campaign changed during audit".into());
    }
    Ok((
        json!({"summary":summary_id,"plan":plan_id,"observations":artifacts,"statuses":statuses,"arms":totals,"native_contract":plan["native_contract"],"runner":plan["runner"]}),
        observations,
    ))
}
fn combine(
    mut base: BTreeMap<u64, Value>,
    extra: BTreeMap<u64, Value>,
) -> Result<BTreeMap<u64, Value>, String> {
    for (id, r) in extra {
        let previous = base
            .get(&id)
            .ok_or("extension ID absent from original cohort")?;
        if previous["status"] != "unsupported_profile"
            || previous["source"] != r["source"]
            || previous["metadata"] != r["metadata"]
        {
            return Err("extension overlaps processed original or changes input".into());
        }
        base.insert(id, r);
    }
    Ok(base)
}
fn run() -> Result<(), String> {
    let a = std::env::args()
        .skip(1)
        .map(PathBuf::from)
        .collect::<Vec<_>>();
    if a.len() != 3 {
        return Err("usage: private_campaign_audit ORIGINAL EXTENSION NEW_OUTPUT.json".into());
    }
    let (base, br) = audit(&a[0])?;
    let (extension, er) = audit(&a[1])?;
    let combined = combine(br, er)?;
    let mut statuses = BTreeMap::new();
    let mut valid = Vec::new();
    for (id, r) in &combined {
        inc(
            &mut statuses,
            r["status"].as_str().ok_or("status absent")?,
            1,
        );
        for (name, arm) in r["arms"].as_object().ok_or("arms absent")? {
            if arm["status"] == "complete"
                && arm["strict_ui_unique_count"].as_u64().unwrap_or(0) > 0
            {
                valid.push(json!({"id":id,"receiver":name,"pdus":arm["strict_ui_pdus"],"independent_fcs_verified":arm["independent_fcs_verified"],"validation_evidence":arm["validation_evidence"]}));
            }
        }
    }
    input::write_json_new(
        &a[2],
        &json!({"schema":"private-campaign-reconciliation-v1","status":"pass","original":base,"extension":extension,"unique_recordings":combined.len(),"combined_original_statuses":statuses,"strict_ui_results":valid,"repaired_replays_included":false,"raw_candidates_are_not_all_valid_telemetry":true,"strict_ui_is_not_a_universal_telemetry_metric":true,"independent_received_fcs_revalidation_in_this_audit":false,"source_metadata_and_complete_arm_artifacts_rehashed":true,"publication_ready":false}),
    )
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn row(s: &str) -> Value {
        json!({"status":s,"source":{"sha256":"same"},"metadata":{"sha256":"m"}})
    }
    #[test]
    fn extension_only_replaces_unsupported_same_input() {
        assert!(
            combine(
                BTreeMap::from([(1, row("unsupported_profile"))]),
                BTreeMap::from([(1, row("complete"))])
            )
            .is_ok()
        );
        assert!(
            combine(
                BTreeMap::from([(1, row("complete"))]),
                BTreeMap::from([(1, row("complete"))])
            )
            .is_err()
        );
        assert!(combine(BTreeMap::new(), BTreeMap::from([(1, row("complete"))])).is_err());
        let mut changed = row("complete");
        changed["source"]["sha256"] = json!("changed");
        assert!(
            combine(
                BTreeMap::from([(1, row("unsupported_profile"))]),
                BTreeMap::from([(1, changed)])
            )
            .is_err()
        );
    }
    #[test]
    fn duplicate_or_malformed_packet_arrays_rejected() {
        assert!(strings(&json!(["aa", "aa"])).is_err());
        assert!(strings(&json!([1])).is_err());
        assert!(strings(&Value::Null).is_err());
        assert!(strings(&json!([])).unwrap().is_empty());
    }
}
