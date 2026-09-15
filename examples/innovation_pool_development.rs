//! Frozen OLD20 development regression and incremental pooled-codec ablation.
//! Does not select, fetch, inspect, or mutate any fresh-cohort recording.
use clap::Parser;
use serde::Serialize;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    sync::{
        Mutex,
        atomic::{AtomicUsize, Ordering},
    },
    time::Instant,
};
use telemetry_yield_rs::{adaptive::AdaptiveConfig, innovation_audio, input, protocol};

const IDS: [u64; 20] = [
    14959745, 14963998, 14966639, 14967361, 14967362, 14967367, 14967376, 14967384, 14967385,
    14967393, 14967401, 14967406, 14967407, 14967408, 14967410, 14967413, 14967415, 14967428,
    14967432, 14967436,
];
type FrameSet = BTreeSet<String>;

#[derive(Parser)]
struct Args {
    #[arg(
        long,
        default_value = "/home/ubuntu/telemetry-yield/work/satnogs-today20-20260910-v1/acquisition"
    )]
    acquisition: PathBuf,
    #[arg(
        long,
        default_value = "/home/ubuntu/telemetry-yield/work/innovation-field-20260910-v1/campaign"
    )]
    reference: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 4)]
    jobs: usize,
    #[arg(long, default_value_t = 2)]
    threads: usize,
}

#[derive(Serialize)]
struct FrozenJob {
    id: u64,
    source: input::Identity,
    reference_result: input::Identity,
    reference_pdu_count: usize,
    #[serde(skip)]
    reference_pdus: FrameSet,
}

fn received_crc(bytes: &[u8]) -> bool {
    let mut crc = 0xffffu16;
    for &byte in bytes {
        for bit in 0..8 {
            let feedback = (crc ^ u16::from(byte >> bit)) & 1;
            crc >>= 1;
            if feedback != 0 {
                crc ^= 0x8408;
            }
        }
    }
    bytes.len() >= 2 && crc == 0xf0b8
}

/// Returns exact PDU bytes excluding the independently checked received FCS.
/// These are AX.25 PDUs, not claimed to be pure telemetry application payload.
fn checked_pdus(value: &Value) -> Result<FrameSet, String> {
    let rows = value.as_array().ok_or("native full-frame array missing")?;
    let mut pdus = BTreeSet::new();
    for row in rows {
        let text = row.as_str().ok_or("native full-frame hex is not string")?;
        let bytes = hex::decode(text).map_err(|e| e.to_string())?;
        if hex::encode(&bytes) != text
            || !received_crc(&bytes)
            || !protocol::valid_ax25_ui(&bytes[..bytes.len() - 2])
        {
            return Err("invalid canonical native received FCS or AX.25 UI PDU".into());
        }
        if !pdus.insert(hex::encode(&bytes[..bytes.len() - 2])) {
            return Err("duplicate native PDU in declared set".into());
        }
    }
    Ok(pdus)
}

fn same_identity(expected: &input::Identity, actual: &input::Identity) -> bool {
    expected.path == actual.path
        && expected.bytes == actual.bytes
        && expected.sha256 == actual.sha256
}

fn unchanged(identity: &input::Identity) -> Result<(), String> {
    if !same_identity(identity, &input::identity(Path::new(&identity.path))?) {
        return Err(format!("frozen identity changed: {}", identity.path));
    }
    Ok(())
}

fn usage() -> Result<(f64, f64, i64), String> {
    let mut self_cpu = 0.0;
    let mut children_cpu = 0.0;
    let mut rss = 0;
    for who in [libc::RUSAGE_SELF, libc::RUSAGE_CHILDREN] {
        let mut raw = std::mem::MaybeUninit::<libc::rusage>::zeroed();
        // SAFETY: getrusage initializes this valid, exclusively borrowed object.
        if unsafe { libc::getrusage(who, raw.as_mut_ptr()) } != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        // SAFETY: successful getrusage initialized the complete rusage object.
        let value = unsafe { raw.assume_init() };
        let seconds = value.ru_utime.tv_sec as f64
            + value.ru_utime.tv_usec as f64 * 1e-6
            + value.ru_stime.tv_sec as f64
            + value.ru_stime.tv_usec as f64 * 1e-6;
        if who == libc::RUSAGE_SELF {
            self_cpu = seconds;
            rss = value.ru_maxrss;
        } else {
            children_cpu = seconds;
        }
    }
    Ok((self_cpu, children_cpu, rss))
}

