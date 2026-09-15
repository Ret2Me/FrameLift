//! Paired executable replays on identical PCM, with exact deterministic JSON
//! comparisons (only measured wall times removed). Not a decoder benchmark
//! against another product, nor an assumption of otherwise idle hardware.
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Instant,
};
use telemetry_yield_rs::input;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    before: PathBuf,
    #[arg(long)]
    after: PathBuf,
    #[arg(long)]
    manifest: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 2)]
    repeats: usize,
    #[arg(long, default_value_t = 4)]
    threads: usize,
    #[arg(long, default_value = "4-7")]
    cpus: String,
}

// Do not remove provenance, counts, model floats, hypotheses, skips, failures,
// frame bytes, ordering or multiplicity. serde_json's float_roundtrip feature
// preserves parsed f64 values; canonical serialization also distinguishes -0.0.
fn deterministic(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for key in [
                "elapsed_seconds",
                "stage_wall_seconds",
                "total_wall_seconds",
            ] {
                map.remove(key);
            }
            for child in map.values_mut() {
                deterministic(child);
            }
        }
        Value::Array(items) => {
            for child in items {
                deterministic(child);
            }
        }
        _ => {}
    }
}

fn read(path: &Path) -> Result<Value, Box<dyn std::error::Error>> {
    Ok(serde_json::from_reader(fs::File::open(path)?)?)
}

