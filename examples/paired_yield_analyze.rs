//! Read-only exact-set analysis; never runs a receiver or certifies cohort independence.
#[path = "support/today20_baselines.rs"]
mod baselines;
#[path = "support/native_result_json.rs"]
mod native_result_json;
use clap::Parser;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};
use telemetry_yield_rs::{input, protocol};
const ARMS: [&str; 5] = [
    "progressive_v3",
    "innovation_v2",
    "innovation_v1_no_codec",
    "direwolf",
    "gr_satellites",
];
type Set = BTreeSet<String>;
#[derive(Parser)]
struct Args {
    #[arg(long)]
    cohort: PathBuf,
    #[arg(long)]
    comparison: PathBuf,
    #[arg(long)]
    output: PathBuf,
}
fn bytes_id(path: &Path) -> Result<Value, String> {
    Ok(json!(input::identity(path)?))
}
fn verify(v: &Value) -> Result<(), String> {
    let p = Path::new(v["path"].as_str().ok_or("identity path absent")?);
    if bytes_id(p)? != *v {
        return Err(format!("changed artifact {}", p.display()));
    }
    Ok(())
}
fn same(a: &Value, b: &Value) -> bool {
    a["sha256"].is_string()
        && a["bytes"].is_u64()
        && a["sha256"] == b["sha256"]
        && a["bytes"] == b["bytes"]
}
fn set(v: &Value) -> Result<Set, String> {
    let mut result = Set::new();
    for h in v.as_array().ok_or("frame set missing")? {
        let h = h.as_str().ok_or("nonstring PDU")?;
        let b = hex::decode(h).map_err(|e| e.to_string())?;
        if protocol::parse_ax25_ui(&b).is_none()
            || h != hex::encode(&b)
            || !result.insert(h.to_string())
        {
            return Err("noncanonical, duplicate or non-UI PDU".into());
        }
    }
    Ok(result)
}
fn crc(body: &[u8]) -> u16 {
    let mut c = 0xffffu16;
    for &b in body {
        c ^= u16::from(b);
        for _ in 0..8 {
            c = if c & 1 != 0 {
                (c >> 1) ^ 0x8408
            } else {
                c >> 1
            };
        }
    }
    !c
}
fn native_set(v: &Value) -> Result<Set, String> {
    let mut out = Set::new();
    for s in v.as_array().ok_or("received native frames missing")? {
        let f = hex::decode(s.as_str().ok_or("frame not hex")?).map_err(|e| e.to_string())?;
        if f.len() < 2
            || crc(&f[..f.len() - 2]) != u16::from_le_bytes([f[f.len() - 2], f[f.len() - 1]])
        {
            return Err("invalid received FCS".into());
        }
        let p = &f[..f.len() - 2];
        if protocol::parse_ax25_ui(p).is_none() {
            return Err("native frame not UI".into());
        }
        out.insert(hex::encode(p));
    }
    Ok(out)
}
fn date(v: &Value, key: &str) -> Result<i64, String> {
    chrono::DateTime::parse_from_rfc3339(v[key].as_str().ok_or("timestamp absent")?)
        .map(|x| x.timestamp())
        .map_err(|e| e.to_string())
}
fn utc_day(row: &Value) -> Result<String, String> {
    Ok(chrono::DateTime::from_timestamp(date(row, "start")?, 0)
        .ok_or("UTC date out of range")?
        .date_naive()
        .to_string())
}
fn recovery_categories(rows: &[Value], field: &str) -> Value {
    let (mut gain, mut loss, mut either, mut both, mut neither) =
        (0usize, 0usize, 0usize, 0usize, 0usize);
    for r in rows {
        let g = r[field]["gain_count"].as_u64().unwrap() > 0;
        let l = r[field]["loss_count"].as_u64().unwrap() > 0;
        gain += usize::from(g);
        loss += usize::from(l);
        either += usize::from(g || l);
        both += usize::from(g && l);
        neither += usize::from(!g && !l);
    }
    let mut out = json!({"denominator_complete_observations":rows.len()});
    for (name, count) in [
        ("with_gain", gain),
        ("with_loss", loss),
        ("with_either", either),
        ("with_both", both),
        ("with_neither", neither),
    ] {
        out[name] = json!({"count":count,"fraction":if rows.is_empty(){Value::Null}else{json!(count as f64/rows.len()as f64)}});
    }
    out
}
fn groups(rows: &[Value], gap: i64) -> Result<BTreeMap<u64, usize>, String> {
    if gap < 0 {
        return Err("negative gap".into());
    }
    let mut records = vec![];
    let mut seen = BTreeSet::new();
    for r in rows {
        let id = r["id"].as_u64().filter(|x| *x > 0).ok_or("id absent")?;
        if !seen.insert(id) {
            return Err("duplicate ID".into());
        }
        let sat = r
            .get("norad_cat_id")
            .or_else(|| r.get("norad"))
            .and_then(Value::as_u64)
            .ok_or("NORAD absent")?;
        let s = date(r, "start")?;
        let e = date(r, "end")?;
        if e <= s {
            return Err("invalid interval".into());
        }
        records.push((sat, s, e, id));
    }
    records.sort();
    let (mut previous, mut until, mut n) = (None, i64::MIN, 0);
    let mut out = BTreeMap::new();
    for (sat, s, e, id) in records {
        if previous != Some(sat) || s > until.saturating_add(gap) {
            n += 1;
            previous = Some(sat);
            until = e;
        } else {
            until = until.max(e);
        }
        out.insert(id, n);
    }
    Ok(out)
}
fn difference(a: &Set, b: &Set) -> Value {
    let gained: Vec<_> = a.difference(b).cloned().collect();
    let lost: Vec<_> = b.difference(a).cloned().collect();
    json!({"gained":gained,"lost":lost,"gain_count":gained.len(),"loss_count":lost.len(),"net":gained.len() as i64-lost.len() as i64,"baseline":b.len()})
}
fn bootstrap(values: &[(usize, i64, u64)], gap: i64) -> Value {
    let mut map: BTreeMap<usize, (i64, u64, u64)> = BTreeMap::new();
    for &(k, d, b) in values {
        let v = map.entry(k).or_default();
        v.0 += d;
        v.1 += b;
        v.2 += 1;
    }
    let mut result = json!({"gap_seconds":gap,"contributing_components":map.len(),"available":false,"qualification":"descriptive paired component bootstrap; independence unverified; no significance gate"});
    if map.len() < 10 {
        result["reason"] = json!(
            "fewer than 10 contributing components; operational suppression not sufficient-cluster theorem"
        );
        return result;
    }
    let v: Vec<_> = map.into_values().collect();
    let mut state = 0xd451_4bca_7f92_2026u64;
    let (mut means, mut relative) = (vec![], vec![]);
    for _ in 0..10000 {
        let (mut d, mut b, mut n) = (0i64, 0u64, 0u64);
        for _ in 0..v.len() {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            let x = v[(state as usize) % v.len()];
            d += x.0;
            b += x.1;
            n += x.2;
        }
        means.push(d as f64 / n as f64);
        if b > 0 {
            relative.push(100.0 * d as f64 / b as f64);
        }
    }
    fn bounds(mut x: Vec<f64>) -> Value {
        x.sort_by(f64::total_cmp);
        json!([
            x[x.len() * 25 / 1000],
            x[(x.len() * 975 / 1000).min(x.len() - 1)]
        ])
    }
    result["available"] = json!(true);
    result["resamples"] = json!(10000);
    result["seed_hex"] = json!("d4514bca7f922026");
    result["net_pdus_per_observation_percentile95"] = bounds(means);
    result["relative_undefined_draws"] = json!(10000 - relative.len());
    result["relative_gain_percent_percentile95"] = if relative.len() == 10000 {
        bounds(relative)
    } else {
        Value::Null
    };
    result
}
fn prospective_day_strata(selected_days: &[String], complete: &[Value], field: &str) -> Vec<Value> {
    let days: Vec<_> = (12..19).map(|d| format!("2026-09-{d:02}")).collect();
    if selected_days.is_empty() || !selected_days.iter().all(|d| days.contains(d)) {
        return vec![];
    }
    days.iter().map(|day| {
        let selected = selected_days.iter().filter(|d| *d == day).count();
        let rs = complete.iter().filter(|r| r["day"] == *day).cloned().collect::<Vec<_>>();
        let net:i64 = rs.iter().map(|r| r[field]["net"].as_i64().unwrap()).sum();
        let base:u64 = rs.iter().map(|r| r[field]["baseline"].as_u64().unwrap()).sum();
        json!({"utc_day":day,"selected_observations":selected,"complete_observations":rs.len(),
            "missing_or_incomplete_observations":selected-rs.len(),"net_pairs":net,"baseline_pairs":base,
            "empty_complete_case_set_is_not_zero_yield_for_selected":rs.is_empty(),
            "mean_net":if rs.is_empty(){Value::Null}else{json!(net as f64/rs.len() as f64)},
            "categories":recovery_categories(&rs,field)})
    }).collect()
}
fn run(a: Args) -> Result<(), String> {
    let cohort_id = bytes_id(&a.cohort)?;
    let cohort = input::read_json(&a.cohort)?;
    let rows = cohort["observations"]
        .as_array()
        .ok_or("cohort observations absent")?;
    if rows.is_empty() || cohort["selected_count"].as_u64() != Some(rows.len() as u64) {
        return Err("cohort count mismatch".into());
    }
    let maps = [groups(rows, 0)?, groups(rows, 1800)?, groups(rows, 7200)?];
    let mut evidence = vec![cohort_id.clone()];
    let mut complete = vec![];
    let mut unavailable = vec![];
    let mut counts: BTreeMap<String, u64> = ARMS.into_iter().map(|x| (x.into(), 0)).collect();
    let mut all_sets: BTreeMap<String, Set> =
        ARMS.into_iter().map(|x| (x.into(), Set::new())).collect();
    let mut pair_bytes = counts.clone();
    for row in rows {
        let id = row["id"].as_u64().unwrap();
        let p = a.comparison.join(format!("obs-{id}/result.json"));
        if !p.exists() {
            unavailable
                .push(json!({"id":id,"reason":"not_attempted_or_result_missing","not_zero":true}));
            continue;
        }
        let ident = bytes_id(&p)?;
        let r = input::read_json(&p)?;
        if r["observation_id"] != id || r["row"] != *row {
            return Err("observation metadata mismatch".into());
        }
        evidence.push(ident);
        if r["status"] != "complete"
            || ARMS
                .iter()
                .any(|name| r["decoders"][name]["status"] != "complete")
        {
            unavailable.push(json!({"id":id,"reason":"not_all_five_complete","observation_status":r["status"],"arm_statuses":ARMS.iter().map(|name|(name.to_string(),r["decoders"][name]["status"].clone())).collect::<BTreeMap<_,_>>(),"not_zero":true}));
            continue;
        }
        verify(&r["source"])?;
        evidence.push(r["source"].clone());
        let mut sets = BTreeMap::new();
        for name in ARMS {
            let arm = &r["decoders"][name];
            if !same(&arm["input"], &r["input"])
                || !same(&arm["source"], &r["source"])
                || arm["process"]["success"] != true
                || arm["process"]["returncode"] != 0
                || arm["process"]["timed_out"] != false
            {
                return Err("arm input/process mismatch".into());
            }
            let pdus = set(&arm["strict_ui_payloads"])?;
            if arm["strict_ui_unique_count"].as_u64() != Some(pdus.len() as u64) {
                return Err("PDU count mismatch".into());
            }
            let mut native_frames = None;
            for artifact in arm["artifacts"].as_array().ok_or("arm artifacts absent")? {
                verify(artifact)?;
                evidence.push(artifact.clone());
                let path = artifact["path"].as_str().ok_or("artifact path missing")?;
                if path.ends_with("/decode/result.json") {
                    let v = native_result_json::read_native_report(Path::new(path))?;
                    native_frames = Some(if name == "progressive_v3" {
                        native_set(&v["frame_with_fcs_hex"])?
                    } else {
                        native_set(&v["report"]["union_full_frames"])?
                    });
                }
            }
            if name != "direwolf"
                && name != "gr_satellites"
                && native_frames.as_ref() != Some(&pdus)
            {
                return Err("native received frame set differs from score".into());
            }
            if name == "direwolf" {
                verify(&arm["process"]["stdout"])?;
                let raw = input::read_bytes_bounded(
                    Path::new(
                        arm["process"]["stdout"]["path"]
                            .as_str()
                            .ok_or("Dire Wolf log absent")?,
                    ),
                    64 * 1024 * 1024,
                )?;
                if baselines::parse_direwolf_atest(&String::from_utf8_lossy(&raw))?
                    .strict_ui_payloads
                    != pdus
                {
                    return Err("Dire Wolf scored set differs from actual hex log".into());
                }
            }
            if name == "gr_satellites" {
                let kiss = arm["artifacts"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter(|a| {
                        a["path"]
                            .as_str()
                            .is_some_and(|p| p.ends_with("/frames.kiss"))
                    })
                    .collect::<Vec<_>>();
                if kiss.len() != 1 {
                    return Err("exactly one gr-satellites KISS artifact required".into());
                }
                let raw = input::read_bytes_bounded(
                    Path::new(kiss[0]["path"].as_str().unwrap()),
                    64 * 1024 * 1024,
                )?;
                if baselines::parse_gr_satellites_kiss(&raw)?.strict_ui_payloads != pdus {
                    return Err("gr-satellites scored set differs from actual KISS bytes".into());
                }
            }
            *counts.get_mut(name).unwrap() += pdus.len() as u64;
            *pair_bytes.get_mut(name).unwrap() +=
                pdus.iter().map(|s| (s.len() / 2) as u64).sum::<u64>();
            all_sets.get_mut(name).unwrap().extend(pdus.clone());
            sets.insert(name.to_string(), pdus);
        }
        let external: Set = sets["direwolf"]
            .union(&sets["gr_satellites"])
            .cloned()
            .collect();
        let native: Set = ["progressive_v3", "innovation_v2", "innovation_v1_no_codec"]
            .into_iter()
            .flat_map(|name| sets[name].iter().cloned())
            .collect();
        complete.push(json!({"id":id,"day":utc_day(row)?,"primary":difference(&sets["progressive_v3"],&external),"innovation_vs_external":difference(&sets["innovation_v2"],&external),"innovation_vs_v1":difference(&sets["innovation_v2"],&sets["innovation_v1_no_codec"]),"native_union_vs_external":difference(&native,&external),"native_union_count":native.len()}));
    }
    for e in &evidence {
        verify(e)?;
    }
    let mut analyses = BTreeMap::new();
    for field in [
        "primary",
        "innovation_vs_external",
        "innovation_vs_v1",
        "native_union_vs_external",
    ] {
        let gains: u64 = complete
            .iter()
            .map(|r| r[field]["gain_count"].as_u64().unwrap())
            .sum();
        let losses: u64 = complete
            .iter()
            .map(|r| r[field]["loss_count"].as_u64().unwrap())
            .sum();
        let base: u64 = complete
            .iter()
            .map(|r| r[field]["baseline"].as_u64().unwrap())
            .sum();
        let mut intervals = vec![];
        for (index, gap) in [0, 1800, 7200].into_iter().enumerate() {
            let values = complete
                .iter()
                .map(|r| {
                    (
                        maps[index][&r["id"].as_u64().unwrap()],
                        r[field]["net"].as_i64().unwrap(),
                        r[field]["baseline"].as_u64().unwrap(),
                    )
                })
                .collect::<Vec<_>>();
            intervals.push(bootstrap(&values, gap));
        }
        let n = complete.len();
        let days: Vec<_> = (12..19).map(|d| format!("2026-09-{d:02}")).collect();
        let selected_days = rows.iter().map(utc_day).collect::<Result<Vec<_>, _>>()?;
        let day_strata = prospective_day_strata(&selected_days, &complete, field);
        let prospective_days_applicable = !day_strata.is_empty();
        let leave_days = if prospective_days_applicable {
            days.iter().map(|day|{let other=complete.iter().filter(|r|r["day"]!=*day).collect::<Vec<_>>();let net:i64=other.iter().map(|r|r[field]["net"].as_i64().unwrap()).sum();json!({"omitted_utc_day":day,"remaining_observations":other.len(),"mean_net":if other.is_empty(){Value::Null}else{json!(net as f64/other.len()as f64)}})}).collect::<Vec<_>>()
        } else {
            vec![]
        };
        let mut gained_global = Set::new();
        let mut lost_global = Set::new();
        let mut gained_bytes = 0u64;
        let mut lost_bytes = 0u64;
        for row in &complete {
            for (key, united, total) in [
                ("gained", &mut gained_global, &mut gained_bytes),
                ("lost", &mut lost_global, &mut lost_bytes),
            ] {
                for p in row[field][key].as_array().unwrap() {
                    let p = p.as_str().unwrap();
                    united.insert(p.to_string());
                    *total += (p.len() / 2) as u64;
                }
            }
        }
        let external_global: Set = all_sets["direwolf"]
            .union(&all_sets["gr_satellites"])
            .cloned()
            .collect();
        let comparator_global = if field == "innovation_vs_v1" {
            &all_sets["innovation_v1_no_codec"]
        } else {
            &external_global
        };
        let globally_absent: Set = gained_global
            .difference(comparator_global)
            .cloned()
            .collect();
        analyses.insert(field,json!({"gained_pairs":gains,"lost_pairs":losses,"gained_pair_pdu_bytes":gained_bytes,"lost_pair_pdu_bytes":lost_bytes,"globally_distinct_gained_somewhere":gained_global.len(),"globally_distinct_lost_somewhere":lost_global.len(),"globally_absent_from_comparator_everywhere_in_complete_cases":globally_absent.len(),"globally_absent_pdu_bytes_in_complete_cases":globally_absent.iter().map(|p|p.len()/2).sum::<usize>(),"complete_case_scope_not_entire_intended_cohort":true,"not_global_archive_novelty":true,"net_pairs":gains as i64-losses as i64,"baseline_pairs":base,"mean_net_pdus_per_observation":if n==0{Value::Null}else{json!((gains as f64-losses as f64)/n as f64)},"relative_gain_percent":if base==0{Value::Null}else{json!(100.0*(gains as f64-losses as f64)/base as f64)},"observations_with_gain":complete.iter().filter(|r|r[field]["gain_count"].as_u64().unwrap()>0).count(),"observations_with_loss":complete.iter().filter(|r|r[field]["loss_count"].as_u64().unwrap()>0).count(),"uncertainty":intervals,"prospective_day_summary_applicable":prospective_days_applicable,"prospective_leave_one_day_out_descriptive":leave_days}));
        let entry = analyses.get_mut(field).unwrap();
        entry["recovery_occurrence_categories"] = recovery_categories(&complete, field);
        entry["prospective_utc_day_strata"] = json!(day_strata);
    }
    let out = json!({"schema":"paired-yield-analysis-v1","status":"complete_aggregation_not_receiver_admission","analyzer":bytes_id(&std::env::current_exe().map_err(|e|e.to_string())?)?,"selected":rows.len(),"all_five_complete":complete.len(),"missing_or_incomplete":unavailable,"input_evidence":evidence,"pair_counts":counts,"pair_pdu_bytes_excluding_fcs":pair_bytes,"globally_distinct_pdu_counts":all_sets.iter().map(|(k,v)|(k,v.len())).collect::<BTreeMap<_,_>>(),"globally_distinct_pdu_bytes":all_sets.iter().map(|(k,v)|(k,v.iter().map(|s|s.len()/2).sum::<usize>())).collect::<BTreeMap<_,_>>(),"analyses":analyses,"rows":complete,"primary_uncertainty_gap_seconds":7200,"sampling_and_independence_certified":false,"full_task_and_runtime_admission_reperformed":false,"verification_scope":"hashes/source/shared-input/external process success; native received CRC and exact frame sets; relies on separate runner/control audits for complete task policy and original process provenance","publication_ready":false});
    let mut out = out;
    out["native_union_members"] =
        json!(["progressive_v3", "innovation_v2", "innovation_v1_no_codec"]);
    out["external_hex_and_kiss_sets_rederived_from_hashed_artifacts"] = json!(true);
    out["all_recovery_summaries_use_only_all_five_complete_cases"] = json!(true);
    input::write_json_new(&a.output, &out)
}
fn main() {
    if let Err(e) = run(Args::parse()) {
        eprintln!("analysis failed: {e}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn row(id: u64, start: &str, end: &str) -> Value {
        json!({"id":id,"norad_cat_id":68635,"start":start,"end":end})
    }
    #[test]
    fn crc_check() {
        assert_eq!(crc(b"123456789"), 0x906e);
    }
    #[test]
    fn day_strata_use_utc_not_timestamp_prefix() {
        let r = row(1, "2026-09-13T01:00:00+02:00", "2026-09-13T01:10:00+02:00");
        assert_eq!(utc_day(&r).unwrap(), "2026-09-12");
    }
    #[test]
    fn all_incomplete_prospective_week_still_has_seven_missingness_strata() {
        let selected = vec![
            "2026-09-12".into(),
            "2026-09-12".into(),
            "2026-09-18".into(),
        ];
        let days = prospective_day_strata(&selected, &[], "primary");
        assert_eq!(days.len(), 7);
        assert_eq!(days[0]["selected_observations"], 2);
        assert_eq!(days[0]["missing_or_incomplete_observations"], 2);
        assert_eq!(days[0]["complete_observations"], 0);
        assert!(days[0]["mean_net"].is_null());
        assert!(days[0]["categories"]["with_gain"]["fraction"].is_null());
        assert_eq!(days[1]["selected_observations"], 0);
        assert!(prospective_day_strata(&["2026-09-11".into()], &[], "primary").is_empty());
    }
    #[test]
    fn category_fractions_handle_overlap_and_empty_denominator() {
        let rows = vec![
            json!({"x":{"gain_count":1,"loss_count":0}}),
            json!({"x":{"gain_count":1,"loss_count":1}}),
            json!({"x":{"gain_count":0,"loss_count":0}}),
            json!({"x":{"gain_count":0,"loss_count":1}}),
        ];
        let c = recovery_categories(&rows, "x");
        assert_eq!(c["with_either"]["fraction"], 0.75);
        assert_eq!(c["with_neither"]["fraction"], 0.25);
        assert_eq!(c["with_both"]["count"], 1);
        assert!(recovery_categories(&[], "x")["with_gain"]["fraction"].is_null());
    }
    #[test]
    fn paired_set_not_count_difference() {
        let a = Set::from(["a".into(), "b".into()]);
        let b = Set::from(["b".into(), "c".into()]);
        let d = difference(&a, &b);
        assert_eq!(d["gain_count"], 1);
        assert_eq!(d["loss_count"], 1);
        assert_eq!(d["net"], 0);
    }
    #[test]
    fn transitive_gap_can_join_distinct_passes() {
        let r = vec![
            row(1, "2026-09-12T00:00:00Z", "2026-09-12T00:10:00Z"),
            row(2, "2026-09-12T01:30:00Z", "2026-09-12T01:40:00Z"),
            row(3, "2026-09-12T03:00:00Z", "2026-09-12T03:10:00Z"),
        ];
        assert_eq!(groups(&r, 0).unwrap().values().max(), Some(&3));
        assert_eq!(groups(&r, 7200).unwrap().values().max(), Some(&1));
    }
    #[test]
    fn interval_identity_and_boundaries() {
        let a = row(1, "2026-09-12T00:00:00Z", "2026-09-12T00:10:00Z");
        let b = row(2, "2026-09-12T00:10:00Z", "2026-09-12T00:20:00Z");
        assert_eq!(groups(&[a.clone(), b], 0).unwrap().values().max(), Some(&1));
        assert!(groups(&[a.clone(), a], 0).is_err());
    }
    #[test]
    fn too_few_clusters_and_empty_never_intervals() {
        assert_eq!(bootstrap(&[], 7200)["available"], false);
        assert_eq!(bootstrap(&[(1, 3, 0)], 0)["available"], false);
    }
    #[test]
    fn zero_baseline_no_relative_interval() {
        let v = (0..10).map(|i| (i, 1, 0)).collect::<Vec<_>>();
        let x = bootstrap(&v, 0);
        assert_eq!(x["relative_undefined_draws"], 10000);
        assert!(x["relative_gain_percent_percentile95"].is_null());
        assert_eq!(
            x["net_pdus_per_observation_percentile95"],
            json!([1.0, 1.0])
        );
    }
    #[test]
    fn deterministic_positive_bootstrap() {
        let v = (0..10).map(|i| (i, 1, 2)).collect::<Vec<_>>();
        let x = bootstrap(&v, 0);
        assert_eq!(x, bootstrap(&v, 0));
        assert_eq!(x["relative_gain_percent_percentile95"], json!([50.0, 50.0]));
    }
}
