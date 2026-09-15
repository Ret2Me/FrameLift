//! Finite-corpus audit and day-cluster inference; never input to a receiver.
use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::{formats, input, protocol};

#[derive(Parser)]
struct Args {
    #[command(subcommand)]
    command: Action,
}
#[derive(Subcommand)]
enum Action {
    Analyze {
        #[arg(long)]
        cohort_root: PathBuf,
        #[arg(long)]
        run_root: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        development_summary: Option<PathBuf>,
        #[arg(long)]
        require_complete: bool,
    },
    Controls {
        #[arg(long)]
        output: PathBuf,
    },
    Runtime {
        #[arg(long)]
        prior_plan: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
}
type Set = BTreeSet<String>;
fn require(ok: bool, why: &str) -> Result<(), String> {
    if ok { Ok(()) } else { Err(why.into()) }
}
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str, String> {
    v[key]
        .as_str()
        .ok_or_else(|| format!("missing string {key}"))
}
fn array<'a>(v: &'a Value, key: &str) -> Result<&'a Vec<Value>, String> {
    v[key]
        .as_array()
        .ok_or_else(|| format!("missing array {key}"))
}
fn set(v: &Value) -> Result<Set, String> {
    let a = v.as_array().ok_or("payload set is not an array")?;
    let mut found = Set::new();
    for item in a {
        let s = item.as_str().ok_or("payload is not a string")?;
        let bytes = hex::decode(s).map_err(|e| e.to_string())?;
        require(hex::encode(bytes) == s, "noncanonical payload hex")?;
        require(
            found.insert(s.to_string()),
            "duplicate payload in declared set",
        )?;
    }
    Ok(found)
}
fn checked_json_identity(v: &Value) -> Result<Value, String> {
    let path = Path::new(string(v, "path")?);
    let id = input::identity(path)?;
    require(
        v["sha256"] == id.sha256 && v["bytes"] == id.bytes,
        "artifact identity mismatch",
    )?;
    input::read_json(path)
}
fn verify_identity(v: &Value) -> Result<(), String> {
    let id = input::identity(Path::new(string(v, "path")?))?;
    require(
        v["sha256"] == id.sha256 && v["bytes"] == id.bytes,
        "input identity mismatch",
    )
}
fn verify_tree_identities(value: &Value) -> Result<Vec<Value>, String> {
    let mut found = Vec::new();
    if value.get("path").is_some() && value.get("sha256").is_some() && value.get("bytes").is_some()
    {
        verify_identity(value)?;
        found.push(value.clone());
    } else if let Some(map) = value.as_object() {
        for v in map.values() {
            found.extend(verify_tree_identities(v)?);
        }
    } else if let Some(a) = value.as_array() {
        for v in a {
            found.extend(verify_tree_identities(v)?);
        }
    }
    Ok(found)
}
fn archive_from_raw(
    result: &Value,
    row: &Value,
    artifacts: &BTreeMap<String, Value>,
) -> Result<Set, String> {
    let mut out = Set::new();
    let objects = array(&result["references"], "objects")?;
    let mut urls = Vec::new();
    for object in objects {
        let id = &object["identity"];
        let path = string(id, "path")?;
        require(
            artifacts.get(path) == Some(id),
            "reference identity not bound by commit",
        )?;
        verify_identity(id)?;
        let raw = input::read_bytes_bounded(Path::new(path), 1024 * 1024)?;
        let pdu = hex::encode(raw);
        require(
            object["payload_hex"] == pdu,
            "reference payload differs from downloaded bytes",
        )?;
        out.insert(pdu);
        urls.push(string(object, "url")?.to_string());
    }
    if result["references"]["complete"] == true {
        let listed = array(row, "demoddata")?;
        let mut expected = listed
            .iter()
            .map(|v| string(v, "payload_demod").map(str::to_owned))
            .collect::<Result<Vec<_>, _>>()?;
        urls.sort();
        expected.sort();
        require(
            urls == expected,
            "complete reference set does not match frozen listing",
        )?;
        require(
            result["references"]["listed_count"] == listed.len(),
            "reference listed_count mismatch",
        )?;
        require(
            array(&result["references"], "failures")?.is_empty(),
            "complete reference set has failures",
        )?;
    }
    if result["archive_payloads"].is_array() {
        require(
            set(&result["archive_payloads"])? == out,
            "declared archive differs from raw objects",
        )?;
    }
    Ok(out)
}
fn sample_fingerprint(path: &Path) -> Result<String, String> {
    let mut wav = hound::WavReader::open(path).map_err(|e| e.to_string())?;
    let spec = wav.spec();
    require(
        spec.channels == 1
            && spec.sample_rate == 48000
            && spec.sample_format == hound::SampleFormat::Float
            && spec.bits_per_sample == 32,
        "analysis expects mono48kfloat32 PCM",
    )?;
    let mut digest = Sha256::new();
    for sample in wav.samples::<f32>() {
        let sample = sample.map_err(|e| e.to_string())?;
        require(sample.is_finite(), "nonfinite PCM sample")?;
        digest.update(sample.to_bits().to_le_bytes());
    }
    Ok(hex::encode(digest.finalize()))
}
// Intentionally not the table-driven CRC used by the demodulator.
fn received_fcs_ok(raw: &[u8]) -> bool {
    let mut residue = 0xffff_u16;
    for byte in raw {
        for bit in 0..8 {
            let feedback = (residue ^ u16::from(byte >> bit)) & 1;
            residue >>= 1;
            if feedback != 0 {
                residue ^= 0x8408;
            }
        }
    }
    raw.len() >= 2 && residue == 0xf0b8
}
fn payloads_from_frames(frames: &Value) -> Result<Set, String> {
    let mut found = Set::new();
    for value in set(frames)? {
        let bytes = hex::decode(value).map_err(|e| e.to_string())?;
        require(
            received_fcs_ok(&bytes),
            "original received FCS failed residue audit",
        )?;
        let pdu = &bytes[..bytes.len() - 2];
        require(
            protocol::valid_ax25_ui(pdu),
            "native output is not strict AX25 UI",
        )?;
        found.insert(hex::encode(pdu));
    }
    Ok(found)
}
fn bytes_in(s: &Set) -> usize {
    s.iter().map(|s| s.len() / 2).sum()
}
#[derive(Clone, Debug)]
struct Pair {
    day: String,
    native: usize,
    baseline: usize,
}
#[derive(Clone)]
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    fn uniform(&mut self) -> f64 {
        ((self.next() >> 11) as f64 + 0.5) / ((1u64 << 53) as f64)
    }
    fn normal(&mut self) -> f64 {
        (-2.0 * self.uniform().ln()).sqrt() * (std::f64::consts::TAU * self.uniform()).cos()
    }
}
fn interval(mut a: Vec<f64>) -> Value {
    if a.is_empty() {
        return Value::Null;
    }
    a.sort_by(f64::total_cmp);
    let q = |p: f64| {
        let x = p * (a.len() - 1) as f64;
        let i = x.floor() as usize;
        a[i] + (a[x.ceil() as usize] - a[i]) * (x - i as f64)
    };
    json!([q(0.025), q(0.975)])
}
fn bootstrap(pairs: &[Pair], two_day: bool) -> Result<Value, String> {
    let mut clusters: BTreeMap<String, [f64; 3]> = BTreeMap::new();
    let origin = chrono::NaiveDate::from_ymd_opt(2026, 8, 23).unwrap();
    for p in pairs {
        let key = if two_day {
            let date =
                chrono::NaiveDate::parse_from_str(&p.day, "%Y-%m-%d").map_err(|e| e.to_string())?;
            format!(
                "block-{}",
                date.signed_duration_since(origin).num_days().div_euclid(2)
            )
        } else {
            p.day.clone()
        };
        let c = clusters.entry(key).or_default();
        c[0] += p.native as f64;
        c[1] += p.baseline as f64;
        c[2] += 1.0;
    }
    let groups: Vec<_> = clusters.values().collect();
    if groups.len() < 2 {
        return Ok(
            json!({"cluster_count":groups.len(),"interval_available":false,"reason":"fewer than two date clusters"}),
        );
    }
    let mut rng = Rng(20260908);
    let mut differences = Vec::new();
    let mut ratios = Vec::new();
    for _ in 0..10000 {
        let mut sum = [0.0; 3];
        for _ in &groups {
            let g = groups[(rng.next() % groups.len() as u64) as usize];
            for j in 0..3 {
                sum[j] += g[j];
            }
        }
        differences.push((sum[0] - sum[1]) / sum[2]);
        if sum[1] > 0.0 {
            ratios.push(sum[0] / sum[1]);
        }
    }
    let nonzero_denominator_replicates = ratios.len();
    Ok(json!({
        "cluster":"UTC calendar date", "adjacent_two_day_blocks":two_day,
        "cluster_count":groups.len(),"replicates":10000,"seed":20260908,
        "interval_available":true,"mean_paired_difference_ci95":interval(differences),
        "aggregate_yield_ratio_ci95":if ratios.len()==10000 { interval(ratios) } else { Value::Null },
        "ratio_nonzero_denominator_replicates":nonzero_denominator_replicates,
        "few_clusters_and_cross_boundary_dependence_warning":true,
        "method":"paired percentile cluster bootstrap, retain every observation in each sampled group; descriptive finite-cohort inference, no station/pass independence assumption"
    }))
}

