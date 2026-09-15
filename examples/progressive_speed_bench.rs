//! Paired, version-pinned full-progressive replay. No download or cohort choice.
use clap::Parser;
use serde_json::{Value, json};
use std::{
    fs::{self, File},
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
    comparator: PathBuf,
    #[arg(long, required = true)]
    input: Vec<PathBuf>,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 2)]
    repeats: usize,
    #[arg(long, default_value_t = 2)]
    threads: usize,
    #[arg(long)]
    cpus: Option<String>,
}

fn require(ok: bool, reason: &str) -> Result<(), String> {
    if ok { Ok(()) } else { Err(reason.into()) }
}

fn pinned(path: &Path, identity: &input::Identity) -> Result<(), String> {
    let now = input::identity(path)?;
    require(
        now.sha256 == identity.sha256 && now.bytes == identity.bytes,
        "file identity changed during benchmark",
    )
}

fn admit(report: &Value, before: &str, after: &str) -> Result<(), String> {
    require(
        matches!(report["status"].as_str(), Some("equal" | "different")),
        "session comparison invalid",
    )?;
    let checks = report["identity_checks"]
        .as_object()
        .ok_or("identity checks missing")?;
    let expected = [
        "same_policy",
        "same_executable_sha256",
        "same_source_sha256_and_bytes",
        "same_wav_sha256_and_bytes",
        "same_sample_metadata",
        "same_conversion_presence",
    ];
    require(
        checks.len() == expected.len() && expected.iter().all(|k| checks.contains_key(*k)),
        "unexpected identity check schema",
    )?;
    require(
        expected
            .iter()
            .filter(|k| **k != "same_executable_sha256")
            .all(|k| checks[*k] == true),
        "non-executable identity differs",
    )?;
    require(
        checks["same_executable_sha256"] == json!(before == after),
        "binary identity comparison inconsistent",
    )?;
    for key in [
        "audio_files_rehashed_and_wav_header_checked",
        "every_task_commit_sha256_verified",
        "complete_snapshot_and_task_universe_verified",
        "independent_bitwise_received_fcs_and_ui_verified",
    ] {
        require(
            report[key] == true,
            "required independent session check absent",
        )?;
    }
    for key in [
        "tasks_only_left",
        "tasks_only_right",
        "different_tasks",
        "frames_only_left",
        "frames_only_right",
    ] {
        require(
            report[key].as_array().is_some_and(Vec::is_empty),
            "deterministic decoder outputs differ",
        )?;
    }
    let tasks = report["left_task_count"]
        .as_u64()
        .ok_or("task count absent")?;
    require(
        tasks > 0 && report["right_task_count"].as_u64() == Some(tasks),
        "empty or unmatched completed task bank",
    )
}

fn execute(args: &Args, binary: &Path, directory: &Path, wav: &Path) -> Result<Value, String> {
    fs::create_dir(directory).map_err(|e| e.to_string())?;
    let session = directory.join("session");
    let usage = directory.join("usage.txt");
    let mut command = if let Some(cpus) = &args.cpus {
        let mut c = Command::new("/usr/bin/taskset");
        c.args(["-c", cpus, "/usr/bin/time"]);
        c
    } else {
        Command::new("/usr/bin/time")
    };
    command
        .env("LC_ALL", "C")
        .args(["-f", "%e %U %S %M", "-o"])
        .arg(&usage)
        .arg(binary)
        .arg("decode-progressive")
        .arg("--input")
        .arg(wav)
        .arg("--output")
        .arg(&session)
        .args(["--mode", "full", "--threads"])
        .arg(args.threads.to_string())
        .stdin(Stdio::null())
        .stdout(File::create(directory.join("stdout.log")).map_err(|e| e.to_string())?)
        .stderr(File::create(directory.join("stderr.log")).map_err(|e| e.to_string())?);
    let argv = format!("{command:?}");
    let started = Instant::now();
    let status = command.status().map_err(|e| e.to_string())?;
    let outer_wall = started.elapsed().as_secs_f64();
    // Keep the attempt receipt even on process failure; never call it zero frames.
    let receipt = json!({"command":argv,"exit_code":status.code(),"outer_wall_seconds":outer_wall,"session":session});
    input::write_json_new(&directory.join("process.json"), &receipt)?;
    require(
        status.success(),
        "decoder process failed; attempt evidence retained",
    )?;
    let snapshot = input::read_json(&session.join("result.json"))?;
    require(
        snapshot["complete"] == true && snapshot["status"] == "complete",
        "full receiver did not complete",
    )?;
    let text = fs::read_to_string(&usage).map_err(|e| e.to_string())?;
    let fields: Vec<_> = text.split_whitespace().collect();
    require(fields.len() == 4, "invalid GNU time record")?;
    let numbers: Vec<f64> = fields
        .iter()
        .map(|f| f.parse::<f64>().map_err(|e| e.to_string()))
        .collect::<Result<_, _>>()?;
    require(
        numbers.iter().all(|n| n.is_finite() && *n >= 0.0),
        "invalid measured cost",
    )?;
    Ok(
        json!({"session":session,"outer_wall_seconds":outer_wall,"gnu_wall_seconds":numbers[0],"user_cpu_seconds":numbers[1],"system_cpu_seconds":numbers[2],"max_rss_kib":numbers[3],"frames":snapshot["union_count"],"tasks":snapshot["completed_tasks"],"usage":input::identity(&usage)?,"process_receipt":input::identity(&directory.join("process.json"))?}),
    )
}

