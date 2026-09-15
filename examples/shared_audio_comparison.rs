//! One-file development comparison on identical mono48k PCM16 samples.
#[path = "support/today20_baselines.rs"]
mod baselines;
#[path = "support/holdout_run_io.rs"]
#[allow(dead_code)]
mod io;
use clap::Parser;
use serde_json::{Value, json};
use std::{collections::BTreeSet, path::PathBuf};
use telemetry_yield_rs::{input, protocol};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    input: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long)]
    decoder: PathBuf,
    #[arg(long)]
    decoder_sha256: String,
    #[arg(long)]
    profile: PathBuf,
}
fn native_pdu(result: &Value) -> Result<BTreeSet<String>, String> {
    if result["schema"] != "progressive-audio-result-v1"
        || result["complete"] != true
        || result["status"] != "complete"
    {
        return Err("native complete progressive result required".into());
    }
    let total = result["total_tasks"]
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or("native task total missing or empty")?;
    if result["completed_tasks"].as_u64() != Some(total) {
        return Err("native task coverage incomplete".into());
    }
    let frames = result["frame_with_fcs_hex"]
        .as_array()
        .ok_or("native frame array missing")?;
    let mut pdus = BTreeSet::new();
    for frame in frames {
        let bytes = hex::decode(frame.as_str().ok_or("frame is not hex text")?)
            .map_err(|e| e.to_string())?;
        if bytes.len() < 2
            || !protocol::valid_ax25_fcs(&bytes)
            || !protocol::valid_ax25_ui(&bytes[..bytes.len() - 2])
        {
            return Err("native frame fails received FCS/UI checks".into());
        }
        if !pdus.insert(hex::encode(&bytes[..bytes.len() - 2])) {
            return Err("duplicate native frame".into());
        }
    }
    if result["union_count"].as_u64() != Some(pdus.len() as u64) {
        return Err("native union mismatch".into());
    }
    Ok(pdus)
}
fn pinned_entrypoint(plan: &Value, receiver: &str, observed: &str) -> Result<(), String> {
    let field = match receiver {
        "direwolf" => "direwolf_entrypoint",
        "gr_satellites" => "gr_satellites_entrypoint",
        "rust_progressive" => "decoder",
        _ => return Err("unknown comparison receiver".into()),
    };
    if plan[field]["sha256"].as_str() != Some(observed) {
        return Err(format!(
            "{receiver} entrypoint changed since comparison plan"
        ));
    }
    Ok(())
}
fn direwolf_bytes(bytes: &[u8]) -> Result<baselines::BaselinePdus, String> {
    // Monitor payload is arbitrary binary. Only the fixed-column ASCII hex
    // dump is authoritative; replacement characters there remain an error.
    baselines::parse_direwolf_atest(&String::from_utf8_lossy(bytes))
}
fn run(a: Args) -> Result<(), String> {
    let source = input::identity(&a.input)?;
    let decoder = input::identity(&a.decoder)?;
    let profile = input::identity(&a.profile)?;
    // This bounded runner accepts the JSON-encoded SatYAML profile used in
    // the control, not an arbitrary satellite's decoder or audio transport.
    let profile_doc = input::read_json(&a.profile)?;
    let transmitters = profile_doc["transmitters"]
        .as_object()
        .ok_or("transmitters missing")?;
    if transmitters.len() != 1 {
        return Err("exactly one common-profile transmitter required".into());
    }
    let transmitter = transmitters.values().next().unwrap();
    if transmitter["modulation"] != "FSK"
        || transmitter["baudrate"].as_f64() != Some(9600.0)
        || transmitter["framing"] != "AX.25 G3RUH"
    {
        return Err("comparison profile must be FSK9600 AX.25 G3RUH".into());
    }
    if decoder.sha256 != a.decoder_sha256 {
        return Err("decoder hash mismatch".into());
    }
    let reader = hound::WavReader::open(&a.input).map_err(|e| e.to_string())?;
    let spec = reader.spec();
    if spec.channels != 1
        || spec.sample_rate != 48000
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
        || reader.len() < 8192
    {
        return Err("comparison requires mono48k PCM16 WAV with at least8192 samples".into());
    }
    let root = input::existing_new_dir(&a.output)?;
    let dw = PathBuf::from("/usr/bin/atest");
    let gr = PathBuf::from("/home/ubuntu/telemetry-yield/work/golden/env/bin/gr_satellites");
    let manifest = json!({"schema":"shared-audio-comparison-plan-v1","created_utc":io::utc(),"input":source,"decoder":decoder,"profile":profile,"direwolf_entrypoint":input::identity(&dw)?,"gr_satellites_entrypoint":input::identity(&gr)?,"scope":"Development FSK9600 AX25 G3RUH common PCM16 comparison; input is derived and its provenance belongs in companion report","order":["direwolf","gr_satellites","rust_progressive"],"native_threads":2,"process_deadline_seconds":1800,"input_quantization_is_lossless":false,"performance_is_isolated":false,"received_fcs_external":false,"publication_ready":false});
    input::write_json_new(&root.join("plan.json"), &manifest)?;
    let kiss = root.join("grsat.kiss");
    let native = root.join("native");
    let jobs = [
        (
            "direwolf",
            dw,
            vec![
                "-B".into(),
                "9600".into(),
                "-F".into(),
                "0".into(),
                "-h".into(),
                source.path.clone(),
            ],
        ),
        (
            "gr_satellites",
            gr,
            vec![
                profile.path.clone(),
                "--wavfile".into(),
                source.path.clone(),
                "--kiss_out".into(),
                kiss.display().to_string(),
                "--hexdump".into(),
            ],
        ),
        (
            "rust_progressive",
            a.decoder.clone(),
            vec![
                "decode-progressive".into(),
                "--input".into(),
                source.path.clone(),
                "--output".into(),
                native.display().to_string(),
                "--mode".into(),
                "full".into(),
                "--threads".into(),
                "2".into(),
            ],
        ),
    ];
    let mut rows = Vec::new();
    for (name, program, argv) in jobs {
        let before = input::identity(&program)?;
        pinned_entrypoint(&manifest, name, &before.sha256)?;
        if input::identity(&a.input)?.sha256 != source.sha256
            || input::identity(&a.profile)?.sha256 != profile.sha256
            || input::identity(&a.decoder)?.sha256 != decoder.sha256
        {
            return Err("frozen comparison inputs changed".into());
        }
        let process = io::execute(&program, &argv, &root, name, 1800, 1024 * 1024 * 1024)?;
        let parsed = (|| -> Result<Value, String> {
            if process["success"] != true {
                return Err("process did not complete successfully".into());
            }
            if input::identity(&a.input)?.sha256 != source.sha256
                || input::identity(&program)?.sha256 != before.sha256
                || input::identity(&a.profile)?.sha256 != profile.sha256
            {
                return Err("artifact changed during receiver run".into());
            }
            if name == "direwolf" {
                let bytes =
                    input::read_bytes_bounded(&root.join("direwolf.stdout.log"), 64 * 1024 * 1024)?;
                serde_json::to_value(direwolf_bytes(&bytes)?).map_err(|e| e.to_string())
            } else if name == "gr_satellites" {
                let bytes = input::read_bytes_bounded(&kiss, 64 * 1024 * 1024)?;
                serde_json::to_value(baselines::parse_gr_satellites_kiss(&bytes)?)
                    .map_err(|e| e.to_string())
            } else {
                let result = input::read_json(&native.join("result.json"))?;
                let session = input::read_json(&native.join("manifest.json"))?;
                if session["audio"]["source"]["sha256"] != source.sha256
                    || session["executable_sha256"] != decoder.sha256
                {
                    return Err("native session input/executable mismatch".into());
                }
                let pdus = native_pdu(&result)?;
                Ok(
                    json!({"strict_ui_payloads":pdus,"all_payloads":pdus,"received_fcs_present":true,"crc_evidence":"received FCS checked by this library; independent scientific audit separate","result":input::identity(&native.join("result.json"))?}),
                )
            }
        })();
        rows.push(match parsed { Ok(value)=>json!({"receiver":name,"status":"complete","pdus":value,"process":process}),Err(error)=>json!({"receiver":name,"status":"incomplete","pdus":null,"error":error,"process":process}) });
        io::replace_json(&root.join("progress.json"), &json!({"receivers":rows}))?;
    }
    let complete = rows.iter().all(|r| r["status"] == "complete");
    input::write_json_new(
        &root.join("result.json"),
        &json!({"schema":"shared-audio-comparison-result-v1","plan":input::identity(&root.join("plan.json"))?,"input":source,"status":if complete{"complete"}else{"incomplete"},"receivers":rows,"publication_ready":false,"archive_novelty_established":false}),
    )?;
    if complete {
        Ok(())
    } else {
        Err("incomplete comparison retained".into())
    }
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
    #[test]
    fn binary_monitor_does_not_replace_authoritative_hex_bytes() {
        let valid = b"Fix Bits level = 0\nDECODED[1]\nmonitor \xff\xfe\nU frame UI, length = 1\n000:  ff                                              .\n1 packets decoded in 1 seconds\n";
        let parsed = direwolf_bytes(valid).unwrap();
        assert_eq!(parsed.all_payloads, BTreeSet::from(["ff".to_string()]));
        let mut corrupted = valid.to_vec();
        let at = corrupted.windows(5).position(|b| b == b"000: ").unwrap() + 6;
        corrupted[at] = 0xff;
        assert!(direwolf_bytes(&corrupted).is_err());
    }
    #[test]
    fn each_entrypoint_is_bound_to_plan_not_only_execution_interval() {
        let plan = json!({"decoder":{"sha256":"native"},"direwolf_entrypoint":{"sha256":"dw"},"gr_satellites_entrypoint":{"sha256":"gr"}});
        for (receiver, expected) in [
            ("rust_progressive", "native"),
            ("direwolf", "dw"),
            ("gr_satellites", "gr"),
        ] {
            assert!(pinned_entrypoint(&plan, receiver, expected).is_ok());
            assert!(pinned_entrypoint(&plan, receiver, "changed").is_err());
            assert!(pinned_entrypoint(&json!({}), receiver, expected).is_err());
        }
        assert!(pinned_entrypoint(&plan, "unlisted", "native").is_err());
    }
    #[test]
    fn incomplete_is_never_empty_success() {
        assert!(native_pdu(&json!({"schema":"progressive-audio-result-v1","complete":false,"status":"partial","union_count":0,"frame_with_fcs_hex":[]})).is_err());
    }
    #[test]
    fn missing_or_forged_counts_fail() {
        assert!(native_pdu(&json!({"schema":"progressive-audio-result-v1","complete":true,"status":"complete","union_count":1,"frame_with_fcs_hex":[]})).is_err());
        assert!(native_pdu(&json!({"schema":"progressive-audio-result-v1","complete":true,"status":"complete","total_tasks":2,"completed_tasks":1,"union_count":0,"frame_with_fcs_hex":[]})).is_err());
    }
    #[test]
    fn complete_empty_is_explicit() {
        assert!(native_pdu(&json!({"schema":"progressive-audio-result-v1","complete":true,"status":"complete","total_tasks":1,"completed_tasks":1,"union_count":0,"frame_with_fcs_hex":[]})).unwrap().is_empty());
    }
}