fn delta(new: &FrameSet, old: &FrameSet) -> Value {
    let added: FrameSet = new.difference(old).cloned().collect();
    let lost: FrameSet = old.difference(new).cloned().collect();
    json!({"before":old.len(),"after":new.len(),"added_count":added.len(),"lost_count":lost.len(),
        "added_pdu_bytes_excluding_fcs":added.iter().map(|pdu|pdu.len()/2).sum::<usize>(),
        "lost_pdu_bytes_excluding_fcs":lost.iter().map(|pdu|pdu.len()/2).sum::<usize>(),
        "added_pdus":added,"lost_pdus":lost})
}

fn score(job: &FrozenJob, result: &Value) -> Result<Value, String> {
    let actual: input::Identity =
        serde_json::from_value(result["source"].clone()).map_err(|e| e.to_string())?;
    if result["status"] != "complete"
        || result["pooled_codec"] != true
        || !same_identity(&job.source, &actual)
    {
        return Err(
            "improved source identity, completion or pooled-codec attestation mismatch".into(),
        );
    }
    let report = &result["report"];
    let previous = checked_pdus(&report["previous_innovation_union_full_frames"])?;
    let union = checked_pdus(&report["union_full_frames"])?;
    let weighted = checked_pdus(&report["lane_full_frames"]["pooled-codec-innovations"])?;
    let matched = checked_pdus(&report["pooled_matched_unweighted_full_frames"])?;
    if result["union_count"].as_u64() != Some(union.len() as u64)
        || !previous.is_subset(&union)
        || previous.union(&weighted).cloned().collect::<FrameSet>() != union
        || checked_pdus(&report["added_vs_previous_innovation"])?
            != union.difference(&previous).cloned().collect()
        || checked_pdus(&report["pooled_added_vs_matched_unweighted"])?
            != weighted.difference(&matched).cloned().collect()
        || checked_pdus(&report["pooled_lost_vs_matched_unweighted"])?
            != matched.difference(&weighted).cloned().collect()
    {
        return Err(
            "reported additive union or matched pooled ablation contradicts checked sets".into(),
        );
    }
    let mut statuses: BTreeMap<String, usize> = BTreeMap::new();
    for calibration in report["pooled_calibrations"]
        .as_array()
        .ok_or("pooled calibrations absent")?
    {
        let status = calibration["fit"]["status"]
            .as_str()
            .ok_or("pool status absent")?;
        if ![
            "insufficient_sources",
            "insufficient_samples",
            "gate_accepted",
            "gate_rejected",
        ]
        .contains(&status)
        {
            return Err("unknown pooled fit status".into());
        }
        *statuses.entry(status.into()).or_default() += 1;
    }
    let accepted = statuses.get("gate_accepted").copied().unwrap_or(0);
    if result["pooled_fits_accepted"].as_u64() != Some(accepted as u64) {
        return Err("accepted pooled fit count differs from status evidence".into());
    }
    Ok(json!({
        "previous_v1_parity":previous == job.reference_pdus,
        "counts":{"frozen_v1":job.reference_pdus.len(),"v2_previous":previous.len(),"v2_union":union.len(),
            "pool_weighted":weighted.len(),"pool_matched_unweighted":matched.len()},
        "pdus":{"frozen_v1":job.reference_pdus,"v2_previous":previous,"v2_union":union,
            "pool_weighted":weighted,"pool_matched_unweighted":matched},
        "previous_vs_frozen_v1":delta(&previous,&job.reference_pdus),
        "v2_union_vs_frozen_v1":delta(&union,&job.reference_pdus),
        "v2_union_vs_its_previous":delta(&union,&previous),
        "pool_weighted_vs_matched_unweighted":delta(&weighted,&matched),
        "pool_status_counts":statuses,"codec_fits_accepted":result["codec_fits_accepted"],
        "pooled_fits_accepted":accepted,"received_fcs_independently_checked":true,
        "received_fcs_is_not_source_authentication":true
    }))
}

fn observe(job: &FrozenJob, out: &Path, threads: usize) -> Result<Value, String> {
    let started = Instant::now();
    let attempt = (|| -> Result<Value, String> {
        unchanged(&job.source)?;
        unchanged(&job.reference_result)?;
        let folder = out.join(format!("obs-{}", job.id));
        let receipt = innovation_audio::decode_file_with_options(
            Path::new(&job.source.path),
            &folder,
            &AdaptiveConfig {
                baud: 9600.0,
                threads,
            },
            true,
            true,
            None,
        )?;
        let result_path = folder.join("result.json");
        let result = input::read_json(&result_path)?;
        let mut record = score(job, &result)?;
        unchanged(&job.source)?;
        unchanged(&job.reference_result)?;
        record["status"] = json!(if record["previous_v1_parity"] == true {
            "complete"
        } else {
            "parity_failed"
        });
        record["receipt"] = receipt;
        record["result_identity"] = json!(input::identity(&result_path)?);
        Ok(record)
    })();
    let mut record = match attempt {
        Ok(value) => value,
        Err(reason) => json!({"status":"failed","reason":reason}),
    };
    record["id"] = json!(job.id);
    record["wall_seconds"] = json!(started.elapsed().as_secs_f64());
    record["source"] = json!(job.source);
    record["reference_result"] = json!(job.reference_result);
    record["concurrent_job_wall_time_not_isolated_speed_measurement"] = json!(true);
    input::write_json_new(&out.join(format!("record-{}.json", job.id)), &record)?;
    println!(
        "{}",
        json!({"id":job.id,"status":record["status"],"counts":record["counts"],
        "pool_status_counts":record["pool_status_counts"],"wall_seconds":record["wall_seconds"]})
    );
    Ok(record)
}

