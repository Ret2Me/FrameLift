//! Post-processing only: never used by the decoder or to select parameters.
use clap::Parser;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::{input, protocol};

type Frames = BTreeSet<String>;
#[derive(Parser)]
struct Args {
    #[arg(long)]
    root: PathBuf,
    #[arg(long)]
    output: PathBuf,
}
fn read(path: &Path) -> Result<Value, String> {
    serde_json::from_slice(&fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?)
        .map_err(|e| e.to_string())
}
fn arr(v: &Value) -> Result<&Vec<Value>, String> {
    v.as_array().ok_or("missing array".into())
}
fn frames(v: &Value) -> Result<Frames, String> {
    arr(v)?
        .iter()
        .map(|v| {
            let text = v.as_str().ok_or("non-string frame")?;
            let b = hex::decode(text).map_err(|e| e.to_string())?;
            if b.len() < 18
                || !protocol::valid_ax25_fcs(&b)
                || protocol::parse_ax25_ui(&b[..b.len() - 2]).is_none()
            {
                return Err("invalid received FCS or AX.25 UI structure in output".into());
            }
            Ok(hex::encode(b))
        })
        .collect()
}
fn difference(a: &Frames, b: &Frames) -> Frames {
    a.difference(b).cloned().collect()
}
fn pdu_bytes(a: &Frames) -> usize {
    a.iter().map(|s| s.len() / 2 - 2).sum()
}
fn count(v: &Value, key: &str) -> Result<usize, String> {
    v[key]
        .as_u64()
        .map(|n| n as usize)
        .ok_or(format!("missing count {key}"))
}
fn process_receipts(root: &Path, id: &str) -> Result<Value, String> {
    let directory = root.join("logs").join(id);
    let mut values = Vec::new();
    for n in 1..=2 {
        let attempt = directory.join(format!("attempt-{n}"));
        let path = attempt.join("receipt.json");
        if path.exists() {
            let receipt = read(&path)?;
            if let Some(h) = receipt["snapshot_sha256"].as_str() {
                if input::identity(&attempt.join("snapshot.json"))?.sha256 != h {
                    return Err("queue receipt snapshot changed".into());
                }
            }
            values.push(receipt);
        }
    }
    Ok(json!(values))
}
fn known_score(found: &Frames, t: &Value) -> Result<Value, String> {
    let expected = frames(&t["expected_frame_with_fcs_hex"])?;
    let sources = frames(&t["source_frame_with_fcs_hex"])?;
    let targets = frames(&t["target_frame_with_fcs_hex"])?;
    Ok(
        json!({"known_expected":expected.len(),"correct":found.intersection(&expected).count(),
        "sources_recovered":found.intersection(&sources).count(),"targets_recovered":found.intersection(&targets).count(),
        "missing_full_frames":difference(&expected,found),"unexpected_full_frames":difference(found,&expected)}),
    )
}
fn annotate_synthetic(
    progress: &mut Value,
    combined: &Frames,
    c: &Value,
    truth: &Value,
) -> Result<Value, String> {
    if c["kind"] != "synthetic-original-ogg" {
        return Ok(Value::Null);
    }
    let t = arr(&truth["cases"])?
        .iter()
        .find(|t| t["id"] == c["id"])
        .ok_or("missing synthetic truth")?;
    for (_, p) in progress.as_object_mut().ok_or("policy map")? {
        if let Some(f) = p.get("frame_with_fcs_hex") {
            let score = known_score(&frames(f)?, t)?;
            p["truth_score"] = score;
        }
    }
    known_score(combined, t)
}
fn progressive_results(root: &Path, c: &Value) -> Result<(Value, Frames), String> {
    let id = c["id"].as_str().ok_or("case id")?;
    let mut found_all = Frames::new();
    let mut policies = BTreeMap::<String, Frames>::new();
    let mut progress = BTreeMap::new();
    for policy in ["existing", "blind", "multi", "both"] {
        let dir = root.join("progressive").join(id).join(policy);
        let p = dir.join("result.json");
        let mut receipts = BTreeMap::new();
        for phase in ["quick", "deep", "full"] {
            receipts.insert(
                phase,
                process_receipts(root, &format!("progressive-{id}-{policy}-{phase}"))?,
            );
        }
        if !p.exists() {
            progress.insert(
                policy,
                json!({"status":"no-snapshot","phase_receipts":receipts}),
            );
            continue;
        }
        let plan = read(&root.join("cases").join(id).join("plan.json"))?;
        let crop = root.join("cases").join(id).join("shared-crop.wav");
        let identity = serde_json::to_value(input::identity(&crop)?).map_err(|e| e.to_string())?;
        let manifest_path = dir.join("manifest.json");
        let manifest = read(&manifest_path)?;
        let v = read(&p)?;
        if plan["source"]["sha256"] != c["sha256"]
            || plan["crop_start_sample"] != c["crop_start_sample"]
            || plan["crop_end_sample"] != c["crop_end_sample"]
            || plan["shared_crop_wav"] != identity
            || plan["shared_crop_roundtrip_bit_exact"] != true
            || manifest["audio"]["source"] != identity
            || manifest["audio"]["sample_rate"] != c["sample_rate_hz"]
            || manifest["policy"]["baud"].as_f64() != Some(9600.0)
            || manifest["policy"]["blind"] != matches!(policy, "blind" | "both")
            || manifest["policy"]["multi_anchor"] != matches!(policy, "multi" | "both")
            || manifest["executable_sha256"]
                != input::identity(&root.join("bin/telemetry-yield-rs"))?.sha256
            || v["session_sha256"] != input::identity(&manifest_path)?.sha256
        {
            return Err(format!(
                "{id}/{policy}: progressive input/session/policy binding mismatch"
            ));
        }
        let found = frames(&v["frame_with_fcs_hex"])?;
        let tasks = count(&v, "completed_tasks")?;
        let total = count(&v, "total_tasks")?;
        let complete = v["complete"]
            .as_bool()
            .ok_or("progressive completeness absent")?;
        if tasks > total || complete != (tasks == total) || count(&v, "union_count")? != found.len()
        {
            return Err("progressive task/frame count mismatch".into());
        }
        progress.insert(
            policy,
            json!({"frames":found.len(),"frame_with_fcs_hex":found,"complete":complete,"completed_tasks":tasks,
            "total_tasks":total,"snapshot":input::identity(&p)?,"input_and_policy_verified":true,"phase_receipts":receipts}),
        );
        found_all.extend(found.iter().cloned());
        policies.insert(policy.into(), found);
    }
    if let Some(existing) = policies.get("existing") {
        for (policy, found) in &policies {
            progress.get_mut(policy.as_str()).unwrap()["added_vs_existing"] =
                json!(difference(found, existing));
            progress.get_mut(policy.as_str()).unwrap()["lost_vs_existing"] =
                json!(difference(existing, found));
        }
    }
    Ok((json!(progress), found_all))
}
fn score(root: &Path) -> Result<Value, String> {
    let cohort = read(&root.join("dataset/cohort.json"))?;
    let truth = read(&root.join("dataset/scoring-truth.json"))?;
    let terminal =
        root.join("queue.complete.json").exists() || root.join("queue.deadline.json").exists();
    let mut rows = Vec::new();
    let mut global_ref = Frames::new();
    let mut global_union = Frames::new();
    let mut group_stats = BTreeMap::<String, Value>::new();
    for c in arr(&cohort["cases"])? {
        let id = c["id"].as_str().ok_or("case id")?;
        let out = root.join("cases").join(id);
        let mut row = json!({"id":id,"role":c["role"],"kind":c["kind"],"impairment":c["impairment"],
            "historical_signal_label":c["waterfall_status"],"historical_stratum":c["difficulty_stratum"],
            "status":if terminal {"not-completed"} else {"pending"}});
        row["process_receipts"] = process_receipts(root, &format!("robust-{id}"))?;
        if !out.join("summary.json").exists() {
            let (mut progress, available) = progressive_results(root, c)?;
            row["available_progressive_truth_score"] =
                annotate_synthetic(&mut progress, &available, c, &truth)?;
            row["progressive"] = progress;
            row["available_progressive_frames"] = json!(available.len());
            row["robust_reference_available"] = json!(false);
            rows.push(row);
            continue;
        }
        let summary = read(&out.join("summary.json"))?;
        let plan = read(&out.join("plan.json"))?;
        let r = read(&out.join("report.json"))?;
        if summary["status"] != "complete"
            || plan["schema"] != "robust-audio-cpu-pilot-v1"
            || plan["source"]["sha256"] != c["sha256"]
            || plan["source"]["path"] != c["source"]
            || plan["crop_start_sample"] != c["crop_start_sample"]
            || plan["crop_end_sample"] != c["crop_end_sample"]
            || plan["shared_crop_roundtrip_bit_exact"] != true
            || plan["baud"].as_f64() != Some(9600.0)
            || plan["threads"] != 2
            || plan["metrics"][0]["lane"] != "huber-2"
            || plan["metrics"][0]["delta"].as_f64() != Some(2.0)
            || plan["metrics"][1]["lane"] != "student-t-3"
            || plan["metrics"][1]["nu"].as_f64() != Some(3.0)
            || plan["executable"]["sha256"]
                != input::identity(&root.join("bin/robust_audio_probe"))?.sha256
        {
            return Err(format!("{id}: invalid plan/completion provenance"));
        }
        let crop = out.join("shared-crop.wav");
        if serde_json::to_value(input::identity(&crop)?).map_err(|e| e.to_string())?
            != plan["shared_crop_wav"]
        {
            return Err(format!("{id}: shared PCM changed"));
        }
        let reference = frames(&r["reference"]["union_full_frames"])?;
        let point = frames(&r["lane_full_frames"]["matched-point"])?;
        let mut union = reference.clone();
        let mut lanes = BTreeMap::new();
        for name in ["huber-2", "student-t-3"] {
            let lane = frames(&r["lane_full_frames"][name])?;
            let added = difference(&lane, &reference);
            let lost = difference(&point, &lane);
            if frames(&r["added_vs_reference"][name])? != added
                || frames(&r["lost_vs_matched_point"][name])? != lost
                || frames(&r["added_vs_matched_point"][name])? != difference(&lane, &point)
            {
                return Err(format!("{id}: inconsistent set arithmetic"));
            }
            lanes.insert(name,json!({"frames":lane.len(),"added_vs_reference":added.len(),"added_full_frames":added,
                "added_vs_point":difference(&lane,&point).len(),"lost_vs_point":lost.len(),"lost_full_frames":lost}));
            union.extend(lane);
        }
        if frames(&r["union_full_frames"])? != union
            || count(&summary, "union_frames")? != union.len()
            || count(&summary, "reference_frames")? != reference.len()
        {
            return Err(format!("{id}: inconsistent union"));
        }
        let execution_status = row["process_receipts"]
            .as_array()
            .and_then(|a| a.last())
            .and_then(|r| r["status"].as_str())
            .unwrap_or("not-recorded")
            .to_owned();
        row["raw_result_complete"] = json!(true);
        row["execution_status"] = json!(execution_status);
        let execution_confirmed =
            matches!(execution_status.as_str(), "complete" | "skipped_complete");
        row["status"] = json!(if execution_confirmed {
            "complete"
        } else {
            "result-present-execution-unconfirmed"
        });
        row["reference_frames"] = json!(reference.len());
        row["reference_pdu_bytes"] = json!(pdu_bytes(&reference));
        row["union_frames"] = json!(union.len());
        row["union_pdu_bytes"] = json!(pdu_bytes(&union));
        row["added_frames"] = json!(difference(&union, &reference).len());
        row["lanes"] = json!(lanes);
        row["source_models"] = json!(arr(&r["sources"])?.len());
        row["trials"] = json!(arr(&r["trials"])?.len());
        row["exercised"] = json!(!arr(&r["trials"])?.is_empty());
        row["decode_seconds"] = summary["decode_seconds"].clone();
        row["confirmed_by_current_reference_decode"] = json!(!reference.is_empty());
        let synthetic = c["kind"] == "synthetic-original-ogg";
        if synthetic {
            let t = arr(&truth["cases"])?
                .iter()
                .find(|t| t["id"] == id)
                .ok_or("missing truth")?;
            let expected = frames(&t["expected_frame_with_fcs_hex"])?;
            let targets = frames(&t["target_frame_with_fcs_hex"])?;
            let sources = frames(&t["source_frame_with_fcs_hex"])?;
            row["expected_frames"] = json!(expected.len());
            row["correct_frames"] = json!(union.intersection(&expected).count());
            row["source_frames_recovered"] = json!(union.intersection(&sources).count());
            row["target_frames_recovered"] = json!(union.intersection(&targets).count());
            row["reference_targets_recovered"] = json!(reference.intersection(&targets).count());
            row["unexpected_full_frames"] = json!(difference(&union, &expected));
            row["missing_full_frames"] = json!(difference(&expected, &union));
        } else if execution_confirmed {
            global_ref.extend(reference.iter().cloned());
            global_union.extend(union.iter().cloned());
        }

        let (mut progress, mut all) = progressive_results(root, c)?;
        all.extend(union.iter().cloned());
        row["robust_plus_available_progressive_truth_score"] =
            annotate_synthetic(&mut progress, &all, c, &truth)?;
        row["progressive"] = progress;
        row["robust_plus_available_progressive_union"] = json!(all.len());
        if let Some(t) = arr(&truth["regression_crop_known_frames"])?
            .iter()
            .find(|t| t["case_id"] == id)
        {
            let expected = frames(&t["known_progressive_missing_frame_with_fcs_hex"])?;
            row["historical_regression_targets"] = json!({"known":expected.len(),
                "recovered_by_robust":union.intersection(&expected).count(),
                "recovered_by_combined":all.intersection(&expected).count(),"still_missing":difference(&expected,&all),
                "scope":"historical PCM16 targets vs new float32; not physical truth or identical-input external benchmark"});
        }
        rows.push(row);
    }
    for label in [
        "synthetic_development",
        "synthetic_fixed_validation",
        "preserved_real_development",
        "known-regression-challenge-only",
        "real_reference_positive",
        "real_historical_signal_label",
    ] {
        let members: Vec<_> = rows
            .iter()
            .filter(|r| {
                if label == "real_reference_positive" {
                    r["kind"] != "synthetic-original-ogg"
                        && r["confirmed_by_current_reference_decode"] == true
                } else if label == "real_historical_signal_label" {
                    r["kind"] != "synthetic-original-ogg"
                        && r["historical_signal_label"] == "with-signal"
                } else {
                    r["role"] == label
                }
            })
            .collect();
        let complete: Vec<_> = members
            .iter()
            .filter(|r| r["status"] == "complete")
            .collect();
        let sum = |key: &str| {
            complete
                .iter()
                .map(|r| r[key].as_u64().unwrap_or(0))
                .sum::<u64>()
        };
        group_stats.insert(label.into(),json!({"selected_or_conditional_cases":members.len(),"completed":complete.len(),
            "exercised_cases":complete.iter().filter(|r|r["exercised"]==true).count(),
            "reference_frames":sum("reference_frames"),"union_frames":sum("union_frames"),
            "added_frames":sum("added_frames"),"reference_pdu_bytes":sum("reference_pdu_bytes"),"union_pdu_bytes":sum("union_pdu_bytes"),
            "known_expected_frames_completed_cases":sum("expected_frames"),"correct_frames":sum("correct_frames"),
            "target_frames_recovered":sum("target_frames_recovered"),"reference_targets_recovered":sum("reference_targets_recovered"),
            "unexpected_frames":complete.iter().map(|r|r["unexpected_full_frames"].as_array().map_or(0,Vec::len)).sum::<usize>()}));
    }
    Ok(
        json!({"schema":"overnight-recovery-score-v1","publication_ready":false,"physical_100_percent_claim":false,
        "selected_cases":rows.len(),"completed_cases":rows.iter().filter(|r|r["status"]=="complete").count(),
        "queue_terminal_marker":terminal,"groups":group_stats,
        "global_real_completed_robust_scope":{"reference":global_ref.len(),"union":global_union.len(),
            "new_unique_frames":difference(&global_union,&global_ref).len(),"new_pdu_bytes":pdu_bytes(&difference(&global_union,&global_ref))},
        "metric_notes":["missing/failed cases never enter recovery denominator as successful zero",
            "confirmed-positive is conditional on current reference FCS-valid decode, not independent population selection",
            "progressive counts may be partial, paired inputs but not guaranteed equal completed work",
            "global and per-recording deduplication are different metrics; no physical transmitter truth for field cases"],"rows":rows}),
    )
}
fn main() {
    let args = Args::parse();
    let result=score(&args.root).and_then(|v| {
        // This is a replaceable derived summary; raw reports remain immutable.
        let temp=args.output.with_extension(format!("tmp-{}",std::process::id()));
        input::write_json_new(&temp,&v)?;
        fs::rename(&temp,&args.output).map_err(|e|e.to_string())?;
        println!("{}",json!({"selected":v["selected_cases"],"completed":v["completed_cases"],"output":args.output}));
        Ok(())
    });
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn malformed_and_missing_results_are_not_empty_frames() {
        assert!(frames(&Value::Null).is_err());
        assert!(frames(&json!(["001122"])).is_err());
        assert!(frames(&json!([])).unwrap().is_empty());
    }
    #[test]
    fn set_differences_keep_equal_count_but_different_bytes() {
        let a = Frames::from(["a".into(), "b".into()]);
        let b = Frames::from(["b".into(), "c".into()]);
        assert_eq!(difference(&a, &b), Frames::from(["a".into()]));
        assert_eq!(difference(&b, &a), Frames::from(["c".into()]));
    }
    #[test]
    fn progressive_truth_keeps_unexpected_valid_frames_visible() {
        fn packet(n: u8) -> String {
            let mut b = hex::decode("94a662b2a0826094a662b29eb2e103f0").unwrap();
            b.push(n);
            let crc = protocol::crc16_x25(&b);
            b.extend(crc.to_le_bytes());
            hex::encode(b)
        }
        let s1 = packet(1);
        let s2 = packet(2);
        let target = packet(3);
        let unexpected = packet(4);
        let found = Frames::from([target.clone(), unexpected.clone()]);
        let truth = json!({"cases":[{"id":"x","expected_frame_with_fcs_hex":[s1,s2,target],
            "source_frame_with_fcs_hex":[packet(1),packet(2)],"target_frame_with_fcs_hex":[packet(3)]}]});
        let mut progress =
            json!({"both":{"frame_with_fcs_hex":found},"existing":{"status":"no-snapshot"}});
        let combined = annotate_synthetic(
            &mut progress,
            &found,
            &json!({"id":"x","kind":"synthetic-original-ogg"}),
            &truth,
        )
        .unwrap();
        assert_eq!(combined["correct"], 1);
        assert_eq!(combined["targets_recovered"], 1);
        assert_eq!(combined["unexpected_full_frames"], json!([unexpected]));
        assert_eq!(progress["both"]["truth_score"], combined);
        assert!(progress["existing"]["truth_score"].is_null());
    }
}
