//! Post-decode scoring only: no signal processing or receiver parameter edits.
use clap::Parser;
use serde_json::{Value, json};
use std::{collections::BTreeSet, path::PathBuf};
use telemetry_yield_rs::{input, protocol};

#[derive(Parser)]
struct Args {
    #[arg(long)]
    campaign: PathBuf,
    #[arg(long)]
    progressive_root: PathBuf,
    #[arg(long)]
    output: PathBuf,
}

fn frames(value: &Value) -> Result<BTreeSet<String>, String> {
    let result: BTreeSet<String> =
        serde_json::from_value(value.clone()).map_err(|e| e.to_string())?;
    for encoded in &result {
        let bytes = hex::decode(encoded).map_err(|e| e.to_string())?;
        if bytes.len() < 2 {
            return Err("short frame".into());
        }
        let mut crc = 0xffffu16;
        for byte in &bytes {
            for bit in 0..8 {
                let feedback = (crc ^ u16::from(byte >> bit)) & 1;
                crc >>= 1;
                if feedback != 0 {
                    crc ^= 0x8408;
                }
            }
        }
        if crc != 0xf0b8 || !protocol::valid_ax25_ui(&bytes[..bytes.len() - 2]) {
            return Err("invalid received FCS or AX25 UI in scored output".into());
        }
    }
    Ok(result)
}

fn delta(a: &BTreeSet<String>, b: &BTreeSet<String>) -> BTreeSet<String> {
    a.difference(b).cloned().collect()
}

fn pdu_bytes(frames: &BTreeSet<String>) -> usize {
    frames.iter().map(|s| s.len() / 2 - 2).sum()
}

fn information_bytes(frames: &BTreeSet<String>) -> usize {
    frames
        .iter()
        .map(|encoded| {
            let bytes = hex::decode(encoded).expect("previously validated frame");
            protocol::parse_ax25_ui(&bytes[..bytes.len() - 2])
                .expect("previously validated AX25")
                .information
                .len()
        })
        .sum()
}

