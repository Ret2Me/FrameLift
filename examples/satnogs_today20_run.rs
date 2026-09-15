//! Matched-input three-decoder replay. Does not select observations or tune DSP.
#[path = "support/today20_baselines.rs"]
mod baselines;
#[path = "support/holdout_run_io.rs"]
mod io;

use clap::Parser;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use telemetry_yield_rs::{input, protocol};

const NATIVE: &str =
    "/home/ubuntu/telemetry-yield/work/lossless-speed-20260910-v1/after-v2/telemetry-yield-rs";
const NATIVE_SHA: &str = "acf20e51e8cfb2b4e2264757fe8fe8ef61ae6de3d523ac3da6e02f63fb492a97";
const GRSAT: &str = "/home/ubuntu/telemetry-yield/work/golden/env/bin/gr_satellites";
const PROFILE: &str = "name: Today20 matched audio baseline\nnorad: 0\ndata:\n  &raw Raw: unknown\ntransmitters:\n  selected:\n    frequency: 437250000\n    modulation: FSK\n    baudrate: 9600\n    framing: AX.25 G3RUH\n    data:\n    - *raw\n";

#[derive(Parser)]
struct Args {
    #[arg(long)]
    acquisition: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long, default_value_t = 2)]
    workers: usize,
    #[arg(long, default_value_t = 4)]
    threads: usize,
    #[arg(long)]
    resume: bool,
    #[arg(long)]
    analyze_only: bool,
    /// Allow acquisition to finish each frozen file while earlier files decode.
    #[arg(long, default_value_t = 0)]
    acquisition_wait_seconds: u64,
}

