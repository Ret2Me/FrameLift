//! Re-score immutable decoder outputs after a reader-only correction.
#[path = "support/today20_baselines.rs"]
mod baselines;
#[path = "satnogs_today20_run.rs"]
mod replay;
use serde_json::{Value, json};
use std::{fs, path::Path};
use telemetry_yield_rs::input;

fn run() -> Result<(), String> {
    let root = Path::new("/home/ubuntu/telemetry-yield/work/satnogs-today20-20260910-v1");
    let original = root.join("comparison");
    let output = root.join("verified");
    fs::create_dir(&output).map_err(|e| e.to_string())?;
    let cohort = input::read_json(&root.join("acquisition/cohort.json"))?;
    let mut audit = vec![];
    for row in cohort["observations"]
        .as_array()
        .ok_or("observations absent")?
    {
        let id = row["id"].as_u64().ok_or("ID absent")?;
        let olddir = original.join(format!("obs-{id}"));
        let mut result = input::read_json(&olddir.join("result.json"))?;
        if result["observation_id"] != id {
            return Err("observation mismatch".into());
        }
        for arm in ["direwolf", "gr_satellites"] {
            let receipt = input::read_json(&olddir.join(format!("{arm}.process.json")))?;
            if receipt["success"] != true {
                return Err("original decoder process failed".into());
            }
            let log = input::identity(&olddir.join(format!("{arm}.stdout.log")))?;
            if json!(log) != receipt["stdout"] {
                return Err("original stdout changed".into());
            }
            let parsed = if arm == "direwolf" {
                baselines::parse_direwolf_atest(&String::from_utf8_lossy(
                    &input::read_bytes_bounded(
                        &olddir.join("direwolf.stdout.log"),
                        64 * 1024 * 1024,
                    )?,
                ))?
            } else {
                baselines::parse_gr_satellites_kiss(&input::read_bytes_bounded(
                    &olddir.join("grsat.kiss"),
                    64 * 1024 * 1024,
                )?)?
            };
            let previous = &result["decoders"][arm];
            if previous["status"] == "complete"
                && (previous["all_payloads"] != json!(parsed.all_payloads)
                    || previous["strict_ui_payloads"] != json!(parsed.strict_ui_payloads)
                    || previous["emitted_count"] != parsed.emitted_count)
            {
                return Err("reader correction changed previously accepted results".into());
            }
            audit.push(json!({"id":id,"arm":arm,"previous_status":previous["status"],"same_previously_accepted_sets":previous["status"]=="complete",
                "original_log":log,"all_unique":parsed.all_payloads.len(),"strict_ui":parsed.strict_ui_payloads.len()}));
            result["decoders"][arm] = json!({"status":"complete","input_sha256":result["input"]["sha256"],"process":receipt,
                "emitted_count":parsed.emitted_count,"all_unique_count":parsed.all_payloads.len(),"strict_ui_unique_count":parsed.strict_ui_payloads.len(),
                "all_payloads":parsed.all_payloads,"strict_ui_payloads":parsed.strict_ui_payloads,"crc_evidence":parsed.crc_evidence});
        }
        if result["decoders"]["native"]["status"] != "complete" {
            return Err("native result incomplete".into());
        }
        result["status"] = "complete".into();
        result["reader_correction"] = json!({"original_result":input::identity(&olddir.join("result.json"))?,
            "reason":"atest omits length/type declaration for some internally CRC-verified noncanonical packets; parse fixed48-column hex bounded by markers and closing separator",
            "decoder_reexecuted":false,"native_dsp_changed":false});
        let dir = output.join(format!("obs-{id}"));
        fs::create_dir(&dir).map_err(|e| e.to_string())?;
        input::write_json_new(&dir.join("result.json"), &result)?;
    }
    input::write_json_new(
        &output.join("reader-audit.json"),
        &json!({"original_manifest":input::identity(&original.join("manifest.json"))?,
        "parser_source":input::identity(Path::new("examples/support/today20_baselines.rs"))?,
        "executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,"all40_external_outputs_reparsed":true,"rows":audit}),
    )?;
    let summary = replay::summarize(&output, &cohort)?;
    println!(
        "{}",
        json!({"complete":summary["complete"],"counts":summary["counts"],"global":summary["globally_distinct_counts"],"errors":summary["failed_or_missing"]})
    );
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