fn run(args: Args) -> Result<Value, String> {
    let summary_path = args.campaign.join("summary.json");
    let summary = input::read_json(&summary_path)?;
    if summary["status"] != "complete" {
        return Err("require complete twenty-file campaign, never score failures as zero".into());
    }
    let records = summary["records"].as_array().ok_or("missing records")?;
    if records.len() != 20 {
        return Err("expected frozen twenty observations".into());
    }
    let mut seen = BTreeSet::new();
    let mut rows = Vec::new();
    let mut global_baseline = BTreeSet::new();
    let mut global_union = BTreeSet::new();
    let mut global_white = BTreeSet::new();
    let mut global_ar = BTreeSet::new();
    let mut global_codec = BTreeSet::new();
    let mut total_baseline = 0usize;
    let mut total_union = 0usize;
    let mut total_added_bytes = 0usize;
    let mut total_added_information = 0usize;
    let mut ar_additions = 0usize;
    let mut ar_omissions = 0usize;
    let mut codec_additions = 0usize;
    let mut source_fits = 0usize;
    let mut codec_accepted = 0u64;
    let mut selected_comparisons = Vec::new();
    for record in records {
        let id = record["id"].as_u64().ok_or("missing ID")?;
        if !seen.insert(id) || record["status"] != "complete" {
            return Err("duplicate or incomplete observation".into());
        }
        let path = args.campaign.join(format!("obs-{id}/result.json"));
        let identity = input::identity(&path)?;
        if record["result_identity"]["sha256"] != identity.sha256 {
            return Err("result changed after campaign commit".into());
        }
        let value = input::read_json(&path)?;
        let report = &value["report"];
        let baseline = frames(&report["baseline"]["union_full_frames"])?;
        let union = frames(&report["union_full_frames"])?;
        let white = frames(&report["lane_full_frames"]["matched-white"])?;
        let ar = frames(&report["lane_full_frames"]["innovations"])?;
        let codec = frames(&report["lane_full_frames"]["codec-innovations"])?;
        if !baseline.is_subset(&union) {
            return Err("additive output omitted baseline frame".into());
        }
        let added = delta(&union, &baseline);
        let ar_added = delta(&ar, &white);
        let ar_lost = delta(&white, &ar);
        let codec_added = delta(&codec, &ar);
        total_baseline += baseline.len();
        total_union += union.len();
        total_added_bytes += pdu_bytes(&added);
        total_added_information += information_bytes(&added);
        ar_additions += ar_added.len();
        ar_omissions += ar_lost.len();
        codec_additions += codec_added.len();
        source_fits += report["source_models"]
            .as_array()
            .ok_or("source models")?
            .len();
        codec_accepted += value["codec_fits_accepted"]
            .as_u64()
            .ok_or("codec fit count")?;
        rows.push(
            json!({"id":id,"baseline":baseline.len(),"union":union.len(),
            "matched_white":white.len(),"innovations":ar.len(),"codec":codec.len(),
            "added_vs_adaptive":added,"ar_added_vs_white":ar_added,"ar_lost_vs_white":ar_lost,
            "codec_added_vs_ar":codec_added,"result_identity":identity,
            "wall_seconds":record["wall_seconds"],"cpu_self_seconds":record["cpu_self_seconds"],
            "cpu_waited_children_seconds":record["cpu_waited_children_seconds"]}),
        );
        if !added.is_empty() {
            let folder = args.progressive_root.join(format!("progressive-{id}"));
            let old_path = folder.join("result.json");
            let previous = input::read_json(&old_path)?;
            if previous["complete"] != true {
                return Err(format!("full progressive comparison {id} is incomplete"));
            }
            let old_manifest = input::read_json(&folder.join("manifest.json"))?;
            let plan = input::read_json(&args.campaign.join(format!("obs-{id}/plan.json")))?;
            if old_manifest["audio"]["source"]["sha256"] != value["source"]["sha256"]
                || old_manifest["audio"]["wav"]["sha256"] != plan["input"]["sha256"]
            {
                return Err("comparison is not the identical source and float32 PCM".into());
            }
            let old = frames(&previous["frame_with_fcs_hex"])?;
            selected_comparisons.push(
                json!({"id":id,"selection":"post-development adaptive gainer",
                "same_original_source_and_pcm_sha256":true,"previous_full_progressive":old.len(),
                "experimental_union":union.len(),"new_only":delta(&union,&old),
                "previous_only":delta(&old,&union),"previous_full_frames":old,
                "progressive_result_identity":input::identity(&old_path)?,
                "source_sha256":value["source"]["sha256"],"pcm_sha256":plan["input"]["sha256"]}),
            );
        }
        global_baseline.extend(baseline);
        global_union.extend(union);
        global_white.extend(white);
        global_ar.extend(ar);
        global_codec.extend(codec);
    }
    let global_added = delta(&global_union, &global_baseline);
    let result = json!({"schema":"innovation-postdecode-audit-v1","status":"complete",
        "scorer_executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "campaign_summary_identity":input::identity(&summary_path)?,"rows":rows,
        "observation_pdu_counts":{"existing_adaptive":total_baseline,"union":total_union,
            "additions":total_union-total_baseline,"added_pdu_bytes_excluding_fcs":total_added_bytes,
            "added_ax25_information_bytes":total_added_information,
            "ar_added_vs_white":ar_additions,"ar_lost_vs_white":ar_omissions,
            "codec_added_vs_ar":codec_additions},
        "global_distinct_counts":{"existing_adaptive":global_baseline.len(),"union":global_union.len(),
            "additions":global_added.len(),"added_pdu_bytes_excluding_fcs":pdu_bytes(&global_added),
            "added_ax25_information_bytes":information_bytes(&global_added),
            "matched_white":global_white.len(),"innovations":global_ar.len(),"codec":global_codec.len()},
        "global_added_vs_all_twenty_adaptive":global_added,
        "global_existing_adaptive":global_baseline,"global_experimental_union":global_union,
        "source_fits_including_overlapping_duplicates":source_fits,"codec_fits_accepted":codec_accepted,
        "selected_full_progressive_comparisons":selected_comparisons,
        "selection_is_independent_holdout":false,"complete_twenty_full_progressive_comparison":false,
        "publication_ready":false,"scientific_priority_established":false,
        "false_alarm_population_qualification":false,"equal_cpu_advantage_established":false,
        "known_target_truth_used_in_demodulation":false,"scorer_runs_after_decoding":true});
    input::write_json_new(&args.output, &result)?;
    Ok(json!({"status":"complete","output":args.output,
        "observation_pdu_counts":result["observation_pdu_counts"],
        "global_distinct_counts":result["global_distinct_counts"]}))
}

fn main() {
    match run(Args::parse()) {
        Ok(value) => println!("{value}"),
        Err(reason) => {
            eprintln!("{reason}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn valid_frame(payload: &[u8]) -> Vec<u8> {
        let mut bytes = hex::decode("86829cac82a6609882a6a04040e103f0").unwrap();
        bytes.extend(payload);
        bytes.extend(protocol::crc16_x25(&bytes).to_le_bytes());
        bytes
    }
    #[test]
    fn received_crc_and_structure_are_required() {
        let good = valid_frame(b"test");
        assert_eq!(frames(&json!([hex::encode(&good)])).unwrap().len(), 1);
        let mut bad = good.clone();
        bad[16] ^= 1;
        assert!(frames(&json!([hex::encode(&bad)])).is_err());
        let mut malformed = vec![0u8; 20];
        malformed.extend(protocol::crc16_x25(&malformed).to_le_bytes());
        assert!(frames(&json!([hex::encode(malformed)])).is_err());
        assert!(frames(&json!(["00"])).is_err());
    }
    #[test]
    fn deduplication_and_byte_units_are_explicit() {
        let a = hex::encode(valid_frame(b"abcd"));
        let b = hex::encode(valid_frame(b"efgh"));
        let base = frames(&json!([a.clone(), a.clone()])).unwrap();
        let extended = frames(&json!([a, b])).unwrap();
        let added = delta(&extended, &base);
        assert_eq!(added.len(), 1);
        assert_eq!(pdu_bytes(&added), 20);
        assert_eq!(information_bytes(&added), 4);
    }
}