fn run(mut args: Args) -> Result<(), String> {
    args.before = fs::canonicalize(&args.before).map_err(|e| e.to_string())?;
    args.after = fs::canonicalize(&args.after).map_err(|e| e.to_string())?;
    args.comparator = fs::canonicalize(&args.comparator).map_err(|e| e.to_string())?;
    for path in &mut args.input {
        *path = fs::canonicalize(&*path).map_err(|e| e.to_string())?;
    }
    require(
        (1..=10).contains(&args.repeats) && (1..=16).contains(&args.threads),
        "invalid repeats/threads",
    )?;
    require(!args.input.is_empty(), "no inputs")?;
    let before = input::identity(&args.before)?;
    let after = input::identity(&args.after)?;
    let comparator = input::identity(&args.comparator)?;
    let mut identities = Vec::new();
    for wav in &args.input {
        let reader = hound::WavReader::open(wav).map_err(|e| e.to_string())?;
        require(
            reader.spec().channels == 1 && reader.len() >= 8192,
            "benchmark requires nonempty mono WAV",
        )?;
        identities.push(input::identity(wav)?);
    }
    fs::create_dir(&args.output).map_err(|e| e.to_string())?;
    input::write_json_new(
        &args.output.join("manifest.json"),
        &json!({"schema":"progressive-speed-manifest-v1","before":before,"after":after,"comparator":comparator,"inputs":identities,"threads":args.threads,"cpus":args.cpus,"repeats":args.repeats,"order":"alternate AB/BA by repetition+input index","scope":"full finite task bank; existing development WAV only; conversion excluded"}),
    )?;
    let mut pairs = Vec::new();
    for repeat in 0..args.repeats {
        for (index, wav) in args.input.iter().enumerate() {
            let dir = args.output.join(format!("r{repeat}-i{index}"));
            fs::create_dir(&dir).map_err(|e| e.to_string())?;
            let mut runs = serde_json::Map::new();
            let order = if (repeat + index) % 2 == 0 {
                ["before", "after"]
            } else {
                ["after", "before"]
            };
            for side in order {
                pinned(wav, &identities[index])?;
                pinned(&args.before, &before)?;
                pinned(&args.after, &after)?;
                let binary = if side == "before" {
                    &args.before
                } else {
                    &args.after
                };
                let metrics = execute(&args, binary, &dir.join(side), wav)?;
                pinned(wav, &identities[index])?;
                pinned(binary, if side == "before" { &before } else { &after })?;
                let manifest = input::read_json(&dir.join(side).join("session/manifest.json"))?;
                for identity_name in ["source", "wav"] {
                    require(
                        manifest["audio"][identity_name]["sha256"] == identities[index].sha256
                            && manifest["audio"][identity_name]["bytes"] == identities[index].bytes,
                        "session does not describe the exact requested WAV",
                    )?;
                }
                require(
                    manifest["executable_sha256"].as_str()
                        == Some(if side == "before" {
                            before.sha256.as_str()
                        } else {
                            after.sha256.as_str()
                        }),
                    "session binary differs from pinned executed receiver",
                )?;
                runs.insert(side.into(), metrics);
            }
            pinned(&args.comparator, &comparator)?;
            let report_path = dir.join("strict-session-comparison.json");
            let status = Command::new(&args.comparator)
                .arg("--left")
                .arg(dir.join("before/session"))
                .arg("--right")
                .arg(dir.join("after/session"))
                .arg("--output")
                .arg(&report_path)
                .stdout(File::create(dir.join("comparison.stdout")).map_err(|e| e.to_string())?)
                .stderr(File::create(dir.join("comparison.stderr")).map_err(|e| e.to_string())?)
                .status()
                .map_err(|e| e.to_string())?;
            require(
                matches!(status.code(), Some(0 | 1)),
                "independent comparator failed",
            )?;
            pinned(&args.comparator, &comparator)?;
            let comparison = input::read_json(&report_path)?;
            require(
                comparison["comparator_executable"]["sha256"] == comparator.sha256
                    && comparison["comparator_executable"]["bytes"] == comparator.bytes,
                "comparison report came from an unexpected executable",
            )?;
            admit(&comparison, &before.sha256, &after.sha256)?;
            let b = runs["before"]["outer_wall_seconds"].as_f64().unwrap();
            let a = runs["after"]["outer_wall_seconds"].as_f64().unwrap();
            let pair = json!({"repeat":repeat,"input_index":index,"input":identities[index],"order":order,"runs":runs,"deterministic_equal":true,"comparison":input::identity(&report_path)?,"wall_speedup":b/a,"wall_reduction_percent":100.0*(b-a)/b,"normalization":"only elapsed_seconds and session_sha256; different executable identities explicitly pinned and checked"});
            input::write_json_new(&dir.join("pair.json"), &pair)?;
            pairs.push(pair);
            println!(
                "{}",
                json!({"completed_pairs":pairs.len(),"expected_pairs":args.repeats*args.input.len(),"last_speedup":b/a})
            );
        }
    }
    let sum = |side: &str, key: &str| {
        pairs
            .iter()
            .map(|p| p["runs"][side][key].as_f64().unwrap())
            .sum::<f64>()
    };
    let before_wall = sum("before", "outer_wall_seconds");
    let after_wall = sum("after", "outer_wall_seconds");
    input::write_json_new(
        &args.output.join("summary.json"),
        &json!({"schema":"progressive-speed-summary-v1","status":"complete","pairs":pairs,"all_deterministic_results_equal":true,"before_wall_seconds":before_wall,"after_wall_seconds":after_wall,"wall_speedup":before_wall/after_wall,"wall_reduction_percent":100.0*(before_wall-after_wall)/before_wall,"before_cpu_seconds":sum("before","user_cpu_seconds")+sum("before","system_cpu_seconds"),"after_cpu_seconds":sum("after","user_cpu_seconds")+sum("after","system_cpu_seconds"),"publication_ready":false,"shared_host_load_not_excluded":true}),
    )?;
    Ok(())
}
fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn report() -> Value {
        json!({"status":"different","identity_checks":{"same_policy":true,"same_executable_sha256":false,"same_source_sha256_and_bytes":true,"same_wav_sha256_and_bytes":true,"same_sample_metadata":true,"same_conversion_presence":true},"audio_files_rehashed_and_wav_header_checked":true,"every_task_commit_sha256_verified":true,"complete_snapshot_and_task_universe_verified":true,"independent_bitwise_received_fcs_and_ui_verified":true,"tasks_only_left":[],"tasks_only_right":[],"different_tasks":[],"frames_only_left":[],"frames_only_right":[],"left_task_count":7,"right_task_count":7})
    }
    #[test]
    fn only_explicit_binary_version_difference_is_permitted() {
        let original = report();
        assert!(admit(&original, "a", "b").is_ok());
        for key in [
            "same_policy",
            "same_source_sha256_and_bytes",
            "same_wav_sha256_and_bytes",
            "same_sample_metadata",
            "same_conversion_presence",
        ] {
            let mut r = original.clone();
            r["identity_checks"][key] = json!(false);
            assert!(admit(&r, "a", "b").is_err());
        }
        for key in [
            "tasks_only_left",
            "tasks_only_right",
            "different_tasks",
            "frames_only_left",
            "frames_only_right",
        ] {
            let mut r = original.clone();
            r[key] = json!(["changed"]);
            assert!(admit(&r, "a", "b").is_err());
        }
        let mut r = original.clone();
        r["left_task_count"] = json!(0);
        assert!(admit(&r, "a", "b").is_err());
        assert!(admit(&original, "same", "same").is_err());
    }
}