fn run(args: Args) -> Result<(), String> {
    if !(1..=4).contains(&args.jobs) || !(1..=2).contains(&args.threads) {
        return Err("OLD20 development runner supports jobs1..4 and threads1..2".into());
    }
    let out = input::existing_new_dir(&args.output)?;
    let mut jobs = Vec::new();
    for id in IDS {
        let source = input::identity(&args.acquisition.join(format!("obs-{id}/capture.ogg")))?;
        let reference_path = args.reference.join(format!("obs-{id}/result.json"));
        let reference_result = input::identity(&reference_path)?;
        let reference = input::read_json(&reference_path)?;
        let reference_source: input::Identity =
            serde_json::from_value(reference["source"].clone()).map_err(|e| e.to_string())?;
        if reference["status"] != "complete" || !same_identity(&source, &reference_source) {
            return Err(format!("OLD20 frozen v1 source/completion mismatch: {id}"));
        }
        let reference_pdus = checked_pdus(&reference["report"]["union_full_frames"])?;
        if reference["union_count"].as_u64() != Some(reference_pdus.len() as u64) {
            return Err(format!("OLD20 frozen v1 count mismatch: {id}"));
        }
        unchanged(&reference_result)?;
        jobs.push(FrozenJob {
            id,
            source,
            reference_result,
            reference_pdu_count: reference_pdus.len(),
            reference_pdus,
        });
    }
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let mut codec_tools = Vec::new();
    for program in ["/usr/bin/ffmpeg", "/usr/bin/ffprobe"] {
        codec_tools.push(json!({"identity":input::identity(Path::new(program))?,
            "version":String::from_utf8(input::external(program,&["-version".into()],10)?).map_err(|e|e.to_string())?}));
    }
    let protocol_paths = [
        "docs/innovation-audio-protocol.md",
        "docs/codec-pool-design-20260910.md",
    ];
    let protocols: Vec<_> = protocol_paths
        .iter()
        .map(|p| input::identity(&root.join(p)))
        .collect::<Result<_, _>>()?;
    let code_paths = [
        "rust/innovation_audio.rs",
        "rust/codec_pool.rs",
        "rust/codec_reliability.rs",
        "examples/innovation_pool_development.rs",
    ];
    let code: Vec<_> = code_paths
        .iter()
        .map(|p| input::identity(&root.join(p)))
        .collect::<Result<_, _>>()?;
    input::write_json_new(
        &out.join("plan.json"),
        &json!({
            "schema":"innovation-pool-old20-development-plan-v1","ids":IDS,"sources":jobs,
            "selection":"all twenty OLD recordings, fixed ascending IDs, no exclusions based on results",
            "known_development_data":true,"fresh_cohort_used":false,
            "executable":executable,"protocols":protocols,"source_code_at_launch":code,"codec_tools":codec_tools,
            "jobs":args.jobs,"threads_per_decode":args.threads,"baud":9600,
            "observation_executor":"fixed OS worker threads; nested Rayon work stealing cannot admit additional observations",
            "input":"original OGG decoded as float32 through existing source-bound input loader",
            "previous_frozen_reference":"entire original OGG innovations v1 union, including its codec lane",
            "pooled_codec":true,"external_codec_source":false,
            "source_gate_is_conditional_on_existing_full_window_frontend":true,
            "source_raw_waveform_holdout_independence_established":false,
            "target_truth_used_for_training":false,"crc_guided_bit_repair":false,
            "reference_bytes_used_for_search":false,"network_access_required":false,
            "timing_scope":"concurrent development regression, not isolated benchmark speed claim",
            "publication_ready":false,"deployment_ready":false
        }),
    )?;
    let before = usage()?;
    let started = Instant::now();
    // Fixed OS workers, not nested Rayon jobs: an inner decoder pool can allow
    // its calling Rayon worker to steal another observation while awaiting work,
    // retaining many large audio buffers despite the apparent outer thread cap.
    let next = AtomicUsize::new(0);
    let attempts = Mutex::new(Vec::new());
    std::thread::scope(|scope| {
        for _ in 0..args.jobs {
            scope.spawn(|| {
                loop {
                    let index = next.fetch_add(1, Ordering::Relaxed);
                    let Some(job) = jobs.get(index) else { break };
                    let result = observe(job, &out, args.threads);
                    attempts.lock().unwrap().push((index, result));
                }
            });
        }
    });
    let mut attempts = attempts.into_inner().map_err(|e| e.to_string())?;
    attempts.sort_by_key(|(index, _)| *index);
    let after = usage()?;
    let mut records = Vec::new();
    for (job, (_, attempt)) in jobs.iter().zip(attempts) {
        records.push(match attempt {
            Ok(record) => record,
            Err(reason) => json!({"id":job.id,"status":"record_write_failed","reason":reason}),
        });
    }
    let mut totals: BTreeMap<String, usize> = BTreeMap::new();
    let mut global: BTreeMap<String, FrameSet> = BTreeMap::new();
    let mut statuses: BTreeMap<String, usize> = BTreeMap::new();
    for record in &records {
        if let Some(counts) = record["counts"].as_object() {
            for (name, count) in counts {
                *totals.entry(name.clone()).or_default() +=
                    count.as_u64().ok_or("bad checked count")? as usize;
            }
            for (name, frames) in record["pdus"]
                .as_object()
                .ok_or("checked PDU maps absent")?
            {
                let values: FrameSet =
                    serde_json::from_value(frames.clone()).map_err(|e| e.to_string())?;
                global.entry(name.clone()).or_default().extend(values);
            }
            for (status, count) in record["pool_status_counts"]
                .as_object()
                .ok_or("status counts absent")?
            {
                *statuses.entry(status.clone()).or_default() +=
                    count.as_u64().ok_or("bad pool status count")? as usize;
            }
        }
    }
    let complete = records.iter().all(|r| r["status"] == "complete");
    let global_counts: BTreeMap<_, _> = global
        .iter()
        .map(|(name, frames)| (name, frames.len()))
        .collect();
    let global_comparison =
        if let (Some(new), Some(old)) = (global.get("v2_union"), global.get("frozen_v1")) {
            Some(delta(new, old))
        } else {
            None
        };
    input::write_json_new(
        &out.join("summary.json"),
        &json!({
            "schema":"innovation-pool-old20-development-summary-v1",
            "status":if complete {"complete"} else {"incomplete"},"requested":IDS.len(),
            "complete_observations":records.iter().filter(|r|r["status"]=="complete").count(),
            "records":records,"per_observation_pdu_totals":totals,"global_distinct_pdu_counts":global_counts,
            "global_pdus":global,"global_v2_vs_frozen_v1":global_comparison,"pool_status_counts":statuses,
            "wall_seconds":started.elapsed().as_secs_f64(),"cpu_self_seconds":after.0-before.0,
            "cpu_waited_children_seconds":after.1-before.1,"process_lifetime_peak_rss_kib":after.2,
            "per_observation_cpu_not_attributed_under_concurrency":true,
            "known_development_data":true,"fresh_cohort_used":false,
            "comparison_to_complete_progressive_bank":false,"equal_cpu_advantage_established":false,
            "publication_ready":false,"deployment_ready":false
        }),
    )?;
    if !complete {
        return Err(
            "OLD20 experiment incomplete or previous-v1 parity failed; inspect retained records"
                .into(),
        );
    }
    Ok(())
}

fn main() {
    if let Err(reason) = run(Args::parse()) {
        eprintln!("{reason}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn independent_crc_and_ui_check_accept_valid_but_reject_modified_frame() {
        let mut payload = hex::decode("86829cac82a6609882a6a04040e103f00820").unwrap();
        let crc = protocol::crc16_x25(&payload);
        payload.extend(crc.to_le_bytes());
        let value = json!([hex::encode(&payload)]);
        assert_eq!(checked_pdus(&value).unwrap().len(), 1);
        payload[15] ^= 1;
        assert!(checked_pdus(&json!([hex::encode(payload)])).is_err());
    }

    #[test]
    fn malformed_or_duplicate_native_sets_are_not_silently_accepted() {
        assert!(checked_pdus(&Value::Null).is_err());
        assert!(checked_pdus(&json!(["00"])).is_err());
        let mut payload = hex::decode("86829cac82a6609882a6a04040e103f00820").unwrap();
        payload.extend(protocol::crc16_x25(&payload).to_le_bytes());
        let frame = hex::encode(payload);
        assert!(checked_pdus(&json!([frame, frame])).is_err());
    }
}