fn run(args: &Args) -> Result<(), Box<dyn std::error::Error>> {
    if args.repeats == 0 || args.repeats > 10 || !(1..=16).contains(&args.threads) {
        return Err("invalid repeat/thread budget".into());
    }
    let manifest = read(&args.manifest)?;
    let inputs = manifest
        .as_array()
        .filter(|items| !items.is_empty())
        .ok_or("manifest must be a nonempty array")?;
    for item in inputs {
        item["id"].as_u64().ok_or("missing id")?;
        let mut file = fs::File::open(item["input"].as_str().ok_or("missing input")?)?;
        let mut magic = [0u8; 12];
        file.read_exact(&mut magic)?;
        if &magic[..4] != b"RIFF" || &magic[8..] != b"WAVE" {
            return Err("paired timing requires preconverted RIFF/WAVE input".into());
        }
    }
    let before = fs::canonicalize(&args.before)?;
    let after = fs::canonicalize(&args.after)?;
    let output = input::existing_new_dir(&args.output)?;
    input::write_json_new(
        &output.join("plan.json"),
        &json!({
            "schema":"lossless-paired-replay-plan-v1", "before":input::identity(&before)?,
            "after":input::identity(&after)?, "manifest":manifest, "repeats":args.repeats,
            "threads":args.threads,"cpu_affinity":args.cpus,"order":"AB in even (repeat+input-index), BA in odd",
            "excluded_comparison_keys":["elapsed_seconds","stage_wall_seconds","total_wall_seconds"],
            "whole_result_comparison":true,"background_host_workload_excluded":false,
            "conversion_in_timed_region":false,"timed_region":"entire decoder subprocess including PCM read, hash, serialization"
        }),
    )?;
    let mut rows = Vec::new();
    for repeat in 0..args.repeats {
        for (index, item) in inputs.iter().enumerate() {
            let id = item["id"].as_u64().ok_or("missing id")?;
            let wav = fs::canonicalize(item["input"].as_str().ok_or("missing input")?)?;
            let identity = input::identity(&wav)?;
            let order = if (repeat + index) % 2 == 0 {
                ["before", "after"]
            } else {
                ["after", "before"]
            };
            let mut pair = serde_json::Map::new();
            let mut projections = Vec::new();
            for arm in order {
                let prefix = format!("r{repeat}-{id}-{arm}");
                let metrics = output.join(format!("{prefix}.time.txt"));
                let run_dir = output.join(&prefix);
                let stdout = fs::File::create_new(output.join(format!("{prefix}.stdout.txt")))?;
                let stderr = fs::File::create_new(output.join(format!("{prefix}.stderr.txt")))?;
                let started = Instant::now();
                let status = Command::new("taskset")
                    .args(["-c", &args.cpus])
                    .arg("/usr/bin/time")
                    .args(["-f", "%e %U %S %M", "-o"])
                    .arg(&metrics)
                    .arg(if arm == "before" { &before } else { &after })
                    .arg("decode-adaptive-audio")
                    .arg("--input")
                    .arg(&wav)
                    .arg("--output")
                    .arg(&run_dir)
                    .arg("--observation-id")
                    .arg(id.to_string())
                    .arg("--threads")
                    .arg(args.threads.to_string())
                    .stdin(Stdio::null())
                    .stdout(stdout)
                    .stderr(stderr)
                    .status()?;
                let wall = started.elapsed().as_secs_f64();
                if !status.success() {
                    return Err(format!("{prefix} failed: {status}").into());
                }
                let mut result = read(&run_dir.join("result.json"))?;
                if result["status"] != "complete" {
                    return Err(format!("{prefix} incomplete").into());
                }
                if result["input"] != serde_json::to_value(&identity)? {
                    return Err(format!("{prefix} input identity changed since preflight").into());
                }
                let times = fs::read_to_string(&metrics)?;
                let times: Vec<f64> = times
                    .split_whitespace()
                    .map(str::parse)
                    .collect::<Result<_, _>>()?;
                if times.len() != 4 {
                    return Err("unexpected GNU time result".into());
                }
                pair.insert(arm.into(), json!({
                    "process_wall_seconds":wall,"user_seconds":times[1],"system_seconds":times[2],
                    "maximum_rss_kib":times[3],"stage_wall_seconds":result["report"]["stage_wall_seconds"],
                    "union_count":result["union_count"], "input_sha256":result["input"]["sha256"],
                    "result":run_dir.join("result.json")
                }));
                deterministic(&mut result);
                let encoded = serde_json::to_vec(&result)?;
                pair.get_mut(arm).unwrap()["deterministic_result_sha256"] =
                    json!(hex::encode(Sha256::digest(&encoded)));
                projections.push(encoded);
            }
            let equal = projections[0] == projections[1];
            let row = json!({"repeat":repeat,"id":id,"order":order,"input":identity,
                "exact_deterministic_result_equal":equal,"arms":pair});
            input::write_json_new(
                &output.join(format!("r{repeat}-{id}-comparison.json")),
                &row,
            )?;
            println!(
                "id={id} repeat={repeat} exact_equal={equal} before={:.3}s after={:.3}s",
                row["arms"]["before"]["process_wall_seconds"]
                    .as_f64()
                    .unwrap(),
                row["arms"]["after"]["process_wall_seconds"]
                    .as_f64()
                    .unwrap()
            );
            rows.push(row);
            if !equal {
                return Err(
                    format!("exact deterministic result mismatch: {id}, repeat {repeat}").into(),
                );
            }
        }
    }
    let sum = |arm: &str| {
        rows.iter()
            .map(|r| r["arms"][arm]["process_wall_seconds"].as_f64().unwrap())
            .sum::<f64>()
    };
    input::write_json_new(
        &output.join("summary.json"),
        &json!({
            "schema":"lossless-paired-replay-result-v1","status":"complete","all_exact_equal":true,
            "pairs":rows.len(),"before_total_process_seconds":sum("before"),"after_total_process_seconds":sum("after"),
            "speedup_ratio_of_sums":sum("before")/sum("after"),"rows":rows,
            "universal_equivalence_proven_by_finite_replays":false
        }),
    )?;
    Ok(())
}

fn main() {
    if let Err(error) = run(&Args::parse()) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn projection_keeps_provenance_and_models_not_only_frame_counts() {
        let mut a = json!({"total_wall_seconds":1,"report":{"stage_wall_seconds":{},
            "trials":[{"elapsed_seconds":1,"anchor_id":"a","frames":["01"],"gain":-0.0}]}});
        let mut b = a.clone();
        b["total_wall_seconds"] = json!(2);
        b["report"]["trials"][0]["elapsed_seconds"] = json!(3);
        deterministic(&mut a);
        deterministic(&mut b);
        assert_eq!(
            serde_json::to_vec(&a).unwrap(),
            serde_json::to_vec(&b).unwrap()
        );
        b["report"]["trials"][0]["gain"] = json!(0.0);
        assert_ne!(
            serde_json::to_vec(&a).unwrap(),
            serde_json::to_vec(&b).unwrap()
        );
        b = a.clone();
        b["report"]["trials"][0]["anchor_id"] = json!("b");
        assert_ne!(
            serde_json::to_vec(&a).unwrap(),
            serde_json::to_vec(&b).unwrap()
        );
    }
}
