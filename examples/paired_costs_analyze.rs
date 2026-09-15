//! Read-only cost accounting: final arms and every retained attempt are separate.
//! Receipt consistency is not authenticated execution, receiver qualification or latency isolation.
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::input;

const ARMS: [&str; 5] = [
    "innovation_v2",
    "innovation_v1_no_codec",
    "progressive_v3",
    "direwolf",
    "gr_satellites",
];
const METRICS: [&str; 4] = [
    "wall_seconds",
    "user_cpu_seconds",
    "system_cpu_seconds",
    "maximum_rss_kib",
];
#[derive(Parser)]
struct Args {
    #[arg(long)]
    cohort: PathBuf,
    #[arg(long)]
    comparison: PathBuf,
    #[arg(long)]
    output: PathBuf,
}
#[derive(Default)]
struct Evidence {
    files: BTreeMap<PathBuf, Value>,
    directories: BTreeMap<PathBuf, Value>,
}
fn regular(path: &Path) -> Result<(), String> {
    if !path.is_absolute()
        || path.canonicalize().map_err(|e| e.to_string())? != path
        || !fs::symlink_metadata(path)
            .map_err(|e| e.to_string())?
            .is_file()
    {
        return Err(format!(
            "not an exact canonical regular file: {}",
            path.display()
        ));
    }
    Ok(())
}
fn identity(path: &Path) -> Result<Value, String> {
    regular(path)?;
    Ok(json!(input::identity(path)?))
}
fn same(a: &Value, b: &Value) -> bool {
    a["bytes"].is_u64()
        && a["sha256"].as_str().is_some_and(|s| s.len() == 64)
        && a["bytes"] == b["bytes"]
        && a["sha256"] == b["sha256"]
}
fn directory(path: &Path) -> Result<Value, String> {
    if !path.exists() {
        return Ok(Value::Null);
    }
    if path.canonicalize().map_err(|e| e.to_string())? != path
        || !fs::symlink_metadata(path)
            .map_err(|e| e.to_string())?
            .is_dir()
    {
        return Err("noncanonical or symlinked receipt directory".into());
    }
    let mut entries = BTreeMap::new();
    for item in fs::read_dir(path).map_err(|e| e.to_string())? {
        let item = item.map_err(|e| e.to_string())?;
        let kind = item.file_type().map_err(|e| e.to_string())?;
        let name = item
            .file_name()
            .into_string()
            .map_err(|_| "non-UTF8 receipt name")?;
        entries.insert(
            name,
            if kind.is_symlink() {
                "symlink"
            } else if kind.is_dir() {
                "directory"
            } else if kind.is_file() {
                "file"
            } else {
                "other"
            },
        );
    }
    Ok(json!(entries))
}
impl Evidence {
    fn file(&mut self, path: &Path) -> Result<Value, String> {
        let id = identity(path)?;
        if self.files.get(path).is_some_and(|old| old != &id) {
            return Err("artifact changed within audit".into());
        }
        self.files.insert(path.into(), id.clone());
        Ok(id)
    }
    fn verify(&mut self, id: &Value) -> Result<(), String> {
        let path = Path::new(id["path"].as_str().ok_or("identity path missing")?);
        if self.file(path)? != *id {
            return Err(format!(
                "artifact hash/size/path mismatch: {}",
                path.display()
            ));
        }
        Ok(())
    }
    fn bytes(&mut self, path: &Path) -> Result<(Vec<u8>, Value), String> {
        let id = self.file(path)?;
        let raw = input::read_bytes_bounded(path, 64 * 1024 * 1024)?;
        if id["bytes"].as_u64() != Some(raw.len() as u64)
            || id["sha256"] != hex::encode(Sha256::digest(&raw))
        {
            return Err("artifact changed during read".into());
        }
        Ok((raw, id))
    }
    fn doc(&mut self, path: &Path) -> Result<(Value, Value), String> {
        let (raw, id) = self.bytes(path)?;
        Ok((serde_json::from_slice(&raw).map_err(|e| e.to_string())?, id))
    }
    fn inventory(&mut self, path: &Path) -> Result<Value, String> {
        let value = directory(path)?;
        if self.directories.get(path).is_some_and(|v| v != &value) {
            return Err("attempt inventory changed during audit".into());
        }
        self.directories.insert(path.into(), value.clone());
        Ok(value)
    }
    fn recheck(&self) -> Result<(), String> {
        for (path, id) in &self.files {
            if identity(path)? != *id {
                return Err("input evidence changed before commit".into());
            }
        }
        for (path, entries) in &self.directories {
            if directory(path)? != *entries {
                return Err("attempt inventory changed before commit".into());
            }
        }
        Ok(())
    }
}
fn optional_number(value: &Value) -> Result<Option<f64>, String> {
    if value.is_null() {
        return Ok(None);
    }
    value
        .as_f64()
        .filter(|n| n.is_finite() && *n >= 0.0)
        .map(Some)
        .ok_or_else(|| "invalid negative/nonnumeric resource".into())
}
fn usage_field(raw: &str, key: &str) -> Result<Option<f64>, String> {
    let values: Vec<_> = raw
        .lines()
        .filter_map(|line| line.trim().strip_prefix(key))
        .collect();
    if values.len() > 1 {
        return Err("duplicate GNU time resource field".into());
    }
    values
        .first()
        .map(|v| {
            v.trim()
                .parse::<f64>()
                .map_err(|e| e.to_string())
                .and_then(|n| {
                    if n.is_finite() && n >= 0.0 {
                        Ok(n)
                    } else {
                        Err("nonfinite/negative GNU time value".into())
                    }
                })
        })
        .transpose()
}
fn program_for(arm: &str, manifest: &Value) -> Result<String, String> {
    if let Some(p) = manifest["receiver_bindings"][arm]["path"].as_str() {
        return Ok(p.into());
    }
    if arm == "innovation_v2" {
        return manifest["improved"]["path"]
            .as_str()
            .map(str::to_owned)
            .ok_or("improved executable absent".into());
    }
    let candidates: Vec<_> = manifest["runtime"]
        .as_array()
        .ok_or("runtime identities absent")?
        .iter()
        .filter_map(|v| v["path"].as_str())
        .filter(|p| match arm {
            "direwolf" => *p == "/usr/bin/atest",
            "gr_satellites" => p.ends_with("/gr_satellites"),
            "progressive_v3" => p.ends_with("/telemetry-yield-rs"),
            "innovation_v1_no_codec" => {
                p.ends_with("/innovation_audio_probe")
                    && Some(*p) != manifest["improved"]["path"].as_str()
            }
            _ => false,
        })
        .collect();
    if candidates.len() != 1 {
        return Err("executable cannot be uniquely associated with arm".into());
    }
    Ok(candidates[0].into())
}
fn expected_args(
    arm: &str,
    dir: &Path,
    pcm: &Value,
    source: &Value,
    manifest: &Value,
    comparison: &Path,
) -> Result<Value, String> {
    let wav = pcm["path"]
        .as_str()
        .ok_or("PCM path missing in retained record")?;
    let threads = manifest["threads"]
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or("threads absent")?
        .to_string();
    Ok(match arm {
        "direwolf" => json!(["-B", "9600", "-F", "0", "-h", wav]),
        "gr_satellites" => json!([
            comparison.join("grsat-profile.yml"),
            "--wavfile",
            wav,
            "--kiss_out",
            dir.join("frames.kiss"),
            "--hexdump"
        ]),
        "progressive_v3" => json!([
            "decode-progressive",
            "--input",
            wav,
            "--output",
            dir.join("decode"),
            "--threads",
            threads,
            "--mode",
            "full",
            "--budget-ms",
            manifest["decoder_seconds"]
                .as_u64()
                .filter(|n| *n > 20)
                .ok_or("process budget absent")?
                .saturating_sub(20)
                .saturating_mul(1000)
                .to_string()
        ]),
        "innovation_v1_no_codec" => json!([
            "--input",
            wav,
            "--output",
            dir.join("decode"),
            "--threads",
            threads
        ]),
        "innovation_v2" => json!([
            "--input",
            wav,
            "--output",
            dir.join("decode"),
            "--threads",
            threads,
            "--codec-sideinfo",
            "--pooled-codec",
            "--codec-source",
            source["path"].as_str().ok_or("OGG source path absent")?
        ]),
        _ => return Err("unknown arm".into()),
    })
}
fn metrics(process: &Value, usage: Option<&str>) -> Result<Value, String> {
    let mut value = json!({});
    for name in METRICS {
        value[name] = json!(optional_number(&process[name])?);
    }
    if let Some(raw) = usage {
        for (field, key) in [
            ("user_cpu_seconds", "User time (seconds):"),
            ("system_cpu_seconds", "System time (seconds):"),
            ("maximum_rss_kib", "Maximum resident set size (kbytes):"),
        ] {
            if optional_number(&process[field])? != usage_field(raw, key)? {
                return Err(format!(
                    "process {field} differs from hashed GNU time output"
                ));
            }
        }
    } else if METRICS[1..].iter().any(|key| !process[*key].is_null()) {
        return Err("CPU/RSS claimed without retained GNU time evidence".into());
    }
    Ok(value)
}
fn attempt(
    dir: &Path,
    id: u64,
    arm: &str,
    observation: Option<&Value>,
    manifest: &Value,
    manifest_id: &Value,
    comparison: &Path,
    evidence: &mut Evidence,
) -> Result<Value, String> {
    evidence.inventory(dir)?;
    let record_path = dir.join("result.json");
    let record = if record_path.exists() {
        Some(evidence.doc(&record_path)?)
    } else {
        None
    };
    let process_path = dir.join("run.process.json");
    let (process, process_id) = evidence.doc(&process_path)?;
    let context = record
        .as_ref()
        .map(|x| &x.0)
        .or(observation)
        .ok_or("no retained source/input context for uncommitted process")?;
    if let Some((r, _)) = &record {
        if !matches!(
            r["schema"].as_str(),
            Some("innovation-benchmark-arm-v2" | "innovation-benchmark-arm-v3")
        ) || r["arm"] != arm
            || r["manifest_sha256"] != manifest_id["sha256"]
            || r["process"] != process
        {
            return Err("arm record schema/label/manifest/process binding mismatch".into());
        }
        if let Some(obs) = observation {
            if !same(&r["input"], &obs["input"]) || !same(&r["source"], &obs["source"]) {
                return Err("attempt source/input differs from observation".into());
            }
        }
        let artifacts = r["artifacts"]
            .as_array()
            .ok_or("arm artifact list missing")?;
        if !artifacts.contains(&process_id) {
            return Err("actual process receipt was not committed as an artifact".into());
        }
        for a in artifacts {
            let p = Path::new(a["path"].as_str().ok_or("artifact path absent")?);
            if !p.starts_with(dir) {
                return Err("artifact escaped canonical owned attempt".into());
            }
            evidence.verify(a)?;
        }
    }
    if process["program"] != program_for(arm, manifest)?
        || process["args"]
            != expected_args(
                arm,
                dir,
                &context["input"],
                &context["source"],
                manifest,
                comparison,
            )?
    {
        return Err(
            "process not bound to arm, owned output, frozen resource options and input paths"
                .into(),
        );
    }
    for (field, file) in [("stdout", "run.stdout.log"), ("stderr", "run.stderr.log")] {
        if evidence.file(&dir.join(file))? != process[field] {
            return Err("process log escaped/mismatched owned attempt".into());
        }
    }
    let usage_path = dir.join("run.rusage.txt");
    if process["usage_path"] != json!(usage_path) {
        return Err("GNU time path escaped owned attempt".into());
    }
    let usage = if usage_path.exists() {
        let (raw, _) = evidence.bytes(&usage_path)?;
        Some(String::from_utf8(raw).map_err(|e| e.to_string())?)
    } else {
        None
    };
    let resources = metrics(&process, usage.as_deref())?;
    let timeout = process["timed_out"]
        .as_bool()
        .ok_or("timeout state missing")?;
    let success = process["success"]
        .as_bool()
        .ok_or("success state missing")?;
    let code = process["returncode"].as_i64();
    if success != (code == Some(0) && !timeout)
        || !process["returncode"].is_null() && code.is_none()
    {
        return Err("inconsistent process exit/timeout/success fields".into());
    }
    let category = if let Some((r, _)) = &record {
        if r["status"] == "complete" {
            if !success {
                return Err("complete arm has failed process".into());
            }
            "complete"
        } else if timeout {
            "timed_out"
        } else {
            "failed_or_incomplete"
        }
    } else {
        "uncommitted_process"
    };
    Ok(
        json!({"observation_id":id,"arm":arm,"attempt_directory":dir,"category":category,"verification":"consistent_owned_receipts_not_execution_authentication","resources_verified":true,"resources":resources,
        "record_identity":record.as_ref().map(|r|&r.1),"process_identity":process_id,"program":process["program"],"recorded_os_pid":process["pid"],"pid_missing_is_explicit":process["pid"].is_null(),
        "process_identifier_policy":"canonical run.process.json path plus SHA256/bytes; historical format does not record an OS PID",
        "timed_out":timeout,"cpu_usage_incomplete_on_timeout":timeout||process["cpu_usage_incomplete_on_timeout"]==true,"historically_committed_arm_artifacts":record.is_some(),"is_current_commit":false}),
    )
}
fn unavailable(id: u64, arm: &str, category: &str, error: &str) -> Value {
    json!({"observation_id":id,"arm":arm,"category":category,"resources_verified":false,"resources":null,"error":error})
}
fn aggregates(rows: &[Value]) -> Value {
    let mut counts = BTreeMap::<String, usize>::new();
    for r in rows {
        *counts
            .entry(r["category"].as_str().unwrap_or("unverified").into())
            .or_default() += 1;
    }
    let mut groups = json!({});
    for category in [
        "all",
        "complete",
        "failed_or_incomplete",
        "timed_out",
        "uncommitted_process",
        "unattempted",
        "unverified",
        "unfinalized",
    ] {
        let selected: Vec<_> = rows
            .iter()
            .filter(|r| category == "all" || r["category"] == category)
            .collect();
        let mut resources = json!({});
        for metric in METRICS {
            let values: Vec<_> = selected
                .iter()
                .filter(|r| r["resources_verified"] == true)
                .filter_map(|r| r["resources"][metric].as_f64())
                .collect();
            let incomplete = if metric == "wall_seconds" {
                0
            } else {
                selected
                    .iter()
                    .filter(|r| r["cpu_usage_incomplete_on_timeout"] == true)
                    .count()
            };
            let observed = if values.is_empty() {
                None
            } else if metric == "maximum_rss_kib" {
                values.iter().copied().reduce(f64::max)
            } else {
                Some(values.iter().sum::<f64>())
            };
            resources[metric] = json!({"operation":if metric=="maximum_rss_kib"{"maximum_not_sum"}else{"sum"},"observed_value":observed,"records":selected.len(),"verified_numeric_records":values.len(),"missing_or_unverified_records":selected.len()-values.len(),"timeout_or_incomplete_usage_records":incomplete,"complete_cost_coverage":!selected.is_empty()&&values.len()==selected.len()&&incomplete==0});
        }
        groups[category] = json!({"records":selected.len(),"resources":resources});
    }
    json!({"records":rows.len(),"category_counts":counts,"groups":groups})
}
fn analyze(a: &Args) -> Result<Value, String> {
    let comparison = a.comparison.canonicalize().map_err(|e| e.to_string())?;
    let cohort_path = a.cohort.canonicalize().map_err(|e| e.to_string())?;
    let mut evidence = Evidence::default();
    let (cohort, cohort_id) = evidence.doc(&cohort_path)?;
    let (manifest, manifest_id) = evidence.doc(&comparison.join("manifest.json"))?;
    if !matches!(
        manifest["schema"].as_str(),
        Some("innovation-benchmark-manifest-v2" | "innovation-benchmark-manifest-v3")
    ) || !same(&manifest["cohort"], &cohort_id)
    {
        return Err("cost cohort is not bound to supported comparison manifest".into());
    }
    evidence.verify(&manifest["cohort"])?;
    let rows = cohort["observations"]
        .as_array()
        .filter(|r| !r.is_empty() && r.len() <= 1000)
        .ok_or("cohort requires1..1000 observations")?;
    let ids: Vec<_> = rows
        .iter()
        .map(|r| {
            r["id"]
                .as_u64()
                .filter(|id| *id > 0)
                .ok_or("invalid observation ID")
        })
        .collect::<Result<_, _>>()?;
    if ids.iter().collect::<BTreeSet<_>>().len() != rows.len()
        || cohort["selected_count"].as_u64() != Some(rows.len() as u64)
    {
        return Err("cohort count or uniqueness mismatch".into());
    }
    let mut all = BTreeMap::<String, Vec<Value>>::new();
    let mut finals = BTreeMap::<String, Vec<Value>>::new();
    let mut observation_states = vec![];
    for row in rows {
        let id = row["id"].as_u64().unwrap();
        let obs_dir = comparison.join(format!("obs-{id}"));
        evidence.inventory(&obs_dir)?;
        let state_path = obs_dir.join("result.json");
        let observation = if state_path.exists() {
            let (v, _) = evidence.doc(&state_path)?;
            if v["observation_id"] != id || v["row"] != *row {
                return Err("observation receipt differs from selected metadata".into());
            }
            Some(v)
        } else {
            None
        };
        observation_states.push(json!({"id":id,"status":observation.as_ref().map(|v|&v["status"]),"not_a_zero_frame_result":true}));
        evidence.inventory(&obs_dir.join("arms"))?;
        for arm in ARMS {
            let root = obs_dir.join("arms").join(arm);
            let inventory = evidence.inventory(&root)?;
            let entries = inventory.as_object();
            let mut attempts = vec![];
            let mut problems = vec![];
            if let Some(entries) = entries {
                for (name, kind) in entries {
                    if name == "committed.json" && kind == "file" {
                        continue;
                    }
                    if !name.starts_with("attempt-") || kind != "directory" {
                        problems.push(format!("unrecognized arm entry {name}/{kind}"));
                        continue;
                    }
                    let dir = root.join(name);
                    let mut value = match attempt(
                        &dir,
                        id,
                        arm,
                        observation.as_ref(),
                        &manifest,
                        &manifest_id,
                        &comparison,
                        &mut evidence,
                    ) {
                        Ok(v) => v,
                        Err(e) => {
                            let mut v = unavailable(id, arm, "unverified", &e);
                            v["attempt_directory"] = json!(dir);
                            v
                        }
                    };
                    value["declared_arm_status"] = evidence
                        .doc(&dir.join("result.json"))
                        .ok()
                        .map(|(r, _)| r["status"].clone())
                        .unwrap_or(Value::Null);
                    attempts.push(value);
                }
            }
            let commit_path = root.join("committed.json");
            let selected = if commit_path.exists() {
                let checked = (|| -> Result<Value, String> {
                    let (link, _) = evidence.doc(&commit_path)?;
                    let p = Path::new(
                        link["record"]["path"]
                            .as_str()
                            .ok_or("commit record missing")?,
                    );
                    if p.file_name().and_then(|n| n.to_str()) != Some("result.json")
                        || p.parent().and_then(Path::parent) != Some(root.as_path())
                    {
                        return Err("commit escaped exact arm/attempt directory".into());
                    }
                    evidence.verify(&link["record"])?;
                    let (r, _) = evidence.doc(p)?;
                    let matching = attempts
                        .iter_mut()
                        .find(|v| v["record_identity"] == link["record"])
                        .ok_or("commit has no verified inventoried attempt")?;
                    if matching["resources_verified"] != true {
                        return Err("committed attempt cost evidence unverified".into());
                    }
                    matching["is_current_commit"] = true.into();
                    let mut final_record = matching.clone();
                    if let Some(obs) = &observation {
                        if obs["decoders"][arm] != r {
                            return Err(
                                "observation final arm differs from canonical commit".into()
                            );
                        }
                    } else {
                        final_record["category"] = "unfinalized".into();
                    }
                    Ok(final_record)
                })();
                match checked {
                    Ok(v) => v,
                    Err(e) => unavailable(id, arm, "unverified", &e),
                }
            } else if attempts.is_empty()
                && problems.is_empty()
                && observation
                    .as_ref()
                    .is_none_or(|v| v["decoders"][arm].is_null())
            {
                unavailable(
                    id,
                    arm,
                    "unattempted",
                    "no arm attempt directory or committed/final arm receipt in current local snapshot; no cost imputed",
                )
            } else {
                unavailable(
                    id,
                    arm,
                    "unverified",
                    "retained attempts/final arm without a validated canonical commit",
                )
            };
            let mut selected = selected;
            if !problems.is_empty() {
                selected = unavailable(id, arm, "unverified", &problems.join("; "));
            }
            selected["retained_attempt_count"] = json!(attempts.len());
            selected["inventory_problems"] = json!(problems);
            selected["declared_final_arm_status"] = observation
                .as_ref()
                .map(|r| r["decoders"][arm]["status"].clone())
                .unwrap_or(Value::Null);
            all.entry(arm.into()).or_default().extend(attempts);
            finals.entry(arm.into()).or_default().push(selected);
        }
    }
    evidence.recheck()?;
    let per_arm: BTreeMap<_,_>=ARMS.into_iter().map(|arm|(arm,json!({"final_arm_costs":aggregates(&finals[arm]),"all_retained_attempt_costs_including_retries":aggregates(&all[arm]),"final_arm_rows":finals[arm],"all_attempt_rows":all[arm]}))).collect();
    Ok(
        json!({"schema":"paired-costs-analysis-v1","status":"complete_cost_inventory_not_receiver_admission","analyzer":identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,"selected":rows.len(),"cohort":cohort_id,"comparison_manifest":manifest_id,"observations":observation_states,"per_arm":per_arm,
        "evidence":evidence.files.values().collect::<Vec<_>>(),"directory_snapshots":evidence.directories.iter().map(|(p,v)|json!({"path":p,"entries":v})).collect::<Vec<_>>(),
        "source_waveforms_read":0,"decoder_runs":0,"network_fetches":0,"publication_ready":false,"receiver_completion_revalidated":false,
        "scope":"Rehash retained owned process/results/log/artifact receipts; bind exact process command to arm/input/options; rederive user/system/RSS from GNU time where present. No source waveform or recovered frame scoring. No authenticated execution or full runtime qualification.",
        "missingness":"No missing cost is zero. Observed sums/maxima include verified numeric fields only; per-field missing counts and timeout-incomplete coverage remain explicit. An unattempted label means no local attempt/final evidence, not proof no historical execution ever occurred.",
        "timing":"Concurrent outer-process wall includes startup; summing concurrent wall is work accounting, not campaign elapsed time or isolated latency. PeakRSS is max, not sum. Acquisition/conversion not included.",
        "retry_policy":"Final committed arm and all retained attempts are reported separately. Uncommitted process snapshots remain their own category; unknown/corrupt attempt evidence is explicit and excluded from verified numeric totals."}),
    )
}
fn main() {
    let a = Args::parse();
    let result = analyze(&a).and_then(|v| input::write_json_new(&a.output, &v));
    if let Err(e) = result {
        eprintln!("cost analysis failed: {e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn sample(category: &str, wall: Value, cpu: Value) -> Value {
        json!({"category":category,"resources_verified":true,"resources":{"wall_seconds":wall,"user_cpu_seconds":cpu,"system_cpu_seconds":0.5,"maximum_rss_kib":100.0},"cpu_usage_incomplete_on_timeout":category=="timed_out"})
    }
    #[test]
    fn null_is_not_zero_and_rss_is_max() {
        let v = aggregates(&[
            sample("complete", json!(2), json!(1)),
            sample("failed_or_incomplete", Value::Null, Value::Null),
        ]);
        assert_eq!(
            v["groups"]["all"]["resources"]["wall_seconds"]["observed_value"],
            2.0
        );
        assert_eq!(
            v["groups"]["all"]["resources"]["wall_seconds"]["missing_or_unverified_records"],
            1
        );
        assert_eq!(
            v["groups"]["all"]["resources"]["maximum_rss_kib"]["observed_value"],
            100.0
        );
    }
    #[test]
    fn empty_and_unattempted_are_unknown_cost() {
        let v = aggregates(&[unavailable(1, "direwolf", "unattempted", "none")]);
        assert!(v["groups"]["all"]["resources"]["wall_seconds"]["observed_value"].is_null());
        assert_eq!(
            v["groups"]["all"]["resources"]["wall_seconds"]["complete_cost_coverage"],
            false
        );
    }
    #[test]
    fn timeout_resources_remain_explicit_partial() {
        let v = aggregates(&[sample("timed_out", json!(900), json!(30))]);
        assert_eq!(
            v["groups"]["timed_out"]["resources"]["user_cpu_seconds"]["complete_cost_coverage"],
            false
        );
        assert_eq!(
            v["groups"]["timed_out"]["resources"]["user_cpu_seconds"]["timeout_or_incomplete_usage_records"],
            1
        );
    }
    #[test]
    fn retries_are_not_final_costs() {
        let old = sample("timed_out", json!(900), Value::Null);
        let last = sample("complete", json!(10), json!(4));
        assert_eq!(
            aggregates(&[old, last.clone()])["groups"]["all"]["resources"]["wall_seconds"]["observed_value"],
            910.0
        );
        assert_eq!(
            aggregates(&[last])["groups"]["all"]["resources"]["wall_seconds"]["observed_value"],
            10.0
        );
    }
    #[test]
    fn negative_and_bad_numeric_metrics_reject() {
        assert!(optional_number(&json!(-1)).is_err());
        assert!(optional_number(&json!("3")).is_err());
        assert_eq!(optional_number(&Value::Null).unwrap(), None);
    }
    #[test]
    fn rusage_rederived_and_missing_not_invented() {
        let p = json!({"wall_seconds":2,"user_cpu_seconds":1,"system_cpu_seconds":0.5,"maximum_rss_kib":100});
        assert!(metrics(&p,Some("User time (seconds): 1\nSystem time (seconds): 0.5\nMaximum resident set size (kbytes): 100\n")).is_ok());
        assert!(metrics(&p,Some("User time (seconds): 2\nSystem time (seconds): 0.5\nMaximum resident set size (kbytes): 100\n")).is_err());
        assert!(metrics(&p, None).is_err());
        assert!(
            usage_field(
                "User time (seconds): 1\nUser time (seconds): 1",
                "User time (seconds):"
            )
            .is_err()
        );
    }
    #[test]
    fn changed_document_and_directory_reject() {
        let t = tempfile::tempdir().unwrap();
        let p = t.path().join("test.json");
        fs::write(&p, b"{}").unwrap();
        let mut e = Evidence::default();
        let (_, id) = e.doc(&p).unwrap();
        e.inventory(t.path()).unwrap();
        fs::write(&p, b"[]").unwrap();
        assert!(e.verify(&id).is_err());
        assert!(e.recheck().is_err());
    }
    fn put(path: &Path, value: &Value) -> Value {
        fs::write(path, serde_json::to_vec_pretty(value).unwrap()).unwrap();
        identity(path).unwrap()
    }
    fn fixture() -> (tempfile::TempDir, Args, PathBuf) {
        let t = tempfile::tempdir().unwrap();
        let comparison = t.path().join("comparison");
        fs::create_dir(&comparison).unwrap();
        let row = json!({"id":1});
        let cohort_path = t.path().join("cohort.json");
        let cohort_id = put(
            &cohort_path,
            &json!({"selected_count":1,"observations":[row]}),
        );
        let manifest = json!({"schema":"innovation-benchmark-manifest-v3","cohort":cohort_id,"threads":2,"decoder_seconds":900,"receiver_bindings":{"direwolf":{"path":"/usr/bin/atest"}}});
        let manifest_id = put(&comparison.join("manifest.json"), &manifest);
        let obs_dir = comparison.join("obs-1");
        let arm = obs_dir.join("arms/direwolf");
        fs::create_dir_all(&arm).unwrap();
        let pcm = json!({"path":obs_dir.join("deleted-owned-pcm.wav"),"sha256":"a".repeat(64),"bytes":100});
        let source =
            json!({"path":t.path().join("not-read.ogg"),"sha256":"b".repeat(64),"bytes":100});
        let mut last = Value::Null;
        let mut last_id = Value::Null;
        let mut last_dir = PathBuf::new();
        for (name, timeout) in [("attempt-first", true), ("attempt-last", false)] {
            let dir = arm.join(name);
            fs::create_dir(&dir).unwrap();
            fs::write(dir.join("run.stdout.log"), b"log").unwrap();
            fs::write(dir.join("run.stderr.log"), b"").unwrap();
            fs::write(dir.join("run.rusage.txt"),if timeout {""}else{"User time (seconds): 1\nSystem time (seconds): 0.5\nMaximum resident set size (kbytes): 100\n"}).unwrap();
            let process = json!({"program":"/usr/bin/atest","args":expected_args("direwolf",&dir,&pcm,&source,&manifest,&comparison).unwrap(),"success":!timeout,"returncode":if timeout{Value::Null}else{json!(0)},"timed_out":timeout,"wall_seconds":if timeout{900.0}else{2.0},"user_cpu_seconds":if timeout{Value::Null}else{json!(1.0)},"system_cpu_seconds":if timeout{Value::Null}else{json!(0.5)},"maximum_rss_kib":if timeout{Value::Null}else{json!(100.0)},"usage_path":dir.join("run.rusage.txt"),"stdout":identity(&dir.join("run.stdout.log")).unwrap(),"stderr":identity(&dir.join("run.stderr.log")).unwrap()});
            let pid = put(&dir.join("run.process.json"), &process);
            last = json!({"schema":"innovation-benchmark-arm-v3","arm":"direwolf","status":if timeout{"timeout"}else{"complete"},"source":source,"input":pcm,"process":process,"manifest_sha256":manifest_id["sha256"],"artifacts":[pid,identity(&dir.join("run.stdout.log")).unwrap(),identity(&dir.join("run.stderr.log")).unwrap(),identity(&dir.join("run.rusage.txt")).unwrap()]});
            last_id = put(&dir.join("result.json"), &last);
            last_dir = dir;
        }
        put(&arm.join("committed.json"), &json!({"record":last_id}));
        put(
            &obs_dir.join("result.json"),
            &json!({"observation_id":1,"row":row,"status":"incomplete","source":source,"input":pcm,"decoders":{"direwolf":last}}),
        );
        let args = Args {
            cohort: cohort_path,
            comparison,
            output: t.path().join("out.json"),
        };
        (t, args, last_dir)
    }
    #[test]
    fn actual_receipt_fixture_preserves_timeout_retry_and_unattempted() {
        let (_t, a, _) = fixture();
        let v = analyze(&a).unwrap();
        let dw = &v["per_arm"]["direwolf"];
        assert_eq!(
            dw["all_retained_attempt_costs_including_retries"]["records"],
            2
        );
        assert_eq!(
            dw["all_retained_attempt_costs_including_retries"]["groups"]["all"]["resources"]["wall_seconds"]
                ["observed_value"],
            902.0
        );
        assert_eq!(
            dw["final_arm_costs"]["groups"]["complete"]["resources"]["wall_seconds"]["observed_value"],
            2.0
        );
        assert_eq!(
            dw["all_retained_attempt_costs_including_retries"]["groups"]["timed_out"]["resources"]
                ["user_cpu_seconds"]["missing_or_unverified_records"],
            1
        );
        assert_eq!(
            v["per_arm"]["progressive_v3"]["final_arm_costs"]["category_counts"]["unattempted"],
            1
        );
        assert_eq!(v["source_waveforms_read"], 0);
    }
    #[test]
    fn mutated_log_is_unverified_not_zero_or_complete() {
        let (_t, a, dir) = fixture();
        fs::write(dir.join("run.stdout.log"), b"changed").unwrap();
        let v = analyze(&a).unwrap();
        assert_eq!(
            v["per_arm"]["direwolf"]["final_arm_costs"]["category_counts"]["unverified"],
            1
        );
        assert!(v["per_arm"]["direwolf"]["final_arm_costs"]["groups"]["all"]["resources"]["wall_seconds"]["observed_value"].is_null());
    }
    #[test]
    fn escaped_commit_is_unverified_and_valid_attempts_still_accounted() {
        let (t, a, dir) = fixture();
        let r: Value = serde_json::from_slice(&fs::read(dir.join("result.json")).unwrap()).unwrap();
        let wrong = put(&t.path().join("wrong.json"), &r);
        put(
            &dir.parent().unwrap().join("committed.json"),
            &json!({"record":wrong}),
        );
        let v = analyze(&a).unwrap();
        assert_eq!(
            v["per_arm"]["direwolf"]["final_arm_costs"]["category_counts"]["unverified"],
            1
        );
        assert_eq!(
            v["per_arm"]["direwolf"]["all_retained_attempt_costs_including_retries"]["groups"]["all"]
                ["resources"]["wall_seconds"]["observed_value"],
            902.0
        );
    }
    #[test]
    fn uncommitted_attempt_is_kept_separate() {
        let (_t, a, dir) = fixture();
        let orphan = dir.parent().unwrap().join("attempt-orphan");
        fs::create_dir(&orphan).unwrap();
        let v = analyze(&a).unwrap();
        assert_eq!(
            v["per_arm"]["direwolf"]["all_retained_attempt_costs_including_retries"]["records"],
            3
        );
        assert_eq!(
            v["per_arm"]["direwolf"]["all_retained_attempt_costs_including_retries"]["category_counts"]
                ["unverified"],
            1
        );
    }
    #[test]
    fn gnu_nan_does_not_panic() {
        assert!(usage_field("User time (seconds): NaN", "User time (seconds):").is_err());
    }
}