fn analyze(
    cohort_root: &Path,
    run_root: &Path,
    output: &Path,
    development: Option<&Path>,
    require_complete: bool,
) -> Result<Value, String> {
    require(!output.exists(), "analysis output must be a new directory")?;
    let cohort_path = cohort_root.join("cohort.json");
    let cohort = input::read_json(&cohort_path)?;
    let summary_path = run_root.join("summary.json");
    let summary_raw = input::read_bytes_bounded(&summary_path, 64 * 1024 * 1024)?;
    let summary: Value = serde_json::from_slice(&summary_raw).map_err(|e| e.to_string())?;
    let summary_id = json!({"path":summary_path,"bytes":summary_raw.len(),"sha256":hex::encode(Sha256::digest(&summary_raw))});
    let freeze = input::read_json(&cohort_root.join("freeze.json"))?;
    let frozen_ids = verify_tree_identities(&freeze)?;
    let cohort_id = input::identity(&cohort_path)?;
    require(
        frozen_ids
            .iter()
            .any(|id| id["sha256"] == cohort_id.sha256 && id["bytes"] == cohort_id.bytes),
        "freeze does not bind cohort",
    )?;
    require(
        freeze["pagination_complete"] == true
            && freeze["outcomes_computed"] == false
            && freeze["audio_downloaded"] == false,
        "not a completed metadata-only cohort freeze",
    )?;
    let run_plan = input::read_json(&run_root.join("plan.json"))?;
    let plan_ids = verify_tree_identities(&run_plan)?;
    require(
        run_plan["cohort"]["sha256"] == cohort_id.sha256,
        "run plan binds different cohort",
    )?;
    require(
        plan_ids.iter().any(|id| {
            id["sha256"] == "455a9622ba7c839fbba1c575b8139502a9b24c2b3f99421adb2e9ab74bb37ab3"
        }),
        "frozen probe identity absent",
    )?;
    require(
        cohort["protocol_identity"]["sha256"]
            == "ddfe8c45f2c0adf86571eda3603c0796a4c7937db6902f1b076e35cc09607c28",
        "wrong preanalysis protocol",
    )?;
    let cohort_rows = array(&cohort, "observations")?;
    let mut selected = BTreeMap::new();
    for row in cohort_rows {
        let id = row["id"].as_u64().ok_or("invalid cohort ID")?;
        require(selected.insert(id, row).is_none(), "duplicate cohort ID")?;
    }
    let mut seen = BTreeSet::new();
    let mut rows = Vec::new();
    let mut pairs = Vec::new();
    let mut global_native = Set::new();
    let mut global_baseline = Set::new();
    let mut global_archive = Set::new();
    let mut by_variant: BTreeMap<String, Set> = BTreeMap::new();
    let mut variant_totals: BTreeMap<String, usize> = BTreeMap::new();
    let mut statuses: BTreeMap<String, usize> = BTreeMap::new();
    let mut archive_complete_rows = 0;
    let mut added = 0;
    let mut lost = 0;
    let mut improved = 0;
    let mut worsened = 0;
    let mut baseline_zero_recovered = 0;
    let mut perobs_archive_added = 0;
    let mut perobs_archive_baseline_added = 0;
    let mut archive_paired_rows = 0;
    let mut source_addresses: BTreeMap<String, usize> = BTreeMap::new();
    let mut observation_sources: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    let mut input_hashes = BTreeMap::new();
    let mut duplicate_inputs = Vec::new();
    let mut cross_day_duplicates = false;
    for item in array(&summary, "observations")? {
        let id = item["id"].as_u64().ok_or("invalid summary ID")?;
        require(
            selected.contains_key(&id) && seen.insert(id),
            "unexpected/duplicate summary ID",
        )?;
        let status = string(item, "status")?;
        require(
            [
                "complete",
                "failed",
                "no_audio",
                "unsupported_audio",
                "paused_disk",
                "pending",
            ]
            .contains(&status),
            "unknown summary status",
        )?;
        *statuses.entry(status.into()).or_default() += 1;
        if status == "pending" {
            continue;
        }
        require(
            item["result_identity"].is_object() && item["commit_identity"].is_object(),
            "terminal row lacks committed evidence",
        )?;
        let result = checked_json_identity(&item["result_identity"])?;
        require(
            result["observation_id"] == id && result["status"] == status,
            "result ID/status mismatch",
        )?;
        let commit = checked_json_identity(&item["commit_identity"])?;
        let committed = commit
            .get("result_identity")
            .or_else(|| commit.get("result"))
            .ok_or("commit result identity absent")?;
        require(
            committed == &item["result_identity"],
            "result identity differs from commit",
        )?;
        let mut artifacts = BTreeMap::new();
        for artifact in array(&commit, "artifacts")? {
            verify_identity(artifact)?;
            let path = string(artifact, "path")?.to_string();
            require(
                artifacts.insert(path, artifact.clone()).is_none(),
                "duplicate committed artifact path",
            )?;
        }
        let archive = archive_from_raw(&result, selected[&id], &artifacts)?;
        global_archive.extend(archive.iter().cloned());
        if result["references"]["complete"] == true {
            archive_complete_rows += 1;
        }
        if status != "complete" {
            continue;
        }
        verify_identity(&result["source"])?;
        verify_identity(&result["input"])?;
        let pcm_sha = string(&result["input"], "sha256")?;
        let day = string(selected[&id], "start")?
            .get(..10)
            .ok_or("invalid start date")?
            .to_string();
        let samples_sha = sample_fingerprint(Path::new(string(&result["input"], "path")?))?;
        if let Some((previous_id, previous_day)) =
            input_hashes.insert(samples_sha.clone(), (id, day.clone()))
        {
            cross_day_duplicates |= previous_day != day;
            duplicate_inputs.push(json!({"id":id,"previous_id":previous_id,"cross_day":previous_day!=day,"sample_sha256":samples_sha}));
        }
        let probe = checked_json_identity(&result["probe_result_identity"])?;
        require(
            probe["status"] == "complete" && probe["observation_id"] == id,
            "probe ID/status mismatch",
        )?;
        require(probe["input"]["sha256"] == pcm_sha, "probe PCM mismatch")?;
        require(
            probe["bit_repair_enabled"] == false
                && probe["reference_bytes_used_for_search"] == false,
            "probe repair/reference search contract changed",
        )?;
        let native = payloads_from_frames(&probe["union_full_frames"])?;
        require(
            native == set(&result["union_payloads"])?,
            "reported native union differs from full received frames",
        )?;
        let variants = probe["variant_full_frames"]
            .as_object()
            .ok_or("missing variant full frames")?;
        let names: BTreeSet<_> = [
            "legacy-fir512/fixed-full160",
            "boxcar32-rms50/fixed-full160",
            "legacy-fir512/gardner16",
            "boxcar32-rms50/gardner16",
        ]
        .into_iter()
        .collect();
        require(
            variants.keys().map(String::as_str).collect::<BTreeSet<_>>() == names,
            "expected exact four frozen variants",
        )?;
        let mut recombined = Set::new();
        let mut variant_sets = BTreeMap::new();
        for (name, frames) in variants {
            let pdus = payloads_from_frames(frames)?;
            require(
                pdus == set(&result["variant_payloads"][name])?,
                "variant payload mismatch",
            )?;
            recombined.extend(pdus.iter().cloned());
            by_variant
                .entry(name.clone())
                .or_default()
                .extend(pdus.iter().cloned());
            *variant_totals.entry(name.clone()).or_default() += pdus.len();
            variant_sets.insert(name.clone(), pdus);
        }
        require(
            recombined == native,
            "union is not the exact union of four variants",
        )?;
        let baseline = set(&result["baseline_payloads"])?;
        let result_dir = Path::new(string(&item["result_identity"], "path")?)
            .parent()
            .ok_or("missing attempt dir")?;
        let kiss_path = result_dir.join("baseline.kiss");
        require(
            artifacts.contains_key(&kiss_path.display().to_string()),
            "baseline KISS missing from commit",
        )?;
        let parsed =
            formats::parse_kiss(&input::read_bytes_bounded(&kiss_path, 64 * 1024 * 1024)?)?;
        require(
            parsed.malformed_records == 0,
            "malformed committed baseline KISS",
        )?;
        let raw_baseline: Set = parsed
            .data_frames
            .iter()
            .filter(|f| protocol::valid_ax25_ui(&f.payload))
            .map(|f| hex::encode(&f.payload))
            .collect();
        let raw_other: Set = parsed
            .data_frames
            .iter()
            .filter(|f| !protocol::valid_ax25_ui(&f.payload))
            .map(|f| hex::encode(&f.payload))
            .collect();
        require(
            raw_baseline == baseline && raw_other == set(&result["baseline_other_payloads"])?,
            "baseline result differs from original KISS",
        )?;
        for p in &baseline {
            require(
                protocol::valid_ax25_ui(&hex::decode(p).map_err(|e| e.to_string())?),
                "baseline strict UI set includes invalid structure",
            )?;
        }
        let this_added = native.difference(&baseline).count();
        let this_lost = baseline.difference(&native).count();
        added += this_added;
        lost += this_lost;
        improved += usize::from(native.len() > baseline.len());
        worsened += usize::from(native.len() < baseline.len());
        baseline_zero_recovered += usize::from(baseline.is_empty() && !native.is_empty());
        let archive_add = native.difference(&archive).count();
        let ref_union: Set = archive.union(&baseline).cloned().collect();
        let archive_base_add = native.difference(&ref_union).count();
        let complete_refs = result["references"]["complete"] == true;
        if complete_refs {
            perobs_archive_added += archive_add;
            perobs_archive_baseline_added += archive_base_add;
            archive_paired_rows += 1;
        }
        let leave_one_out: BTreeMap<_,_> = variant_sets.keys().map(|omitted| {
            let union: Set = variant_sets.iter().filter(|(k,_)|*k!=omitted).flat_map(|(_,v)|v.iter().cloned()).collect();
            (omitted.clone(),json!({"union_without_count":union.len(),"exclusive_contribution":native.difference(&union).count()}))
        }).collect();
        for p in &native {
            observation_sources.entry(p.clone()).or_default().push(id);
            let ui = protocol::parse_ax25_ui(&hex::decode(p).map_err(|e| e.to_string())?).unwrap();
            *source_addresses
                .entry(format!("{}-{}", ui.source.callsign, ui.source.ssid))
                .or_default() += 1;
        }
        rows.push(json!({"id":id,"utc_day":day,"station":selected[&id]["ground_station"],
            "native":native.len(),"baseline":baseline.len(),"added":this_added,"lost":this_lost,
            "reference_complete":complete_refs,"archive_count":archive.len(),
            "archive_added":if complete_refs {json!(archive_add)} else {Value::Null},
            "archive_baseline_added":if complete_refs {json!(archive_base_add)} else {Value::Null},
            "variants":variant_sets.iter().map(|(k,v)|(k.clone(),v.len())).collect::<BTreeMap<_,_>>(),
            "leave_one_out":leave_one_out,"processes":result["processes"],"result_identity":item["result_identity"]}));
        pairs.push(Pair {
            day,
            native: native.len(),
            baseline: baseline.len(),
        });
        global_native.extend(native);
        global_baseline.extend(baseline);
    }
    let pending = selected.len() - seen.len() + statuses.get("pending").copied().unwrap_or(0);
    let terminal = pending == 0
        && summary["running"] == false
        && summary["fatal_errors"]
            .as_array()
            .is_some_and(Vec::is_empty);
    require(
        !require_complete || terminal,
        "cohort still pending; final analysis refused",
    )?;
    let all_refs = archive_complete_rows == selected.len();
    let archive_absent: Set = global_native.difference(&global_archive).cloned().collect();
    let both: Set = global_archive.union(&global_baseline).cloned().collect();
    let archive_baseline_absent: Set = global_native.difference(&both).cloned().collect();
    let mut development_union = Set::new();
    let mut development_id = Value::Null;
    if let Some(path) = development {
        let dev = input::read_json(path)?;
        development_id = json!(input::identity(path)?);
        for key in ["archive", "native_same_audio", "baseline_same_audio"] {
            development_union.extend(set(
                &dev["global_union_of_observation_local_sets"][key]["pdus"],
            )?);
        }
    }
    let extra_after_development: Set = archive_baseline_absent
        .difference(&development_union)
        .cloned()
        .collect();
    let candidates: Vec<_> = archive_absent.iter().map(|p|json!({
        "payload_hex":p,"bytes":p.len()/2,"observation_ids":observation_sources[p],
        "absent_from_entire_frozen_selected_archive":all_refs,
        "absent_from_completed_component_baseline_union":!global_baseline.contains(p),
        "absent_from_supplied_original_development_union":development.map(|_|!development_union.contains(p)),
        "transmitter_authenticated":false,"secondary_replay_verified":false
    })).collect();
    let n: usize = pairs.iter().map(|p| p.native).sum();
    let b: usize = pairs.iter().map(|p| p.baseline).sum();
    let report = json!({
        "schema":"satnogs-holdout-analysis-v1","status":if terminal {"terminal_cohort_analysis"}else{"interim_not_final"},
        "cohort_identity":input::identity(&cohort_path)?,"summary_snapshot_identity":summary_id,
        "analysis_executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "selected":selected.len(),"paired_complete":pairs.len(),"pending":pending,"statuses":statuses,
        "completed_utc_dates":pairs.iter().map(|p|p.day.clone()).collect::<BTreeSet<_>>(),
        "counts":{"native":n,"baseline":b,"native_only":added,"baseline_only":lost,
            "net":n as i64-b as i64,"improved_observations":improved,"worsened_observations":worsened,
            "tied_observations":pairs.len()-improved-worsened,"baseline_zero_recovered":baseline_zero_recovered,
            "mean_paired_difference":if pairs.is_empty(){Value::Null}else{json!((n as f64-b as f64)/pairs.len() as f64)},
            "yield_ratio":if b==0{Value::Null}else{json!(n as f64/b as f64)},
            "reference_complete_paired_observations":archive_paired_rows,
            "per_observation_archive_added":perobs_archive_added,
            "per_observation_archive_and_baseline_added":perobs_archive_baseline_added},
        "intervals":if terminal&&!cross_day_duplicates {bootstrap(&pairs,false)?}else{Value::Null},
        "two_day_sensitivity":if terminal&&!cross_day_duplicates {bootstrap(&pairs,true)?}else{Value::Null},
        "interval_suppressed_for_cross_day_duplicate_waveform":cross_day_duplicates,
        "global":{"native":global_native.len(),"baseline":global_baseline.len(),"retrieved_archive":global_archive.len(),
            "entire_selected_archive_complete":all_refs,"reference_complete_rows":archive_complete_rows,
            "archive_absent_candidates":archive_absent.len(),"archive_absent_candidate_bytes":bytes_in(&archive_absent),
            "archive_and_baseline_absent_candidates":archive_baseline_absent.len(),
            "archive_and_baseline_absent_candidate_bytes":bytes_in(&archive_baseline_absent),
            "after_supplied_development_exclusion_candidates":development.map(|_|extra_after_development.len()),
            "after_supplied_development_exclusion_candidate_bytes":development.map(|_|bytes_in(&extra_after_development)),
            "development_summary_identity":development_id,
            "absence_from_all_SatNOGS_not_established":true},
        "variant_per_observation_totals":variant_totals,
        "variant_global_counts":by_variant.iter().map(|(k,v)|(k.clone(),v.len())).collect::<BTreeMap<_,_>>(),
        "duplicate_pcm_sample_sequences":duplicate_inputs,"source_address_per_observation_pdu_counts":source_addresses,
        "candidates":candidates,"observations":rows,
        "claim_limits":["Retrospective CANVAS/9600AX25 waveform holdout, not all satellites/protocols.",
            "Additional hypothesis searches and same-byte replay are not independent transmitter evidence.",
            "Same-input component replay is not a replay of the historical station runtime.",
            "CPU/wall observations under concurrent load do not establish superiority at equal computation.",
            "Entire database novelty, production readiness and publication acceptance are not established."],
        "publication_ready":false,"deployment_ready":false
    });
    fs::create_dir_all(output).map_err(|e| e.to_string())?;
    input::write_json_new(&output.join("summary-source.json"), &summary)?;
    input::write_json_new(&output.join("analysis.json"), &report)?;
    let markdown = format!(
        "# Frozen SatNOGS CANVAS waveform holdout — measured results\n\nStatus: {}. These are measurements, not a publication/deployment acceptance certificate.\n\n| Quantity | Count |\n|---|---:|\n| Frozen selected observations | {} |\n| Completed same-PCM comparisons | {} |\n| Pending observations | {} |\n| Native portfolio observation/PDU pairs | {} |\n| Component-baseline observation/PDU pairs | {} |\n| Native-only pairs | {} |\n| Baseline-only pairs missed by native | {} |\n| Globally distinct native PDUs | {} |\n| Native candidates absent from retrieved archive | {} |\n| Corresponding PDU bytes | {} |\n\nAll selected archive references complete: **{}**. If false, absence refers only to the retrieved subset and is not established for the complete selected archive. It never means absent from the entire SatNOGS database.\n\nDay-cluster intervals and two-day sensitivity, per-variant ablations, all input/artifact identities, per-observation failures, processor costs and full candidate bytes are in `analysis.json`. Repeated same-sample recordings across dates suppress intervals.\n\nThis is one spacecraft, one modulation/framing family and a fixed component replay—not a reproduction of every historical station decoder. Additional candidates still need secondary replay and attribution follow-up. No revolutionary, equal-compute-superior or production-ready claim is established.\n",
        report["status"],
        selected.len(),
        pairs.len(),
        pending,
        n,
        b,
        added,
        lost,
        global_native.len(),
        archive_absent.len(),
        bytes_in(&archive_absent),
        all_refs
    );
    {
        use std::io::Write;
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(output.join("results.md"))
            .map_err(|e| e.to_string())?;
        file.write_all(markdown.as_bytes())
            .and_then(|_| file.sync_all())
            .map_err(|e| e.to_string())?;
    }
    input::write_json_new(
        &output.join("commit.json"),
        &json!({"analysis":input::identity(&output.join("analysis.json"))?,"summary_source":input::identity(&output.join("summary-source.json"))?,"results_markdown":input::identity(&output.join("results.md"))?}),
    )?;
    Ok(
        json!({"status":report["status"],"selected":selected.len(),"paired_complete":pairs.len(),"counts":report["counts"],"output":output}),
    )
}