fn independent_fcs(frame: &[u8]) -> bool {
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

fn native_pdus(
    result: &Value,
    expected: &input::Identity,
    id: u64,
) -> Result<BTreeSet<String>, String> {
    if result["status"] != "complete"
        || result["observation_id"] != id
        || result["input"]["sha256"] != expected.sha256
        || result["source"]["sha256"] != expected.sha256
        || result["bit_repair_enabled"] != false
        || result["reference_bytes_used_for_search"] != false
    {
        return Err("native completion, identity or no-repair contract mismatch".into());
    }
    let mut pdus = BTreeSet::new();
    let frames = result["report"]["union_full_frames"]
        .as_array()
        .ok_or("native frames missing")?;
    for frame in frames {
        let bytes = hex::decode(frame.as_str().ok_or("native frame not hex string")?)
            .map_err(|e| e.to_string())?;
        if !independent_fcs(&bytes) {
            return Err("native independent received FCS mismatch".into());
        }
        let payload = &bytes[..bytes.len() - 2];
        if protocol::parse_ax25_ui(payload).is_none() {
            return Err("native non-UI frame".into());
        }
        pdus.insert(hex::encode(payload));
    }
    if result["union_count"].as_u64() != Some(pdus.len() as u64) {
        return Err("native union count mismatch".into());
    }
    Ok(pdus)
}

fn verify(identity: &Value) -> Result<(), String> {
    let current = input::identity(Path::new(
        identity["path"].as_str().ok_or("identity path missing")?,
    ))?;
    if current.sha256 != identity["sha256"].as_str().ok_or("identity SHA missing")?
        || Some(current.bytes) != identity["bytes"].as_u64()
    {
        return Err("frozen artifact changed".into());
    }
    Ok(())
}

fn run_process(
    dir: &Path,
    name: &str,
    program: &str,
    args: Vec<String>,
    timeout: u64,
    cap: u64,
) -> Result<Value, String> {
    let receipt = io::execute(Path::new(program), &args, dir, name, timeout, cap)?;
    io::require_success(&receipt)?;
    Ok(receipt)
}

fn process_one(args: &Args, row: &Value, index: usize, manifest: &Value) -> Result<Value, String> {
    let id = row["id"].as_u64().ok_or("observation ID missing")?;
    let dir = args.output.join(format!("obs-{id}"));
    if dir.exists() {
        if !args.resume {
            return Err(format!("observation directory exists: {}", dir.display()));
        }
        if let Ok(saved) = input::read_json(&dir.join("result.json")) {
            if saved["observation_id"] != id {
                return Err("saved observation ID mismatch".into());
            }
            if saved["status"] == "complete" {
                verify(&saved["input"])?;
                verify(&saved["source"])?;
                return Ok(saved);
            }
        }
        // Retain the complete failed/interrupted attempt; never reuse its logs.
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_nanos();
        let previous = args
            .output
            .join(format!("obs-{id}.previous-attempt-{stamp}"));
        if previous.exists() {
            return Err("retry preservation path already exists".into());
        }
        fs::rename(&dir, &previous).map_err(|e| e.to_string())?;
    }
    fs::create_dir(&dir).map_err(|e| e.to_string())?;
    let mut state = json!({"observation_id":id,"start":row["start"],"end":row["end"],
        "station":row["ground_station"],"station_name":row["station_name"],
        "status":"running","started_utc":io::utc(),"processes":{},"decoders":{}});
    let attempt = (|| -> Result<(), String> {
        io::disk_guard(&args.output)?;
        for artifact in manifest["runtime"].as_array().ok_or("runtime absent")? {
            verify(artifact)?;
        }
        let source = args.acquisition.join(format!("obs-{id}/capture.ogg"));
        let downloaded = args.acquisition.join(format!("obs-{id}/downloaded.json"));
        let waiting = std::time::Instant::now();
        while !downloaded.is_file() && waiting.elapsed().as_secs() < args.acquisition_wait_seconds {
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
        let receipt = input::read_json(&downloaded)?;
        verify(&receipt["identity"])?;
        state["source"] = json!(input::identity(&source)?);
        if state["source"] != receipt["identity"] {
            return Err("download receipt/source mismatch".into());
        }
        let probe = run_process(
            &dir,
            "ffprobe",
            "/usr/bin/ffprobe",
            vec![
                "-v".into(),
                "error".into(),
                "-show_streams".into(),
                "-show_format".into(),
                "-of".into(),
                "json".into(),
                source.display().to_string(),
            ],
            60,
            1024 * 1024,
        )?;
        state["processes"]["ffprobe"] = probe;
        let info = input::read_json(&dir.join("ffprobe.stdout.log"))?;
        let streams = info["streams"].as_array().ok_or("missing audio stream")?;
        let seconds = info["format"]["duration"]
            .as_str()
            .ok_or("duration absent")?
            .parse::<f64>()
            .map_err(|e| e.to_string())?;
        if streams.len() != 1
            || streams[0]["codec_type"] != "audio"
            || streams[0]["channels"] != 1
            || streams[0]["sample_rate"] != "48000"
            || !seconds.is_finite()
            || !(0.2..=1800.0).contains(&seconds)
        {
            return Err("unsupported audio: requires whole mono 48 kHz 0.2..1800 s recording; no implicit downmix/resampling".into());
        }
        let wav = dir.join("shared-pcm16.wav");
        state["processes"]["convert"] = run_process(
            &dir,
            "convert",
            "/usr/bin/ffmpeg",
            vec![
                "-nostdin".into(),
                "-v".into(),
                "error".into(),
                "-n".into(),
                "-i".into(),
                source.display().to_string(),
                "-map".into(),
                "0:a:0".into(),
                "-c:a".into(),
                "pcm_s16le".into(),
                "-flags:a".into(),
                "+bitexact".into(),
                "-fflags".into(),
                "+bitexact".into(),
                wav.display().to_string(),
            ],
            120,
            512 * 1024 * 1024,
        )?;
        let reader = hound::WavReader::open(&wav).map_err(|e| e.to_string())?;
        let spec = reader.spec();
        if spec.channels != 1
            || spec.sample_rate != 48000
            || spec.bits_per_sample != 16
            || spec.sample_format != hound::SampleFormat::Int
        {
            return Err("shared PCM contract mismatch".into());
        }
        state["audio_seconds"] = json!(f64::from(reader.duration()) / 48000.0);
        drop(reader);
        let pcm = input::identity(&wav)?;
        state["input"] = json!(pcm);
        let orders = [
            ["native", "direwolf", "gr_satellites"],
            ["direwolf", "gr_satellites", "native"],
            ["gr_satellites", "native", "direwolf"],
        ];
        state["decoder_order"] = json!(orders[index % 3]);
        for arm in orders[index % 3] {
            verify(&json!(pcm))?;
            io::replace_json(
                &dir.join("progress.json"),
                &json!({"observation_id":id,"stage":arm,"updated_utc":io::utc()}),
            )?;
            let decoded = (|| -> Result<Value, String> {
                let (program, command) = match arm {
                    "native" => (
                        NATIVE,
                        vec![
                            "decode-adaptive-audio".into(),
                            "--input".into(),
                            wav.display().to_string(),
                            "--output".into(),
                            dir.join("native").display().to_string(),
                            "--observation-id".into(),
                            id.to_string(),
                            "--threads".into(),
                            args.threads.to_string(),
                            "--baud".into(),
                            "9600".into(),
                        ],
                    ),
                    "direwolf" => (
                        "/usr/bin/atest",
                        vec![
                            "-B".into(),
                            "9600".into(),
                            "-F".into(),
                            "0".into(),
                            "-h".into(),
                            wav.display().to_string(),
                        ],
                    ),
                    _ => (
                        GRSAT,
                        vec![
                            args.output.join("grsat-profile.yml").display().to_string(),
                            "--wavfile".into(),
                            wav.display().to_string(),
                            "--kiss_out".into(),
                            dir.join("grsat.kiss").display().to_string(),
                            "--hexdump".into(),
                        ],
                    ),
                };
                let receipt = run_process(&dir, arm, program, command, 1800, 512 * 1024 * 1024)?;
                verify(&json!(pcm))?;
                let (emitted, all, ui, evidence) = if arm == "native" {
                    let result = input::read_json(&dir.join("native/result.json"))?;
                    let ui = native_pdus(&result, &pcm, id)?;
                    (
                        ui.len(),
                        ui.clone(),
                        ui,
                        "received FCS independently verified; strict AX.25 UI",
                    )
                } else {
                    let parsed = if arm == "direwolf" {
                        let log = input::read_bytes_bounded(
                            &dir.join("direwolf.stdout.log"),
                            64 * 1024 * 1024,
                        )?;
                        baselines::parse_direwolf_atest(&String::from_utf8_lossy(&log))?
                    } else {
                        baselines::parse_gr_satellites_kiss(&input::read_bytes_bounded(
                            &dir.join("grsat.kiss"),
                            64 * 1024 * 1024,
                        )?)?
                    };
                    (
                        parsed.emitted_count,
                        parsed.all_payloads,
                        parsed.strict_ui_payloads,
                        parsed.crc_evidence,
                    )
                };
                Ok(
                    json!({"status":"complete","input_sha256":pcm.sha256,"process":receipt,
                    "emitted_count":emitted,"all_unique_count":all.len(),"strict_ui_unique_count":ui.len(),
                    "all_payloads":all,"strict_ui_payloads":ui,"crc_evidence":evidence}),
                )
            })();
            state["decoders"][arm] = match decoded {
                Ok(value) => value,
                Err(error) => json!({"status":"failed","error":error}),
            };
            io::replace_json(&dir.join("progress.json"), &state)?;
        }
        verify(&state["source"])?;
        verify(&state["input"])?;
        for artifact in manifest["runtime"].as_array().ok_or("runtime absent")? {
            verify(artifact)?;
        }
        Ok(())
    })();
    state["finished_utc"] = io::utc().into();
    state["status"] = if attempt.is_ok()
        && ["native", "direwolf", "gr_satellites"]
            .iter()
            .all(|arm| state["decoders"][*arm]["status"] == "complete")
    {
        "complete"
    } else {
        "failed"
    }
    .into();
    if let Err(error) = attempt {
        state["error"] = error.into();
    }
    input::write_json_new(&dir.join("result.json"), &state)?;
    Ok(state)
}

fn set(value: &Value) -> Result<BTreeSet<String>, String> {
    value
        .as_array()
        .ok_or("missing PDU set")?
        .iter()
        .map(|v| v.as_str().map(str::to_owned).ok_or("PDU not string".into()))
        .collect()
}

pub(crate) fn summarize(output: &Path, cohort: &Value) -> Result<Value, String> {
    let mut rows = Vec::new();
    let mut totals = BTreeMap::<String, usize>::new();
    let mut global = BTreeMap::<String, BTreeSet<String>>::new();
    let mut wall = BTreeMap::<String, f64>::new();
    let mut cpu = BTreeMap::<String, f64>::new();
    let mut total_audio = 0.0;
    let mut errors = Vec::new();
    let mut union_extra = 0;
    let mut union_lost = 0;
    for raw in cohort["observations"]
        .as_array()
        .ok_or("cohort observations missing")?
    {
        let id = raw["id"].as_u64().ok_or("cohort ID missing")?;
        let result_path = output.join(format!("obs-{id}/result.json"));
        let value = match input::read_json(&result_path) {
            Ok(v) => v,
            Err(e) => {
                errors.push(json!({"id":id,"error":e}));
                continue;
            }
        };
        if value["status"] != "complete" {
            errors.push(value);
            continue;
        }
        if value["observation_id"] != id
            || ["native", "direwolf", "gr_satellites"].iter().any(|arm| {
                value["decoders"][*arm]["status"] != "complete"
                    || value["decoders"][*arm]["input_sha256"] != value["input"]["sha256"]
            })
        {
            return Err(format!(
                "saved observation {id} has inconsistent completion or input identity"
            ));
        }
        verify(&value["input"])?;
        verify(&value["source"])?;
        let mut sets = BTreeMap::new();
        let mut row = json!({"id":id,"audio_seconds":value["audio_seconds"],"station":value["station"],"counts":{},"wall_seconds":{},"all_unique_counts":{}});
        total_audio += value["audio_seconds"]
            .as_f64()
            .ok_or("audio seconds missing")?;
        for arm in ["native", "direwolf", "gr_satellites"] {
            let d = &value["decoders"][arm];
            let pdus = set(&d["strict_ui_payloads"])?;
            *totals.entry(arm.into()).or_default() += pdus.len();
            global
                .entry(arm.into())
                .or_default()
                .extend(pdus.iter().cloned());
            let elapsed = d["process"]["wall_seconds"]
                .as_f64()
                .ok_or("wall seconds missing")?;
            *wall.entry(arm.into()).or_default() += elapsed;
            *cpu.entry(arm.into()).or_default() +=
                d["process"]["user_cpu_seconds"].as_f64().unwrap_or(0.0)
                    + d["process"]["system_cpu_seconds"].as_f64().unwrap_or(0.0);
            row["counts"][arm] = pdus.len().into();
            row["all_unique_counts"][arm] = d["all_unique_count"].clone();
            row["wall_seconds"][arm] = elapsed.into();
            sets.insert(arm, pdus);
        }
        let native = &sets["native"];
        for baseline in ["direwolf", "gr_satellites"] {
            row[format!("native_only_vs_{baseline}")] = json!(
                native
                    .difference(&sets[baseline])
                    .cloned()
                    .collect::<BTreeSet<_>>()
            );
            row[format!("{baseline}_only_vs_native")] = json!(
                sets[baseline]
                    .difference(native)
                    .cloned()
                    .collect::<BTreeSet<_>>()
            );
        }
        let ext: BTreeSet<_> = sets["direwolf"]
            .union(&sets["gr_satellites"])
            .cloned()
            .collect();
        let extra: BTreeSet<_> = native.difference(&ext).cloned().collect();
        let lost: BTreeSet<_> = ext.difference(native).cloned().collect();
        union_extra += extra.len();
        union_lost += lost.len();
        row["native_only_vs_both"] = json!(extra);
        row["external_only_vs_native"] = json!(lost);
        rows.push(row);
    }
    let global_counts: BTreeMap<_, _> = global.iter().map(|(k, v)| (k.clone(), v.len())).collect();
    let summary = json!({"schema":"satnogs-today20-threeway-v1","updated_utc":io::utc(),
        "selected":cohort["observations"].as_array().unwrap().len(),"complete":rows.len(),"failed_or_missing":errors.len(),
        "scope":"CANVAS GMSK9600 AX.25 UI, today's latest 20 completed available OGG recordings; no outcome selection",
        "primary_metric":"sum of unique (observation ID, FCS-stripped strict AX.25 UI PDU) pairs",
        "counts":totals,"globally_distinct_counts":global_counts,"global_payloads":global,
        "native_only_vs_both_observation_pdu_pairs":union_extra,"external_only_vs_native_observation_pdu_pairs":union_lost,
        "process_wall_seconds":wall,"process_cpu_seconds":cpu,"audio_seconds":total_audio,
        "timing_note":"whole decoder processes, including startup; conversion/download excluded; two concurrent observations, not isolated performance benchmark",
        "fcs_note":"native received FCS independently checked; external decoders verify internally then strip FCS; no synthesized FCS evidence",
        "reference_note":"no archive payloads used for decoding or cohort selection; extra PDUs not yet independently archive corroborated",
        "publication_ready":false,"rows":rows,"errors":errors});
    io::replace_json(&output.join("summary.json"), &summary)?;
    Ok(summary)
}

fn run() -> Result<(), String> {
    let args = Args::parse();
    if !(1..=4).contains(&args.workers) || !(1..=8).contains(&args.threads) {
        return Err("workers1..4 threads1..8 required".into());
    }
    if args.acquisition_wait_seconds > 3600 {
        return Err("acquisition wait exceeds one hour per file".into());
    }
    let cohort_path = args.acquisition.join("cohort.json");
    let cohort = input::read_json(&cohort_path)?;
    let rows = cohort["observations"]
        .as_array()
        .ok_or("cohort observations absent")?;
    if rows.len() != 20 {
        return Err("requires exactly20 frozen observations".into());
    }
    let ids: BTreeSet<_> = rows
        .iter()
        .map(|row| {
            row["id"]
                .as_u64()
                .filter(|id| *id > 0)
                .ok_or("invalid cohort ID")
        })
        .collect::<Result<_, _>>()?;
    if ids.len() != 20 {
        return Err("requires20 distinct observation IDs".into());
    }
    if args.analyze_only {
        let s = summarize(&args.output, &cohort)?;
        println!(
            "{}",
            json!({"complete":s["complete"],"counts":s["counts"],"errors":s["failed_or_missing"]})
        );
        return Ok(());
    }
    let manifest = if args.resume {
        let m = input::read_json(&args.output.join("manifest.json"))?;
        verify(&m["cohort"])?;
        if json!(input::identity(&cohort_path)?) != m["cohort"] {
            return Err("supplied acquisition cohort differs from frozen manifest".into());
        }
        if m["threads"] != args.threads || m["workers"] != args.workers {
            return Err("resume resource config mismatch".into());
        }
        for artifact in m["runtime"].as_array().ok_or("runtime absent")? {
            verify(artifact)?;
        }
        m
    } else {
        fs::create_dir(&args.output).map_err(|e| e.to_string())?;
        io::new_text(&args.output.join("grsat-profile.yml"), PROFILE)?;
        let native = input::identity(Path::new(NATIVE))?;
        if native.sha256 != NATIVE_SHA {
            return Err("native frozen SHA mismatch".into());
        }
        let mut runtime = vec![];
        for path in [
            NATIVE,
            "/usr/bin/atest",
            GRSAT,
            "/usr/bin/ffmpeg",
            "/usr/bin/ffprobe",
        ] {
            runtime.push(json!(input::identity(Path::new(path))?));
        }
        let gr_package = Path::new(
            "/home/ubuntu/telemetry-yield/work/golden/env/lib/python3.12/site-packages/satellites",
        );
        for component in [
            "components/demodulators/fsk_demodulator.py",
            "components/deframers/ax25_deframer.py",
            "hdlc_deframer.py",
            "core/gr_satellites_flowgraph.py",
        ] {
            runtime.push(json!(input::identity(&gr_package.join(component))?));
        }
        runtime.push(json!(input::identity(
            &args.output.join("grsat-profile.yml")
        )?));
        let m = json!({"created_utc":io::utc(),"cohort":input::identity(&cohort_path)?,"runtime":runtime,
            "harness":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,"workers":args.workers,"threads":args.threads,
            "acquisition_wait_seconds":args.acquisition_wait_seconds,
            "input_policy":"OGG decoded once to mono native48000Hz signed16bit PCM; exact same file passed to all; no resampling/gain normalization; quantization shared by all",
            "native_config":"frozen optimized adaptive defaults, baud9600, specified threads; no tuning on this cohort",
            "direwolf_config":"atest -B9600 -F0 -h; default G3RUH modem profile; no bit repair",
            "grsat_config":"FSK9600 AX.25 G3RUH component replay, not complete historic gr-satnogs station IQ chain",
            "timeout_per_decoder_seconds":1800,"decoder_order":"cyclic native/dw/gr, dw/gr/native, gr/native/dw",
            "network_submission":false,"selection_frozen_before_decoding":true});
        input::write_json_new(&args.output.join("manifest.json"), &m)?;
        m
    };
    let next = AtomicUsize::new(0);
    std::thread::scope(|scope| {
        for _ in 0..args.workers {
            scope.spawn(|| loop {
                let index = next.fetch_add(1,Ordering::Relaxed);
                if index >= rows.len() { break; }
                match process_one(&args,&rows[index],index,&manifest) {
                    Ok(result)=>println!("{}",json!({"id":result["observation_id"],"status":result["status"],"counts":{
                        "native":result["decoders"]["native"]["strict_ui_unique_count"],"direwolf":result["decoders"]["direwolf"]["strict_ui_unique_count"],
                        "gr_satellites":result["decoders"]["gr_satellites"]["strict_ui_unique_count"]}})),
                    Err(e)=>eprintln!("observation {}: {e}",rows[index]["id"]),
                }
            });
        }
    });
    let summary = summarize(&args.output, &cohort)?;
    println!(
        "{}",
        json!({"complete":summary["complete"],"failed_or_missing":summary["failed_or_missing"],"counts":summary["counts"]})
    );
    if summary["complete"] != 20 {
        return Err(
            "not all20 observations completed all three decoders; failures are not zero decodes"
                .into(),
        );
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn independent_fcs_known_and_corrupt() {
        let mut bytes = b"123456789".to_vec();
        bytes.extend([0x6e, 0x90]);
        assert!(independent_fcs(&bytes));
        bytes[3] ^= 1;
        assert!(!independent_fcs(&bytes));
        assert!(!independent_fcs(&[]));
    }
    #[test]
    fn pdus_are_exact_not_length_or_count_only() {
        assert_eq!(set(&json!(["00", "00", "01"])).unwrap().len(), 2);
        assert!(set(&json!([1])).is_err());
    }
}
