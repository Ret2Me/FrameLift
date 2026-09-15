//! Frozen twenty-observation DEVELOPMENT pilot; no outcome-based selection.
use clap::Parser;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::PathBuf,
    time::Instant,
};
use telemetry_yield_rs::{adaptive::AdaptiveConfig, innovation_audio, input, protocol};
const IDS: [u64; 20] = [
    14959745, 14963998, 14966639, 14967361, 14967362, 14967367, 14967376, 14967384, 14967385,
    14967393, 14967401, 14967406, 14967407, 14967408, 14967410, 14967413, 14967415, 14967428,
    14967432, 14967436,
];
#[derive(Parser)]
struct Args {
    #[arg(long)]
    acquisition: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 4)]
    threads: usize,
}
fn usage() -> Result<(f64, f64, i64), String> {
    let mut total = 0.0;
    let mut children = 0.0;
    let mut rss = 0;
    for who in [libc::RUSAGE_SELF, libc::RUSAGE_CHILDREN] {
        let mut value = std::mem::MaybeUninit::<libc::rusage>::zeroed();
        // SAFETY:getrusage initializes the supplied valid rusage pointer.
        if unsafe { libc::getrusage(who, value.as_mut_ptr()) } != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        let value = unsafe { value.assume_init() };
        let seconds = value.ru_utime.tv_sec as f64
            + value.ru_utime.tv_usec as f64 * 1e-6
            + value.ru_stime.tv_sec as f64
            + value.ru_stime.tv_usec as f64 * 1e-6;
        if who == libc::RUSAGE_SELF {
            total = seconds;
            rss = value.ru_maxrss;
        } else {
            children = seconds;
        }
    }
    Ok((total, children, rss))
}
fn received_crc(bytes: &[u8]) -> bool {
    let mut crc = 0xffffu16;
    for byte in bytes {
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
fn checked_frames(value: &Value) -> Result<BTreeSet<String>, String> {
    let frames: BTreeSet<String> =
        serde_json::from_value(value.clone()).map_err(|e| e.to_string())?;
    for frame in &frames {
        let bytes = hex::decode(frame).map_err(|e| e.to_string())?;
        if !received_crc(&bytes) || !protocol::valid_ax25_ui(&bytes[..bytes.len() - 2]) {
            return Err("campaign found invalid received CRC or AX25 UI".into());
        }
    }
    Ok(frames)
}
fn run(args: Args) -> Result<(), String> {
    if !(1..=16).contains(&args.threads) {
        return Err("threads must be1..16".into());
    }
    let out = input::existing_new_dir(&args.output)?;
    let executable = input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let mut sources = Vec::new();
    for id in IDS {
        let path = args.acquisition.join(format!("obs-{id}/capture.ogg"));
        sources.push((id, path.clone(), input::identity(&path)?));
    }
    let mut tools = Vec::new();
    for path in ["/usr/bin/ffmpeg", "/usr/bin/ffprobe"] {
        tools.push(json!({"identity":input::identity(std::path::Path::new(path))?,
            "version":String::from_utf8(input::external(path,&["-version".into()],10)?).map_err(|e|e.to_string())?}));
    }
    input::write_json_new(
        &out.join("manifest.json"),
        &json!({
            "schema":"innovation-field-development-manifest-v1","ids":IDS,
            "selection":"all twenty previously inspected recordings; ascendingID; no outcome filter",
            "sources":sources.iter().map(|(id,_,source)|json!({"id":id,"source":source})).collect::<Vec<_>>(),
            "executable":executable,"threads":args.threads,"codec_tools":tools,
            "protocol":input::identity(std::path::Path::new("docs/innovation-audio-protocol.md"))?,
            "known_development_data":true,"publication_ready":false,
            "comparison_to_complete_progressive_bank":false
        }),
    )?;
    let campaign_started = Instant::now();
    let mut records = Vec::new();
    let mut global: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut totals: BTreeMap<String, usize> = BTreeMap::new();
    for (id, path, source) in sources {
        let before = input::identity(&path)?;
        if before.sha256 != source.sha256 || before.bytes != source.bytes {
            return Err("input differs from frozen manifest".into());
        }
        let folder = out.join(format!("obs-{id}"));
        let cpu0 = usage()?;
        let started = Instant::now();
        let run = innovation_audio::decode_file(
            &path,
            &folder,
            &AdaptiveConfig {
                baud: 9600.0,
                threads: args.threads,
            },
            true,
        );
        let cpu1 = usage()?;
        let mut record = json!({"id":id,"wall_seconds":started.elapsed().as_secs_f64(),
            "cpu_self_seconds":cpu1.0-cpu0.0,"cpu_waited_children_seconds":cpu1.1-cpu0.1,
            "process_lifetime_peak_rss_kib":cpu1.2,"rss_is_not_per_observation_peak":true});
        match run {
            Ok(receipt) => {
                let value = input::read_json(&folder.join("result.json"))?;
                let report = &value["report"];
                let baseline = checked_frames(&report["baseline"]["union_full_frames"])?;
                let union = checked_frames(&report["union_full_frames"])?;
                if !baseline.is_subset(&union) {
                    return Err("additive union lost baseline frame".into());
                }
                let mut counts = BTreeMap::new();
                for (name, frames) in [
                    ("existing-adaptive", baseline.clone()),
                    (
                        "matched-white",
                        checked_frames(&report["lane_full_frames"]["matched-white"])?,
                    ),
                    (
                        "innovations",
                        checked_frames(&report["lane_full_frames"]["innovations"])?,
                    ),
                    (
                        "codec-innovations",
                        checked_frames(&report["lane_full_frames"]["codec-innovations"])?,
                    ),
                    ("union", union.clone()),
                ] {
                    counts.insert(name, frames.len());
                    *totals.entry(name.into()).or_default() += frames.len();
                    global.entry(name.into()).or_default().extend(frames);
                }
                record["status"] = json!("complete");
                record["counts"] = json!(counts);
                record["added_vs_existing_adaptive"] =
                    json!(union.difference(&baseline).cloned().collect::<Vec<_>>());
                record["codec_fits_accepted"] = value["codec_fits_accepted"].clone();
                record["result_identity"] = json!(input::identity(&folder.join("result.json"))?);
                record["receipt"] = receipt;
            }
            Err(reason) => {
                record["status"] = json!("failed");
                record["reason"] = json!(reason);
            }
        }
        input::write_json_new(&out.join(format!("record-{id}.json")), &record)?;
        println!(
            "{}",
            json!({"id":id,"status":record["status"],"counts":record["counts"],"wall_seconds":record["wall_seconds"]})
        );
        records.push(record);
    }
    let complete = records.iter().all(|record| record["status"] == "complete");
    input::write_json_new(
        &out.join("summary.json"),
        &json!({
            "schema":"innovation-field-development-summary-v1","status":if complete{"complete"}else{"incomplete"},
            "records":records,"observation_pdu_totals":totals,"global_full_frames":global,
            "wall_seconds":campaign_started.elapsed().as_secs_f64(),"publication_ready":false,
            "equal_cpu_advantage_established":false,"comparison_to_complete_progressive_bank":false
        }),
    )?;
    if !complete {
        return Err("some observations failed; see preserved records".into());
    }
    Ok(())
}
fn main() {
    if let Err(reason) = run(Args::parse()) {
        eprintln!("{reason}");
        std::process::exit(1);
    }
}