fn controls(output: &Path) -> Result<Value, String> {
    require(!output.exists(), "controls output must be a new directory")?;
    fs::create_dir_all(output).map_err(|e| e.to_string())?;
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: 48000,
        bits_per_sample: 32,
        sample_format: hound::SampleFormat::Float,
    };
    let mut rows = Vec::new();
    for index in 0..64_u64 {
        let seed = 20260908 + index * 104729;
        let mut rng = Rng(seed);
        let kind = if index < 32 { "white" } else { "ar1_0.85" };
        let path = output.join(format!("control-{index:03}-{kind}.wav"));
        let mut wav = hound::WavWriter::create(&path, spec).map_err(|e| e.to_string())?;
        let mut previous = 0.0;
        for _ in 0..(48000 * 60) {
            let white = rng.normal() * 0.12;
            let sample = if index < 32 {
                white
            } else {
                0.85 * previous + (1.0_f64 - 0.85 * 0.85).sqrt() * white
            };
            previous = sample;
            wav.write_sample(sample as f32).map_err(|e| e.to_string())?;
        }
        wav.finalize().map_err(|e| e.to_string())?;
        rows.push(json!({"index":index,"kind":kind,"seed":seed,"seconds":60,"input":input::identity(&path)?}));
    }
    let report = json!({"schema":"holdout-synthetic-controls-v1","generator":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,"controls":rows,"total_seconds":3840,"no_known_frames_inserted":true,"real_station_false_alarm_population":false,"decoder_executed":false});
    input::write_json_new(&output.join("manifest.json"), &report)?;
    Ok(json!({"generated":64,"seconds":3840,"decoder_executed":false,"output":output}))
}
fn runtime(prior_plan: &Path, output: &Path) -> Result<Value, String> {
    require(!output.exists(), "runtime manifest path must be new")?;
    let previous = input::read_json(prior_plan)?;
    let loaded: Set = array(&previous["sources"]["baseline_runtime"], "files")?
        .iter()
        .map(|v| {
            v.as_str()
                .map(str::to_owned)
                .ok_or("bad loaded path".to_string())
        })
        .collect::<Result<_, _>>()?;
    let mut files = BTreeMap::new();
    for old in array(&previous["sources"], "files")? {
        let name = string(old, "path")?;
        if name.contains("/work/golden/env/") || loaded.contains(name) {
            verify_identity(old)?;
            let current = input::identity(Path::new(name))?;
            files.insert(current.path.clone(), current);
        }
    }
    require(files.len() >= 700, "baseline closure unexpectedly small")?;
    let critical: Vec<_> = files
        .values()
        .filter(|id| {
            id.path.contains("/site-packages/satellites/")
                || id.path.ends_with("/bin/gr_satellites")
                || id.path.ends_with("/bin/python3.12")
        })
        .collect();
    let report = json!({"schema":"satnogs-holdout-baseline-runtime-v1","files":files.values().collect::<Vec<_>>(),"critical_files":critical,
        "versions":previous["sources"]["baseline_runtime"].as_object().unwrap().iter().filter(|(k,_)|*k!="files").collect::<BTreeMap<_,_>>(),
        "prior_plan_identity":input::identity(prior_plan)?,"all_current_hashes_equal_prior_recorded_hashes":true,
        "scope":"All prior baseline-loaded source/mapped libraries plus recorded golden-env sources. Not an exhaustive OS/container image or current dynamic CLI import trace."});
    input::write_json_new(output, &report)?;
    Ok(
        json!({"files":files.len(),"bytes":files.values().map(|id|id.bytes).sum::<u64>(),"output":output}),
    )
}
fn main() {
    let result = match Args::parse().command {
        Action::Analyze {
            cohort_root,
            run_root,
            output,
            development_summary,
            require_complete,
        } => analyze(
            &cohort_root,
            &run_root,
            &output,
            development_summary.as_deref(),
            require_complete,
        ),
        Action::Controls { output } => controls(&output),
        Action::Runtime { prior_plan, output } => runtime(&prior_plan, &output),
    };
    match result {
        Ok(v) => println!("{v}"),
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1);
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    fn archive_fixture() -> (tempfile::TempDir, Value, Value, BTreeMap<String, Value>) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference-0000.bin");
        // Archive objects are preserved byte-for-byte, including non-AX.25 prefixes.
        let raw = [0x00, 0x00, 0xca, 0x00, 0x81, 0xff];
        fs::write(&path, raw).unwrap();
        let identity = json!(input::identity(&path).unwrap());
        let url = "https://network.satnogs.org/media/demoddata/fixture-0000.bin";
        let result = json!({
            "status": "no_audio",
            "references": {
                "complete": true,
                "listed_count": 1,
                "failures": [],
                "objects": [{
                    "url": url,
                    "identity": identity,
                    "payload_hex": hex::encode(raw)
                }]
            }
        });
        let row = json!({"demoddata": [{"payload_demod": url}]});
        let artifacts = BTreeMap::from([(path.display().to_string(), identity)]);
        (directory, result, row, artifacts)
    }

    #[test]
    fn archive_no_audio_row_still_contributes_original_reference_bytes() {
        let (_directory, result, row, artifacts) = archive_fixture();
        assert!(result.get("archive_payloads").is_none());
        let archive = archive_from_raw(&result, &row, &artifacts).unwrap();
        let expected = BTreeSet::from(["0000ca0081ff".to_owned()]);
        assert_eq!(archive, expected);
        assert_eq!(expected.difference(&archive).count(), 0);
    }

    #[test]
    fn archive_rejects_raw_object_changed_after_commit() {
        let (directory, result, row, artifacts) = archive_fixture();
        fs::write(
            directory.path().join("reference-0000.bin"),
            [0x00, 0x00, 0xcb, 0x00, 0x81, 0xff],
        )
        .unwrap();
        let error = archive_from_raw(&result, &row, &artifacts).unwrap_err();
        assert!(error.contains("identity mismatch"), "{error}");
    }

    #[test]
    fn archive_complete_flag_cannot_hide_missing_listed_reference() {
        let (_directory, mut result, mut row, artifacts) = archive_fixture();
        row["demoddata"].as_array_mut().unwrap().push(json!({
            "payload_demod": "https://network.satnogs.org/media/demoddata/fixture-0001.bin"
        }));
        result["references"]["listed_count"] = json!(2);
        let error = archive_from_raw(&result, &row, &artifacts).unwrap_err();
        assert!(error.contains("frozen listing"), "{error}");
    }

    #[test]
    fn residue_standard_vector_and_corruption() {
        let mut raw = b"123456789".to_vec();
        raw.extend([0x6e, 0x90]);
        assert!(received_fcs_ok(&raw));
        raw[0] ^= 1;
        assert!(!received_fcs_ok(&raw));
        assert!(!received_fcs_ok(&[]));
    }
    #[test]
    fn frame_dedup_rejects_duplicate_declarations() {
        assert!(set(&json!(["00", "00"])).is_err());
        assert!(set(&json!(["AA"])).is_err());
        assert_eq!(set(&json!([])).unwrap().len(), 0);
    }
    #[test]
    fn identical_pairs_have_zero_interval() {
        let pairs = vec![
            Pair {
                day: "2026-08-23".into(),
                native: 2,
                baseline: 2,
            },
            Pair {
                day: "2026-08-24".into(),
                native: 5,
                baseline: 5,
            },
        ];
        let b = bootstrap(&pairs, false).unwrap();
        assert_eq!(b["mean_paired_difference_ci95"], json!([0.0, 0.0]));
        assert_eq!(b["aggregate_yield_ratio_ci95"], json!([1.0, 1.0]));
    }
    #[test]
    fn paired_cluster_retains_within_day_rows_and_is_deterministic() {
        let a = vec![
            Pair {
                day: "2026-08-23".into(),
                native: 10,
                baseline: 1,
            },
            Pair {
                day: "2026-08-23".into(),
                native: 0,
                baseline: 9,
            },
            Pair {
                day: "2026-08-24".into(),
                native: 2,
                baseline: 2,
            },
        ];
        let b = bootstrap(&a, false).unwrap();
        assert_eq!(b["cluster_count"], 2);
        assert_eq!(b["mean_paired_difference_ci95"], json!([0.0, 0.0]));
        assert_eq!(b, bootstrap(&a, false).unwrap());
    }
    #[test]
    fn missing_baseline_denominator_does_not_produce_infinite_ratio() {
        let a = vec![
            Pair {
                day: "2026-08-23".into(),
                native: 1,
                baseline: 0,
            },
            Pair {
                day: "2026-08-24".into(),
                native: 2,
                baseline: 0,
            },
        ];
        assert!(bootstrap(&a, false).unwrap()["aggregate_yield_ratio_ci95"].is_null());
    }
    #[test]
    fn two_day_blocks_use_calendar_distance_not_available_row_rank() {
        let a = vec![
            Pair {
                day: "2026-08-23".into(),
                native: 1,
                baseline: 1,
            },
            Pair {
                day: "2026-08-25".into(),
                native: 1,
                baseline: 1,
            },
        ];
        assert_eq!(bootstrap(&a, true).unwrap()["cluster_count"], 2);
    }
}
