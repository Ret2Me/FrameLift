//! Frozen-input, paired anytime ablations. This is an experiment runner, not a
//! declaration that a corpus or a detector is publication-ready.
#[allow(dead_code)]
#[path = "support/holdout_run_io.rs"]
mod io;

use clap::{Parser, Subcommand, ValueEnum};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use telemetry_yield_rs::{input, protocol};

const DEVELOPMENT_20: [u64; 20] = [
    14967385, 14966639, 14967410, 14967361, 14967408, 14967407, 14963998, 14967367, 14967406,
    14967401, 14967415, 14967436, 14967362, 14967384, 14967376, 14959745, 14967393, 14967428,
    14967413, 14967432,
];

#[derive(Parser)]
struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Validate metadata and source hashes without running a decoder.
    Validate {
        #[arg(long)]
        manifest: PathBuf,
    },
    /// Run four additive ablations on identical inputs and wall budgets.
    Run {
        #[arg(long)]
        manifest: PathBuf,
        #[arg(long)]
        binary: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, value_enum, default_value = "development")]
        split: Split,
        #[arg(long, value_delimiter = ',', default_value = "3000,60000")]
        budgets_ms: Vec<u64>,
        #[arg(long, default_value_t = 3)]
        repeats: usize,
        #[arg(long, default_value_t = 1)]
        threads: usize,
    },
    /// Recompute metrics from already saved experiment records, without DSP.
    Summarize {
        #[arg(long)]
        experiment: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, ValueEnum, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
enum Split {
    Development,
    Validation,
    Holdout,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Kind {
    Field,
    Noise,
    KnownBits,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Observation {
    id: u64,
    satellite: String,
    station: String,
    /// A pass identifier shared across stations observing that satellite pass.
    pass: String,
    split: Split,
    kind: Kind,
    previously_inspected: bool,
    input: input::Identity,
    audio_seconds: f64,
    /// Only synthetic known-bit controls may carry expected payloads. These
    /// bytes are used in evaluation only and are never passed to the decoder.
    #[serde(default)]
    expected_payloads: BTreeSet<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: String,
    frozen_utc: String,
    selection_rule: String,
    /// Pass separation is mandatory. Station/satellite are optional additional
    /// held-out axes, not an implicit claim of out-of-distribution generality.
    leakage_axes: BTreeSet<String>,
    observations: Vec<Observation>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
enum Policy {
    Existing,
    Blind,
    Multi,
    Both,
}

impl Policy {
    const ALL: [Self; 4] = [Self::Existing, Self::Blind, Self::Multi, Self::Both];
    fn name(self) -> &'static str {
        match self {
            Self::Existing => "existing",
            Self::Blind => "blind",
            Self::Multi => "multi",
            Self::Both => "both",
        }
    }
    fn arguments(self) -> Vec<String> {
        let mut flags = Vec::new();
        if matches!(self, Self::Existing | Self::Multi) {
            flags.push("--no-blind".into());
        }
        if matches!(self, Self::Existing | Self::Blind) {
            flags.push("--no-multi-anchor".into());
        }
        flags
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Record {
    observation: Observation,
    policy: Policy,
    budget_ms: u64,
    replicate: usize,
    status: String,
    error: Option<String>,
    input_and_checkpoints_verified: bool,
    complete: bool,
    completed_tasks: u64,
    total_tasks: u64,
    all_payloads: BTreeSet<String>,
    strict_ui_payloads: BTreeSet<String>,
    process: Value,
    invocation: Value,
    snapshot: Option<input::Identity>,
}

fn validate(manifest: &Manifest) -> Result<(), String> {
    if manifest.schema != "progressive-evaluation-manifest-v1"
        || chrono::DateTime::parse_from_rfc3339(&manifest.frozen_utc).is_err()
        || manifest.selection_rule.trim().is_empty()
        || manifest.observations.is_empty()
        || manifest.observations.len() > 10_000
    {
        return Err("manifest schema, freeze time, selection rule or cohort size invalid".into());
    }
    if !manifest.leakage_axes.contains("pass")
        || manifest
            .leakage_axes
            .iter()
            .any(|s| !["pass", "station", "satellite"].contains(&s.as_str()))
    {
        return Err("leakage_axes must include pass and may include station/satellite".into());
    }
    let mut ids = BTreeSet::new();
    let mut hashes = BTreeMap::new();
    let mut groups = BTreeMap::new();
    for row in &manifest.observations {
        if !ids.insert(row.id)
            || [&row.satellite, &row.station, &row.pass]
                .iter()
                .any(|s| s.trim().is_empty())
            || !row.audio_seconds.is_finite()
            || row.audio_seconds <= 0.0
            || !Path::new(&row.input.path).is_absolute()
            || row.input.bytes == 0
            || row.input.sha256.len() != 64
            || hex::decode(&row.input.sha256).is_err()
        {
            return Err(format!("invalid or duplicate observation {}", row.id));
        }
        if row.split != Split::Development
            && (row.previously_inspected || DEVELOPMENT_20.contains(&row.id))
        {
            return Err(format!(
                "already inspected observation {} cannot be validation/holdout",
                row.id
            ));
        }
        if hashes
            .insert(row.input.sha256.clone(), row.split)
            .is_some_and(|split| split != row.split)
        {
            return Err("identical input bytes cross the split boundary".into());
        }
        for axis in &manifest.leakage_axes {
            let key = match axis.as_str() {
                "pass" => format!("pass:{}", json!([row.satellite, row.pass])),
                "station" => format!("station:{}", row.station),
                "satellite" => format!("satellite:{}", row.satellite),
                _ => unreachable!(),
            };
            if groups
                .insert(key.clone(), row.split)
                .is_some_and(|split| split != row.split)
            {
                return Err(format!("split leakage through {key}"));
            }
        }
        if (row.kind == Kind::KnownBits) != !row.expected_payloads.is_empty() {
            return Err("only known_bits controls require non-empty expected_payloads".into());
        }
        for payload in &row.expected_payloads {
            if payload.is_empty()
                || payload.len() % 2 != 0
                || payload != &payload.to_ascii_lowercase()
                || hex::decode(payload).is_err()
            {
                return Err(
                    "expected payload must be canonical non-empty lower-case hexadecimal".into(),
                );
            }
        }
    }
    Ok(())
}

fn verify(expected: &input::Identity) -> Result<(), String> {
    let actual = input::identity(Path::new(&expected.path))?;
    if actual.sha256 != expected.sha256 || actual.bytes != expected.bytes {
        return Err(format!("frozen bytes changed: {}", expected.path));
    }
    Ok(())
}

fn manifest(path: &Path) -> Result<Manifest, String> {
    let result: Manifest =
        serde_json::from_value(input::read_json(path)?).map_err(|e| e.to_string())?;
    validate(&result)?;
    Ok(result)
}

// Independent bitwise FCS oracle; do not reuse the detector's CRC implementation
// and never synthesize an FCS to stand in for received evidence.
fn received_fcs(frame: &[u8]) -> bool {
    if frame.len() < 3 {
        return false;
    }
    let mut crc = 0xffffu16;
    for byte in &frame[..frame.len() - 2] {
        for bit in 0..8 {
            let mix = (crc ^ u16::from((byte >> bit) & 1)) & 1;
            crc >>= 1;
            if mix != 0 {
                crc ^= 0x8408;
            }
        }
    }
    !crc == u16::from_le_bytes([frame[frame.len() - 2], frame[frame.len() - 1]])
}

fn payloads(snapshot: &Value) -> Result<(BTreeSet<String>, BTreeSet<String>), String> {
    if snapshot["schema"] != "progressive-audio-result-v1" {
        return Err("unexpected progressive result schema".into());
    }
    let frames = snapshot["frame_with_fcs_hex"]
        .as_array()
        .ok_or("frame array missing")?;
    let mut all = BTreeSet::new();
    let mut strict = BTreeSet::new();
    for frame in frames {
        let bytes = hex::decode(frame.as_str().ok_or("frame is not hexadecimal text")?)
            .map_err(|e| e.to_string())?;
        if !received_fcs(&bytes) {
            return Err("received FCS failed independent verification".into());
        }
        let pdu = &bytes[..bytes.len() - 2];
        let canonical = hex::encode(pdu);
        if !all.insert(canonical.clone()) {
            return Err("duplicate frame in reported union".into());
        }
        if protocol::parse_ax25_ui(pdu).is_some() {
            strict.insert(canonical);
        }
    }
    if snapshot["union_count"].as_u64() != Some(all.len() as u64) {
        return Err("reported union_count mismatch".into());
    }
    Ok((all, strict))
}

fn number(value: &Value, key: &str) -> Result<f64, String> {
    value[key]
        .as_f64()
        .filter(|x| x.is_finite() && *x >= 0.0)
        .ok_or_else(|| format!("missing/invalid {key}"))
}

fn cpu(record: &Record) -> Result<f64, String> {
    Ok(number(&record.process, "user_cpu_seconds")?
        + number(&record.process, "system_cpu_seconds")?)
}

fn one(
    binary: &input::Identity,
    row: &Observation,
    policy: Policy,
    budget: u64,
    replicate: usize,
    threads: usize,
    dir: &Path,
) -> Result<Record, String> {
    fs::create_dir(dir).map_err(|e| e.to_string())?;
    let mut record = Record {
        observation: row.clone(),
        policy,
        budget_ms: budget,
        replicate,
        status: "failed".into(),
        error: None,
        input_and_checkpoints_verified: false,
        complete: false,
        completed_tasks: 0,
        total_tasks: 0,
        all_payloads: BTreeSet::new(),
        strict_ui_payloads: BTreeSet::new(),
        process: Value::Null,
        invocation: Value::Null,
        snapshot: None,
    };
    let attempt = (|| -> Result<(), String> {
        verify(binary)?;
        verify(&row.input)?;
        let session = dir.join("session");
        let mut args = vec![
            "decode-progressive".into(),
            "--input".into(),
            row.input.path.clone(),
            "--output".into(),
            session.display().to_string(),
            "--mode".into(),
            "deep".into(),
            "--budget-ms".into(),
            budget.to_string(),
            "--threads".into(),
            threads.to_string(),
        ];
        args.extend(policy.arguments());
        // The decoder owns its millisecond deadline. The outer timeout is a
        // safety backstop, not a replacement for accurate 3 s enforcement.
        record.process = io::execute(
            Path::new(&binary.path),
            &args,
            dir,
            "decode",
            budget.div_ceil(1000) + 10,
            1024 * 1024 * 1024,
        )?;
        io::require_success(&record.process)?;
        cpu(&record)?;
        record.invocation = input::read_json(&dir.join("decode.stdout.log"))?;
        number(&record.invocation, "invocation_wall_seconds")?;
        number(&record.invocation, "invocation_cpu_seconds")?;
        record.input_and_checkpoints_verified = record.invocation["input_and_checkpoints_verified"]
            .as_bool()
            .ok_or("input_and_checkpoints_verified missing")?;
        if !record.input_and_checkpoints_verified {
            if record.invocation["budget_exhausted"] != true
                || !record.invocation["result"].is_null()
            {
                return Err(
                    "unverified preparation without explicit budget exhaustion and null result"
                        .into(),
                );
            }
            // No decoder snapshot has yet been verified. This is a legitimate
            // anytime outcome, not evidence that the entire recording is empty.
            verify(&row.input)?;
            verify(binary)?;
            record.status = "accepted".into();
            return Ok(());
        }
        let snapshot = input::read_json(&session.join("result.json"))?;
        (record.all_payloads, record.strict_ui_payloads) = payloads(&snapshot)?;
        record.complete = snapshot["complete"]
            .as_bool()
            .ok_or("complete flag missing")?;
        record.completed_tasks = snapshot["completed_tasks"]
            .as_u64()
            .ok_or("completed_tasks missing")?;
        record.total_tasks = snapshot["total_tasks"]
            .as_u64()
            .ok_or("total_tasks missing")?;
        if record.completed_tasks > record.total_tasks
            || (record.complete && record.completed_tasks != record.total_tasks)
        {
            return Err("invalid task completion counts".into());
        }
        // Preserve the budget cut even if this session is subsequently resumed.
        // Evaluation never points at the decoder's replaceable live snapshot.
        let frozen_snapshot = dir.join("decoder-snapshot.json");
        input::write_json_new(&frozen_snapshot, &snapshot)?;
        record.snapshot = Some(input::identity(&frozen_snapshot)?);
        verify(&row.input)?;
        verify(binary)?;
        record.status = "accepted".into();
        Ok(())
    })();
    if let Err(error) = attempt {
        record.error = Some(error);
    }
    input::write_json_new(&dir.join("record.json"), &record)?;
    Ok(record)
}

fn run(
    source: &Path,
    binary_path: &Path,
    output: &Path,
    split: Split,
    mut budgets: Vec<u64>,
    repeats: usize,
    threads: usize,
) -> Result<(), String> {
    let manifest_identity = input::identity(source)?;
    let frozen = manifest(source)?;
    verify(&manifest_identity)?;
    if budgets.is_empty()
        || budgets.iter().any(|&v| !(50..=86_400_000).contains(&v))
        || !(1..=30).contains(&repeats)
        || !(1..=16).contains(&threads)
    {
        return Err("budgets must be 50 ms..24 h, repeats 1..30, threads 1..16".into());
    }
    budgets.sort_unstable();
    budgets.dedup();
    let selected: Vec<_> = frozen
        .observations
        .iter()
        .filter(|r| r.split == split)
        .collect();
    if selected.is_empty() {
        return Err("selected split contains no observations".into());
    }
    if budgets.len() > 16
        || selected
            .len()
            .saturating_mul(repeats)
            .saturating_mul(budgets.len())
            .saturating_mul(4)
            > 100_000
    {
        return Err("bounded runner permits up to 16 budgets and 100000 cells; partition larger experiments using a predeclared cohort plan".into());
    }
    let binary = input::identity(binary_path)?;
    for row in &selected {
        verify(&row.input)?;
    }
    fs::create_dir(output).map_err(|e| e.to_string())?;
    let experiment = json!({
        "schema":"progressive-evaluation-experiment-v1", "frozen_utc":io::utc(),
            "manifest":manifest_identity, "cohort":frozen, "binary":binary,
        "evaluator":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "split":split,"budgets_ms":budgets,"repeats":repeats,"threads":threads,
        "policies":Policy::ALL,"sequence":"rotating policy order; serial executions; fresh session for every cell",
        "scope":"matched declared wall budgets; actual CPU reported separately; no external decoder run by this example",
        "publication_ready":false
    });
    input::write_json_new(&output.join("experiment.json"), &experiment)?;
    let mut records = Vec::new();
    for (index, row) in selected.iter().enumerate() {
        for replicate in 0..repeats {
            for &budget in &budgets {
                for offset in 0..4 {
                    let policy = Policy::ALL[(index + replicate + offset) % 4];
                    let dir = output.join(format!(
                        "obs-{}-r{replicate}-b{budget}-{}",
                        row.id,
                        policy.name()
                    ));
                    records.push(one(&binary, row, policy, budget, replicate, threads, &dir)?);
                }
            }
        }
    }
    input::write_json_new(
        &output.join("summary.json"),
        &summarize_experiment(&experiment, &records)?,
    )
}

#[derive(Default)]
struct Moments {
    all: f64,
    strict: f64,
    wall: f64,
    cpu: f64,
    n: usize,
}

// Paired, pass-cluster percentile bootstrap. Replicates are averaged before
// resampling; neither frames nor repeated timing runs are independent samples.
fn cluster_ci(clusters: &BTreeMap<String, (f64, usize)>) -> Option<[f64; 2]> {
    if clusters.len() < 2 {
        return None;
    }
    let rows: Vec<_> = clusters.values().copied().collect();
    let mut seed = 0x513965f1347b2d09u64;
    let mut draws = Vec::with_capacity(5000);
    for _ in 0..5000 {
        let mut sum = 0.0;
        let mut n = 0usize;
        for _ in 0..rows.len() {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let row = rows[(seed % rows.len() as u64) as usize];
            sum += row.0;
            n += row.1;
        }
        draws.push(sum / n as f64);
    }
    draws.sort_by(f64::total_cmp);
    Some([draws[124], draws[4874]])
}

fn summarize(records: &[Record]) -> Result<Value, String> {
    // Directory enumeration order must not perturb floating point sums when
    // re-reading a saved experiment.
    let mut records: Vec<_> = records.iter().collect();
    records.sort_by_key(|row| (row.observation.id, row.policy, row.budget_ms, row.replicate));
    let mut unique = BTreeSet::new();
    for row in &records {
        if !unique.insert((row.observation.id, row.policy, row.budget_ms, row.replicate)) {
            return Err("duplicate experiment record/cell".into());
        }
    }
    let mut aggregate = Vec::new();
    let budgets: BTreeSet<_> = records.iter().map(|r| r.budget_ms).collect();
    for &budget in &budgets {
        for policy in Policy::ALL {
            for kind in [Kind::Field, Kind::Noise, Kind::KnownBits] {
                let rows: Vec<_> = records
                    .iter()
                    .filter(|r| {
                        r.budget_ms == budget && r.policy == policy && r.observation.kind == kind
                    })
                    .collect();
                if rows.is_empty() {
                    continue;
                }
                let accepted: Vec<_> = rows
                    .iter()
                    .copied()
                    .filter(|r| r.status == "accepted")
                    .collect();
                let mut m = Moments::default();
                let mut global = BTreeSet::new();
                let mut global_ui = BTreeSet::new();
                let mut controls = Vec::new();
                let mut maximum_declared_budget_overrun_seconds = 0.0f64;
                let attempted_wall: Vec<_> = rows
                    .iter()
                    .filter_map(|r| number(&r.process, "wall_seconds").ok())
                    .collect();
                let attempted_cpu: Vec<_> = rows.iter().filter_map(|r| cpu(r).ok()).collect();
                for row in &accepted {
                    m.n += 1;
                    m.all += row.all_payloads.len() as f64;
                    m.strict += row.strict_ui_payloads.len() as f64;
                    m.wall += number(&row.process, "wall_seconds")?;
                    m.cpu += cpu(row)?;
                    if let Some(wall) = row.invocation["invocation_wall_seconds"].as_f64() {
                        maximum_declared_budget_overrun_seconds =
                            maximum_declared_budget_overrun_seconds
                                .max((wall - budget as f64 / 1000.0).max(0.0));
                    }
                    global.extend(row.all_payloads.iter().cloned());
                    global_ui.extend(row.strict_ui_payloads.iter().cloned());
                    if row.observation.kind != Kind::Field {
                        controls.push(json!({"observation_id":row.observation.id,"replicate":row.replicate,
                        "kind":row.observation.kind,"audio_seconds":row.observation.audio_seconds,
                        "full_bank_completed":row.complete,
                        "input_and_checkpoints_verified":row.input_and_checkpoints_verified,
                        "full_bank_exposure_seconds":if row.complete {row.observation.audio_seconds} else {0.0},
                        "exact_known_payload_matches":row.all_payloads.intersection(&row.observation.expected_payloads).count(),
                        "unexpected_payloads":row.all_payloads.difference(&row.observation.expected_payloads).count(),
                        "expected_payloads":row.observation.expected_payloads.len(),
                        "crc_alone_is_not_ground_truth":true}));
                    }
                }
                aggregate.push(json!({"policy":policy,"kind":kind,"budget_ms":budget,"attempts":rows.len(),"accepted":m.n,
                "failures":rows.len()-m.n,"complete_runs":accepted.iter().filter(|r| r.complete).count(),
                "preparation_incomplete_runs":accepted.iter().filter(|r|!r.input_and_checkpoints_verified).count(),
                "mean_all_pdus_per_recording_run":if m.n>0 {Some(m.all/m.n as f64)} else {None},
                "mean_strict_ui_pdus_per_recording_run":if m.n>0 {Some(m.strict/m.n as f64)} else {None},
                "distinct_pdus_union_over_recordings_and_repeats":global.len(),
                "distinct_strict_ui_union_over_recordings_and_repeats":global_ui.len(),
                "total_process_wall_seconds":m.wall,"total_cpu_seconds":m.cpu,"controls":controls,
                "cost_totals_above_include_accepted_runs_only":true,
                "all_attempts_measured_wall_seconds":attempted_wall.iter().sum::<f64>(),
                "all_attempts_measured_cpu_seconds":attempted_cpu.iter().sum::<f64>(),
                "attempts_without_complete_wall_measurement":rows.len()-attempted_wall.len(),
                "attempts_without_complete_cpu_measurement":rows.len()-attempted_cpu.len(),
                "maximum_declared_budget_overrun_seconds":maximum_declared_budget_overrun_seconds}));
            }
        }
    }
    let mut comparisons = Vec::new();
    for budget in budgets {
        for policy in [Policy::Blind, Policy::Multi, Policy::Both] {
            let mut per_observation: BTreeMap<u64, (String, f64, f64, usize, usize)> =
                BTreeMap::new();
            let mut wall_delta = 0.0;
            let mut cpu_delta = 0.0;
            let mut missing = 0;
            for existing in records.iter().filter(|r| {
                r.budget_ms == budget
                    && r.policy == Policy::Existing
                    && r.observation.kind == Kind::Field
            }) {
                let variant = records.iter().find(|r| {
                    r.observation.id == existing.observation.id
                        && r.replicate == existing.replicate
                        && r.budget_ms == budget
                        && r.policy == policy
                });
                let Some(variant) =
                    variant.filter(|r| r.status == "accepted" && existing.status == "accepted")
                else {
                    missing += 1;
                    continue;
                };
                if variant.observation.input.sha256 != existing.observation.input.sha256 {
                    return Err("paired input mismatch".into());
                }
                let entry = per_observation.entry(existing.observation.id).or_insert((
                    json!([existing.observation.satellite, existing.observation.pass]).to_string(),
                    0.0,
                    0.0,
                    0,
                    0,
                ));
                entry.1 += variant.strict_ui_payloads.len() as f64
                    - existing.strict_ui_payloads.len() as f64;
                entry.2 += variant.all_payloads.len() as f64 - existing.all_payloads.len() as f64;
                entry.3 += 1;
                entry.4 += existing
                    .strict_ui_payloads
                    .difference(&variant.strict_ui_payloads)
                    .count();
                wall_delta += number(&variant.process, "wall_seconds")?
                    - number(&existing.process, "wall_seconds")?;
                cpu_delta += cpu(variant)? - cpu(existing)?;
            }
            let mut clusters_ui: BTreeMap<String, (f64, usize)> = BTreeMap::new();
            let mut clusters_all: BTreeMap<String, (f64, usize)> = BTreeMap::new();
            for (cluster, ui, all, n, _) in per_observation.values() {
                let item = clusters_ui.entry(cluster.clone()).or_default();
                item.0 += ui / *n as f64;
                item.1 += 1;
                let item = clusters_all.entry(cluster.clone()).or_default();
                item.0 += all / *n as f64;
                item.1 += 1;
            }
            comparisons.push(json!({"policy":policy,"reference":"existing","budget_ms":budget,
                "paired_observations":per_observation.len(),"pass_clusters":clusters_ui.len(),"missing_or_failed_pairs":missing,
                "strict_ui_sum_of_per_observation_mean_deltas":clusters_ui.values().map(|p|p.0).sum::<f64>(),
                "all_pdu_sum_of_per_observation_mean_deltas":clusters_all.values().map(|p|p.0).sum::<f64>(),
                "strict_ui_mean_delta_per_observation_cluster_ci95":cluster_ci(&clusters_ui),
                "all_pdu_mean_delta_per_observation_cluster_ci95":cluster_ci(&clusters_all),
                "strict_ui_reference_pdus_missed_summed_over_paired_runs":per_observation.values().map(|p|p.4).sum::<usize>(),
                "paired_process_wall_delta_seconds":wall_delta,"paired_cpu_delta_seconds":cpu_delta,
                "same_declared_wall_budget":true,"equal_actual_cpu_cost_established":false,
                "ci_method":"5000 paired pass-cluster percentile draws; timing replicates averaged within observation; fixed seed; exploratory intervals, no multiplicity correction"}));
        }
    }
    Ok(
        json!({"schema":"progressive-evaluation-summary-v1","publication_ready":false,
        "records":records.len(),"aggregates":aggregate,"paired_field_comparisons":comparisons,
        "notes":["Failure is never counted as zero telemetry.",
            "All-PDU means all accepted output of this decoder, not support for every protocol.",
            "Timing repeats are not independent observations and their union is not the mean yield.",
            "Matched declared wall budgets do not imply equal CPU cost or identical completed task coverage.",
            "Received FCS is independently checked; field truth and archive novelty remain unestablished.",
            "Budget expiry before preparation is a valid zero-committed-frame partial result, not a whole-recording negative; task coverage is then unknown.",
            "No external-baseline or multi-station-combining experiment is performed by this runner."]}),
    )
}

fn summarize_experiment(metadata: &Value, records: &[Record]) -> Result<Value, String> {
    if metadata["schema"] != "progressive-evaluation-experiment-v1" {
        return Err("unknown experiment schema".into());
    }
    let cohort: Manifest =
        serde_json::from_value(metadata["cohort"].clone()).map_err(|e| e.to_string())?;
    validate(&cohort)?;
    let split: Split =
        serde_json::from_value(metadata["split"].clone()).map_err(|e| e.to_string())?;
    let budgets: BTreeSet<u64> =
        serde_json::from_value(metadata["budgets_ms"].clone()).map_err(|e| e.to_string())?;
    let repeats = metadata["repeats"]
        .as_u64()
        .filter(|n| (1..=30).contains(n))
        .ok_or("invalid repeats")? as usize;
    if budgets.is_empty() || budgets.iter().any(|n| !(50..=86_400_000).contains(n)) {
        return Err("invalid budgets".into());
    }
    let mut expected = BTreeSet::new();
    for row in cohort.observations.iter().filter(|r| r.split == split) {
        for policy in Policy::ALL {
            for &budget in &budgets {
                for replicate in 0..repeats {
                    expected.insert((row.id, policy, budget, replicate));
                }
            }
        }
    }
    if expected.is_empty() {
        return Err("empty selected cohort".into());
    }
    for row in records {
        if !expected.contains(&(row.observation.id, row.policy, row.budget_ms, row.replicate)) {
            return Err("record outside frozen experiment cells".into());
        }
        let original = cohort
            .observations
            .iter()
            .find(|r| r.id == row.observation.id)
            .unwrap();
        if serde_json::to_value(original).map_err(|e| e.to_string())?
            != serde_json::to_value(&row.observation).map_err(|e| e.to_string())?
        {
            return Err("record metadata differs from frozen cohort".into());
        }
    }
    let present: BTreeSet<_> = records
        .iter()
        .map(|r| (r.observation.id, r.policy, r.budget_ms, r.replicate))
        .collect();
    let missing: Vec<_> = expected
        .difference(&present)
        .map(|(id, policy, budget, replicate)| {
            json!({
        "observation_id":id,"policy":policy,"budget_ms":budget,"replicate":replicate})
        })
        .collect();
    let mut result = summarize(records)?;
    result["cohort_split"] = json!(split);
    result["expected_cells"] = json!(expected.len());
    result["missing_cells"] = json!(missing);
    result["experiment_complete"] = json!(missing.is_empty());
    result["all_cells_accepted"] =
        json!(missing.is_empty() && records.iter().all(|r| r.status == "accepted"));
    Ok(result)
}

fn entry() -> Result<(), String> {
    match Args::parse().command {
        Command::Validate { manifest: path } => {
            let frozen = manifest(&path)?;
            for row in &frozen.observations {
                verify(&row.input)?;
            }
            println!(
                "{}",
                json!({"valid":true,"observations":frozen.observations.len(),"publication_ready":false})
            );
            Ok(())
        }
        Command::Run {
            manifest,
            binary,
            output,
            split,
            budgets_ms,
            repeats,
            threads,
        } => run(
            &manifest, &binary, &output, split, budgets_ms, repeats, threads,
        ),
        Command::Summarize { experiment, output } => {
            let mut records = Vec::new();
            let metadata = input::read_json(&experiment.join("experiment.json"))?;
            if metadata["schema"] != "progressive-evaluation-experiment-v1" {
                return Err("unknown experiment schema".into());
            }
            for path in fs::read_dir(&experiment).map_err(|e| e.to_string())? {
                let path = path.map_err(|e| e.to_string())?.path().join("record.json");
                if path.is_file() {
                    let record: Record = serde_json::from_value(input::read_json(&path)?)
                        .map_err(|e| e.to_string())?;
                    if let Some(ref snapshot) = record.snapshot {
                        verify(snapshot)?;
                        let value = input::read_json(Path::new(&snapshot.path))?;
                        let (all, strict) = payloads(&value)?;
                        if record.status == "accepted"
                            && (all != record.all_payloads || strict != record.strict_ui_payloads)
                        {
                            return Err(
                                "record payloads disagree with saved decoder snapshot".into()
                            );
                        }
                    } else if record.status == "accepted" && record.input_and_checkpoints_verified {
                        return Err("accepted record has no decoder snapshot".into());
                    }
                    records.push(record);
                }
            }
            if records.is_empty() {
                return Err("no experiment records found".into());
            }
            input::write_json_new(&output, &summarize_experiment(&metadata, &records)?)
        }
    }
}

fn main() {
    if let Err(error) = entry() {
        eprintln!("progressive evaluation: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn observation(id: u64) -> Observation {
        Observation {
            id,
            satellite: "sat".into(),
            station: "station".into(),
            pass: "pass".into(),
            split: Split::Development,
            kind: Kind::Field,
            previously_inspected: true,
            input: input::Identity {
                path: "/tmp/test.wav".into(),
                sha256: format!("{id:064x}"),
                bytes: 100,
            },
            audio_seconds: 10.0,
            expected_payloads: BTreeSet::new(),
        }
    }
    fn fixture() -> Manifest {
        Manifest {
            schema: "progressive-evaluation-manifest-v1".into(),
            frozen_utc: "2026-09-10T17:00:00Z".into(),
            selection_rule: "all complete eligible observations in frozen interval".into(),
            leakage_axes: BTreeSet::from(["pass".into()]),
            observations: vec![observation(1)],
        }
    }
    fn record(id: u64, policy: Policy, n: usize) -> Record {
        Record {
            observation: observation(id),
            policy,
            budget_ms: 3000,
            replicate: 0,
            status: "accepted".into(),
            error: None,
            input_and_checkpoints_verified: true,
            complete: false,
            completed_tasks: 1,
            total_tasks: 2,
            all_payloads: (0..n).map(|i| format!("{i:02x}")).collect(),
            strict_ui_payloads: (0..n).map(|i| format!("{i:02x}")).collect(),
            process: json!({"wall_seconds":3.1,"user_cpu_seconds":2.8,"system_cpu_seconds":0.1}),
            invocation: Value::Null,
            snapshot: None,
        }
    }

    #[test]
    fn manifest_checks_pass_and_known_development() {
        let mut m = fixture();
        assert!(validate(&m).is_ok());
        let mut held = observation(2);
        held.split = Split::Holdout;
        held.previously_inspected = false;
        m.observations.push(held);
        assert!(validate(&m).unwrap_err().contains("split leakage"));
        m.observations[1].pass = "new-pass".into();
        assert!(validate(&m).is_ok());
        m.observations[1].id = DEVELOPMENT_20[0];
        assert!(validate(&m).unwrap_err().contains("already inspected"));
    }
    #[test]
    fn source_duplicates_crossing_splits_are_rejected() {
        let mut m = fixture();
        let mut row = observation(2);
        row.split = Split::Holdout;
        row.previously_inspected = false;
        row.pass = "other".into();
        row.input.sha256 = m.observations[0].input.sha256.clone();
        m.observations.push(row);
        assert!(validate(&m).unwrap_err().contains("identical input"));
    }
    #[test]
    fn station_axis_is_explicit() {
        let mut m = fixture();
        let mut row = observation(2);
        row.split = Split::Holdout;
        row.previously_inspected = false;
        row.pass = "other".into();
        m.observations.push(row);
        assert!(validate(&m).is_ok());
        m.leakage_axes.insert("station".into());
        assert!(validate(&m).is_err());
    }
    #[test]
    fn truth_only_allowed_for_known_bits() {
        let mut m = fixture();
        m.observations[0].expected_payloads.insert("abcd".into());
        assert!(validate(&m).is_err());
        m.observations[0].kind = Kind::KnownBits;
        assert!(validate(&m).is_ok());
    }
    #[test]
    fn fcs_oracle_and_duplicate_gate() {
        let payload = b"123456789";
        let mut bytes = payload.to_vec();
        bytes.extend_from_slice(&0x906eu16.to_le_bytes());
        assert!(received_fcs(&bytes));
        let mut wrong = bytes.clone();
        wrong[1] ^= 1;
        assert!(!received_fcs(&wrong));
        let frame = hex::encode(bytes);
        let value = json!({"schema":"progressive-audio-result-v1","union_count":1,"frame_with_fcs_hex":[frame]});
        let (all, strict) = payloads(&value).unwrap();
        assert_eq!(all.len(), 1);
        assert!(strict.is_empty());
        let duplicate = json!({"schema":"progressive-audio-result-v1","union_count":2,"frame_with_fcs_hex":[frame,frame]});
        assert!(payloads(&duplicate).is_err());
    }
    #[test]
    fn four_ablations_keep_existing_branch() {
        assert_eq!(
            Policy::Existing.arguments(),
            vec!["--no-blind", "--no-multi-anchor"]
        );
        assert_eq!(Policy::Blind.arguments(), vec!["--no-multi-anchor"]);
        assert_eq!(Policy::Multi.arguments(), vec!["--no-blind"]);
        assert!(Policy::Both.arguments().is_empty());
    }
    #[test]
    fn repeats_are_averaged_before_confidence_interval() {
        let mut rows = vec![
            record(1, Policy::Existing, 1),
            record(1, Policy::Both, 3),
            record(2, Policy::Existing, 2),
            record(2, Policy::Both, 4),
        ];
        rows[2].observation.pass = "other".into();
        rows[3].observation.pass = "other".into();
        let mut repeat = rows.clone();
        for row in &mut repeat {
            row.replicate = 1;
        }
        rows.extend(repeat);
        let result = summarize(&rows).unwrap();
        let comparison = &result["paired_field_comparisons"][2];
        assert_eq!(comparison["paired_observations"], 2);
        assert_eq!(
            comparison["strict_ui_sum_of_per_observation_mean_deltas"],
            4.0
        );
        assert_eq!(
            comparison["strict_ui_mean_delta_per_observation_cluster_ci95"],
            json!([2.0, 2.0])
        );
    }
    #[test]
    fn failed_run_not_zero_and_single_cluster_no_ci() {
        let mut failed = record(2, Policy::Both, 0);
        failed.status = "failed".into();
        failed.process = Value::Null;
        let result = summarize(&[
            record(1, Policy::Existing, 1),
            record(1, Policy::Both, 2),
            record(2, Policy::Existing, 3),
            failed,
        ])
        .unwrap();
        let comparison = &result["paired_field_comparisons"][2];
        assert_eq!(comparison["missing_or_failed_pairs"], 1);
        assert_eq!(
            comparison["strict_ui_sum_of_per_observation_mean_deltas"],
            1.0
        );
        assert!(comparison["strict_ui_mean_delta_per_observation_cluster_ci95"].is_null());
    }
    #[test]
    fn absent_cpu_is_never_zero_filled() {
        let mut row = record(1, Policy::Both, 0);
        row.process["user_cpu_seconds"] = Value::Null;
        assert!(summarize(&[row]).is_err());
    }
    #[test]
    fn duplicate_cell_rejected() {
        let row = record(1, Policy::Both, 0);
        assert!(summarize(&[row.clone(), row]).is_err());
    }

    #[test]
    fn controls_never_enter_field_mean() {
        let field = record(1, Policy::Both, 2);
        let mut noise = record(2, Policy::Both, 0);
        noise.observation.kind = Kind::Noise;
        let summary = summarize(&[field, noise]).unwrap();
        assert_eq!(summary["aggregates"][0]["kind"], "field");
        assert_eq!(
            summary["aggregates"][0]["mean_all_pdus_per_recording_run"],
            2.0
        );
        assert_eq!(summary["aggregates"][1]["kind"], "noise");
    }

    #[test]
    fn unfinished_experiment_exposes_missing_cells() {
        let meta = json!({"schema":"progressive-evaluation-experiment-v1","cohort":fixture(),
            "split":"development","budgets_ms":[3000],"repeats":1});
        let summary = summarize_experiment(&meta, &[record(1, Policy::Existing, 0)]).unwrap();
        assert_eq!(summary["expected_cells"], 4);
        assert_eq!(summary["missing_cells"].as_array().unwrap().len(), 3);
        assert_eq!(summary["experiment_complete"], false);
        let mut wrong = record(1, Policy::Existing, 0);
        wrong.observation.pass = "changed".into();
        assert!(summarize_experiment(&meta, &[wrong]).is_err());
    }
}
