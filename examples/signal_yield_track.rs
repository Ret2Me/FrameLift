//! Additive, post-hoc signal strata. Never alters a decoder or frozen cohort.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{Read, Write},
    path::Path,
    time::Duration,
};
type R<T> = Result<T, Box<dyn std::error::Error>>;
type Set = BTreeSet<String>;
const ARMS: [&str; 5] = [
    "progressive_v3",
    "innovation_v2",
    "innovation_v1_no_codec",
    "direwolf",
    "gr_satellites",
];
const STRATA: [&str; 6] = [
    "all",
    "waterfall_with_signal",
    "waterfall_without_signal",
    "waterfall_unknown",
    "archive_status_good",
    "archive_demoddata_present",
];
fn groups(row: &Value) -> Vec<&'static str> {
    let mut g = vec![
        "all",
        match row["waterfall_status"].as_str() {
            Some("with-signal") => "waterfall_with_signal",
            Some("without-signal") => "waterfall_without_signal",
            _ => "waterfall_unknown",
        },
    ];
    if row["status"] == "good" {
        g.push("archive_status_good");
    }
    if row["demoddata"].as_array().is_some_and(|x| !x.is_empty()) {
        g.push("archive_demoddata_present");
    }
    g
}
fn id(p: &Path) -> R<Value> {
    let p = p.canonicalize()?;
    let mut f = File::open(&p)?;
    let mut h = Sha256::new();
    let mut n = 0;
    let mut b = [0u8; 65536];
    loop {
        let k = f.read(&mut b)?;
        if k == 0 {
            break;
        }
        h.update(&b[..k]);
        n += k;
    }
    Ok(json!({"path":p,"bytes":n,"sha256":hex::encode(h.finalize())}))
}
fn read(p: &Path) -> R<Value> {
    let f = File::open(p)?;
    if f.metadata()?.len() > 64 * 1024 * 1024 {
        return Err("oversized metadata/score".into());
    }
    Ok(serde_json::from_reader(f)?)
}
fn save(p: &Path, v: &Value) -> R<()> {
    let mut f = File::create_new(p)?;
    serde_json::to_writer_pretty(&mut f, v)?;
    f.write_all(b"\n")?;
    f.sync_all()?;
    Ok(())
}
fn replace(p: &Path, v: &Value) -> R<()> {
    let tmp = p.with_extension("next.json");
    save(&tmp, v)?;
    fs::rename(tmp, p)?;
    Ok(())
}
fn frames(v: &Value) -> R<Set> {
    let mut set = Set::new();
    for h in v.as_array().ok_or("PDU array missing")? {
        let h = h.as_str().ok_or("PDU not text")?;
        let bytes = hex::decode(h)?;
        if bytes.is_empty() || hex::encode(bytes) != h || !set.insert(h.to_owned()) {
            return Err("noncanonical or duplicate PDU".into());
        }
    }
    Ok(set)
}
fn delta(a: &Set, b: &Set) -> Value {
    let gained: Vec<_> = a.difference(b).collect();
    let lost: Vec<_> = b.difference(a).collect();
    json!({"ours":a.len(),"baseline":b.len(),"gain_count":gained.len(),"loss_count":lost.len(),"net":a.len() as i64-b.len() as i64,"gain_bytes":gained.iter().map(|x|x.len()/2).sum::<usize>(),"loss_bytes":lost.iter().map(|x|x.len()/2).sum::<usize>()})
}
fn score(r: &Value, row: &Value, manifest: &Value) -> R<Option<Value>> {
    if r["observation_id"] != row["id"] || r["row"] != *row {
        return Err("observation metadata mismatch".into());
    }
    if r["status"] != "complete"
        || ARMS
            .iter()
            .any(|a| r["decoders"][a]["status"] != "complete")
    {
        return Ok(None);
    }
    let mut sets = BTreeMap::new();
    for name in ARMS {
        let arm = &r["decoders"][name];
        let set = frames(&arm["strict_ui_payloads"])?;
        if arm["manifest_sha256"] != manifest["sha256"]
            || arm["strict_ui_unique_count"].as_u64() != Some(set.len() as u64)
            || arm["process"]["success"] != true
            || arm["process"]["returncode"] != 0
            || arm["process"]["timed_out"] != false
            || arm["input"] != r["input"]
            || arm["source"] != r["source"]
        {
            return Err("arm binding/process/count mismatch".into());
        }
        sets.insert(name, set);
    }
    let union = sets["direwolf"]
        .union(&sets["gr_satellites"])
        .cloned()
        .collect();
    Ok(Some(
        json!({"id":row["id"],"strata":groups(row),"primary":delta(&sets["progressive_v3"],&union),"innovation_vs_external":delta(&sets["innovation_v2"],&union),"progressive_vs_direwolf":delta(&sets["progressive_v3"],&sets["direwolf"]),"progressive_vs_gr_satellites":delta(&sets["progressive_v3"],&sets["gr_satellites"])}),
    ))
}
fn aggregate(rows: &[Value], field: &str) -> Value {
    let sum = |key: &str| {
        rows.iter()
            .map(|r| r[field][key].as_u64().unwrap())
            .sum::<u64>()
    };
    let b = sum("baseline");
    let ours = sum("ours");
    let net = ours as i64 - b as i64;
    json!({"ours":ours,"baseline":b,"added":sum("gain_count"),"lost":sum("loss_count"),"net":net,"added_pdu_bytes":sum("gain_bytes"),"lost_pdu_bytes":sum("loss_bytes"),"net_gain_percent":if b==0{Value::Null}else{json!(100.0*net as f64/b as f64)},"baseline_zero_percent_undefined":b==0,"observations_with_gain":rows.iter().filter(|r|r[field]["gain_count"].as_u64().unwrap()>0).count(),"observations_with_loss":rows.iter().filter(|r|r[field]["loss_count"].as_u64().unwrap()>0).count(),"observations_with_our_recovery":rows.iter().filter(|r|r[field]["ours"].as_u64().unwrap()>0).count()})
}
fn snapshot(cohort: &Value, comparison: &Path, manifest_id: &Value, final_path: &Path) -> R<Value> {
    let selected = cohort["observations"]
        .as_array()
        .ok_or("missing cohort rows")?;
    let mut complete = vec![];
    let mut incomplete = vec![];
    let mut evidence = vec![];
    for row in selected {
        let p = comparison.join(format!(
            "obs-{}/result.json",
            row["id"].as_u64().ok_or("id")?
        ));
        if !p.exists() {
            incomplete.push(
                json!({"id":row["id"],"strata":groups(row),"reason":"pending_or_not_attempted"}),
            );
            continue;
        }
        let before = id(&p)?;
        let r = read(&p)?;
        if id(&p)? != before {
            return Err("score changed while read".into());
        }
        evidence.push(before);
        if let Some(s) = score(&r, row, manifest_id)? {
            complete.push(s)
        } else {
            incomplete
                .push(json!({"id":row["id"],"strata":groups(row),"reason":"incomplete_not_zero"}))
        }
    }
    let mut report = json!({"schema":"signal-stratified-yield-v1","primary_signal_definition":"frozen cohort waterfall_status == with-signal; never based on our decoder success","post_hoc_secondary_analysis":true,"strata_can_overlap":true,"metadata_confirmation_is_not_independent_truth_or_satellite_identity_proof":true,"unknown_is_not_absent":true,"incomplete_is_not_zero":true,"selected":selected.len(),"complete":complete.len(),"rows":complete,"missing_or_incomplete":incomplete,"input_scores":evidence,"verification":"runner-scored provisional; raw frame/FCS revalidation deferred to final paired analyzer","final_analysis_crosschecked":false});
    let mut strata = serde_json::Map::new();
    for name in STRATA {
        let selected_n = selected
            .iter()
            .filter(|r| groups(r).contains(&name))
            .count();
        let rs: Vec<_> = complete
            .iter()
            .filter(|r| r["strata"].as_array().unwrap().contains(&json!(name)))
            .cloned()
            .collect();
        let mut s = json!({"selected":selected_n,"complete":rs.len(),"pending_or_incomplete":selected_n-rs.len()});
        for f in [
            "primary",
            "innovation_vs_external",
            "progressive_vs_direwolf",
            "progressive_vs_gr_satellites",
        ] {
            s[f] = aggregate(&rs, f);
        }
        strata.insert(name.into(), s);
    }
    report["strata"] = Value::Object(strata);
    if final_path.exists() {
        let final_id = id(final_path)?;
        let final_report = read(final_path)?;
        if final_report["selected"] != selected.len()
            || final_report["all_five_complete"] != complete.len()
        {
            return Err("final analysis coverage mismatch".into());
        }
        let verified = final_report["rows"]
            .as_array()
            .ok_or("final rows missing")?;
        for r in &complete {
            let v = verified
                .iter()
                .find(|v| v["id"] == r["id"])
                .ok_or("final observation missing")?;
            for f in ["primary", "innovation_vs_external"] {
                for key in ["baseline", "gain_count", "loss_count", "net"] {
                    if v[f][key] != r[f][key] {
                        return Err("final analysis disagrees with live score".into());
                    }
                }
            }
        }
        if id(final_path)? != final_id {
            return Err("final analysis changed".into());
        }
        report["final_analysis_crosschecked"] = true.into();
        report["final_analysis"] = final_id;
        report["verification"] =
            "checked against final paired analyzer; grouping remains metadata-based and post-hoc"
                .into();
    }
    Ok(report)
}
fn run() -> R<()> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 5 && !(args.len() == 6 && args[5] == "--watch") {
        return Err(
            "usage: signal_yield_track COHORT COMPARISON NEW_OUTPUT FINAL_YIELD [--watch]".into(),
        );
    }
    let cp = Path::new(&args[1]);
    let comparison = Path::new(&args[2]);
    let out = Path::new(&args[3]);
    let final_path = Path::new(&args[4]);
    let watch = args.len() == 6;
    let ci = id(cp)?;
    let cohort = read(cp)?;
    let mi = id(&comparison.join("manifest.json"))?;
    let manifest = read(&comparison.join("manifest.json"))?;
    if manifest["cohort"]["sha256"] != ci["sha256"]
        || cohort["selected_count"] != cohort["observations"].as_array().ok_or("rows")?.len()
    {
        return Err("cohort not bound to campaign".into());
    }
    let mut seen = BTreeSet::new();
    for r in cohort["observations"].as_array().unwrap() {
        if !seen.insert(r["id"].as_u64().ok_or("id")?) {
            return Err("duplicate cohort id".into());
        }
    }
    fs::create_dir(out)?;
    save(
        &out.join("registration.json"),
        &json!({"cohort":ci,"comparison_manifest":mi,"executable":id(&std::env::current_exe()?)?,"source":id(Path::new(file!()))?,"post_hoc":true,"definitions":"main=waterfall with-signal; good status and nonempty archival demoddata are separate overlapping sensitivity groups, not proof of valid CRC; unknown never mapped to absent","new_decoder_runs":0,"percent":"100*(ours-baseline_union)/baseline_union; null for zero baseline","deduplication":"exact PDU per observation; union of DireWolf and gr-satellites before comparison; no global archive novelty claim"}),
    )?;
    let mut previous = Value::Null;
    let mut sequence = 0;
    loop {
        if id(cp)? != ci || id(&comparison.join("manifest.json"))? != mi {
            return Err("frozen input changed".into());
        }
        let mut v = snapshot(&cohort, comparison, &mi, final_path)?;
        if v != previous {
            previous = v.clone();
            sequence += 1;
            v["sequence"] = sequence.into();
            v["unix_seconds"] = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_secs()
                .into();
            save(&out.join(format!("snapshot-{sequence:04}.json")), &v)?;
            replace(&out.join("latest.json"), &v)?;
        }
        replace(
            &out.join("status.json"),
            &json!({"state":if v["final_analysis_crosschecked"]==true{"finished"}else{"tracking"},"pid":std::process::id(),"last_sequence":sequence,"complete":v["complete"]}),
        )?;
        if !watch || v["final_analysis_crosschecked"] == true {
            break;
        }
        std::thread::sleep(Duration::from_secs(30));
    }
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("signal metrics failed: {e}");
        if let Some(out) = std::env::args().nth(3) {
            let p = Path::new(&out);
            if p.is_dir() {
                let _ = replace(
                    &p.join("status.json"),
                    &json!({"state":"failed","error":e.to_string()}),
                );
            }
        }
        std::process::exit(1)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn set(xs: &[&str]) -> Set {
        xs.iter().map(|x| x.to_string()).collect()
    }
    #[test]
    fn grouping_not_based_on_decoder_or_good_status() {
        let r = json!({"status":"good","waterfall_status":"unknown","demoddata":[]});
        assert!(!groups(&r).contains(&"waterfall_with_signal"));
        assert!(groups(&r).contains(&"archive_status_good"));
    }
    #[test]
    fn unknown_not_absent() {
        assert!(groups(&json!({})).contains(&"waterfall_unknown"));
        assert!(!groups(&json!({})).contains(&"waterfall_without_signal"));
    }
    #[test]
    fn archive_data_and_signal_groups_remain_separate() {
        let g = groups(&json!({"waterfall_status":"without-signal","demoddata":[{}]}));
        assert!(g.contains(&"archive_demoddata_present"));
        assert!(!g.contains(&"waterfall_with_signal"));
    }
    #[test]
    fn external_union_deduplicates() {
        let d = set(&["aa", "bb"]);
        let g = set(&["bb", "cc"]);
        let u = d.union(&g).cloned().collect();
        let v = delta(&set(&["aa", "dd"]), &u);
        assert_eq!(v["baseline"], 3);
        assert_eq!(v["gain_count"], 1);
        assert_eq!(v["loss_count"], 2);
        assert_eq!(v["net"], -1);
    }
    #[test]
    fn gain_is_ratio_of_sums_not_mean_of_percentages() {
        let r = vec![
            json!({"x":delta(&set(&["aa","bb"]),&set(&["aa"]))}),
            json!({"x":delta(&set(&["aa"]),&set(&["aa","bb","cc"]))}),
        ];
        assert_eq!(aggregate(&r, "x")["net_gain_percent"], -25.0);
    }
    #[test]
    fn zero_baseline_is_undefined_not_infinite() {
        let r = vec![json!({"x":delta(&set(&["aabb"]),&Set::new())})];
        let a = aggregate(&r, "x");
        assert!(a["net_gain_percent"].is_null());
        assert_eq!(a["added_pdu_bytes"], 2);
        assert!(aggregate(&[], "x")["net_gain_percent"].is_null());
    }
    #[test]
    fn reject_duplicate_and_noncanonical_pdus() {
        assert!(frames(&json!(["aa", "aa"])).is_err());
        assert!(frames(&json!(["AA"])).is_err());
        assert!(frames(&json!(["xx"])).is_err());
    }
    #[test]
    fn incomplete_not_zero() {
        let row = json!({"id":1});
        let r = json!({"observation_id":1,"row":row,"status":"incomplete"});
        assert!(score(&r, &row, &json!({})).unwrap().is_none());
    }
    #[test]
    fn mismatched_observation_rejected() {
        assert!(score(&json!({"observation_id":2}), &json!({"id":1}), &json!({})).is_err());
    }
}
