// Versioned metadata-only selector: preserves frozen v2 ranks/exposure, uses
// corrected ground_station stratification and the explicit 300-page recovery bound.
//! Station-field-corrected replay of full frozen metadata, then original OGG acquisition.
//! Selection is frozen before any OGG download or decoder execution.
#[path = "support/innovation_download_receipt.rs"]
mod download_receipt;
#[path = "support/innovation_acquire_io.rs"]
pub(crate) mod io;

use chrono::{DateTime, Utc};
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::input;

const API: &str = "https://network.satnogs.org/api/observations/";
const START: &str = "2026-06-12T00:00:00Z";
const CUTOFF: &str = "2026-09-10T00:00:00Z";
const TX: &str = "GCmN6RULea8dAT7Qoat8z2";
const MAX_PAGES: usize = 300;
const TARGET: usize = 500;
const TOTAL_CAP: u64 = 12 * 1024 * 1024 * 1024;
const PAGE_CAP: u64 = 4 * 1024 * 1024;
const OGG_CAP: u64 = 64 * 1024 * 1024;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
    /// Resume audio downloads from an already frozen cohort; never refetch metadata.
    #[arg(long)]
    resume: bool,
    #[arg(long)]
    metadata_only: bool,
    /// Replay only a complete frozen metadata snapshot; never refetch API pages.
    #[arg(long)]
    replay_frozen_metadata: Option<PathBuf>,
    #[arg(long, default_value = "/home/ubuntu/telemetry-yield/work")]
    work_root: PathBuf,
}

pub(crate) fn encode(text: &str) -> String {
    text.bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
                char::from(b).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect()
}
fn decode(text: &str) -> Result<String, String> {
    let mut bytes = Vec::new();
    let mut i = 0;
    while i < text.len() {
        match text.as_bytes()[i] {
            b'%' => {
                let hex = text.get(i + 1..i + 3).ok_or("bad percent escape")?;
                bytes.push(u8::from_str_radix(hex, 16).map_err(|e| e.to_string())?);
                i += 3;
            }
            b'+' => {
                bytes.push(b' ');
                i += 1;
            }
            b => {
                bytes.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8(bytes).map_err(|e| e.to_string())
}
pub(crate) fn required_query() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("transmitter_uuid".into(), TX.into()),
        ("start".into(), "2026-08-11T00:00:00Z".into()),
        ("start__lt".into(), CUTOFF.into()),
        ("format".into(), "json".into()),
    ])
}
pub(crate) fn url(query: &BTreeMap<String, String>) -> String {
    format!(
        "{API}?{}",
        query
            .iter()
            .map(|(k, v)| format!("{}={}", encode(k), encode(v)))
            .collect::<Vec<_>>()
            .join("&")
    )
}
pub(crate) fn validate_api_url(value: &str) -> Result<String, String> {
    if value.len() > 16384
        || !value.is_ascii()
        || value.bytes().any(|b| b <= 32 || b == 127)
        || value.contains(['#', '\\'])
    {
        return Err("invalid API URL".into());
    }
    let query = value
        .strip_prefix(&format!("{API}?"))
        .ok_or("pagination left exact HTTPS API endpoint")?;
    let mut parsed = BTreeMap::new();
    for part in query.split('&') {
        let (key, val) = part.split_once('=').ok_or("invalid query component")?;
        if parsed.insert(decode(key)?, decode(val)?).is_some() {
            return Err("duplicate query key".into());
        }
    }
    let required = required_query();
    for (key, val) in &required {
        if key != "start" && key != "start__lt" && parsed.get(key) != Some(val) {
            return Err(format!("changed pagination filter {key}"));
        }
    }
    let window = (
        parsed.get("start").map(String::as_str),
        parsed.get("start__lt").map(String::as_str),
    );
    if ![
        (Some("2026-08-11T00:00:00Z"), Some(CUTOFF)),
        (Some("2026-07-12T00:00:00Z"), Some("2026-08-11T00:00:00Z")),
        (Some(START), Some("2026-07-12T00:00:00Z")),
    ]
    .contains(&window)
    {
        return Err("unplanned acquisition date window".into());
    }
    if parsed
        .keys()
        .any(|key| !required.contains_key(key) && key != "cursor")
        || parsed.get("cursor").is_some_and(String::is_empty)
    {
        return Err("unplanned filter or empty cursor".into());
    }
    Ok(url(&parsed))
}
pub(crate) fn next_page(headers: &str) -> Result<Option<String>, String> {
    let mut links = Vec::new();
    let mut seen_status = false;
    for line in headers.lines() {
        if line.starts_with("HTTP/") {
            links.clear();
            seen_status = true;
        } else if line.starts_with([' ', '\t']) {
            return Err("folded header".into());
        } else if let Some((key, value)) = line.split_once(':')
            && key.eq_ignore_ascii_case("link")
        {
            links.push(value.trim());
        }
    }
    if !seen_status {
        return Err("missing HTTP status header".into());
    }
    let mut next = None;
    for item in links.iter().flat_map(|line| line.split(',')) {
        let (target, params) = item
            .trim()
            .strip_prefix('<')
            .and_then(|v| v.split_once('>'))
            .ok_or("invalid Link header")?;
        let mut relation = None;
        for param in params.split(';').filter(|v| !v.trim().is_empty()) {
            let (key, val) = param
                .trim()
                .split_once('=')
                .ok_or("invalid Link parameter")?;
            if key.trim() == "rel" {
                if relation.is_some() {
                    return Err("duplicate Link relation".into());
                }
                let val = val.trim();
                relation = Some(if let Some(val) = val.strip_prefix('"') {
                    val.strip_suffix('"').ok_or("unterminated Link relation")?
                } else {
                    val
                });
            }
        }
        if relation
            .ok_or("missing Link relation")?
            .split_ascii_whitespace()
            .any(|v| v == "next")
        {
            if next.is_some() {
                return Err("multiple next links".into());
            }
            next = Some(validate_api_url(target)?);
        }
    }
    Ok(next)
}
pub(crate) fn legacy_plan() -> Value {
    json!({"schema":"innovation-benchmark-selection-v2","primary_start_utc":"2026-08-11T00:00:00Z",
        "extension_starts_utc":["2026-07-12T00:00:00Z",START],"cutoff_utc":CUTOFF,
        "norad_cat_id":68635,"mission":"CANVAS","transmitter_uuid":TX,"mode":"GMSK","baud":9600,
        "target":TARGET,"selection":"later interval tiers first; station/day stratified round-robin; SHA256 frozen ranks",
        "row_rank_prefix":"innovation-benchmark-v2:","group_rank_prefix":"innovation-benchmark-v2-group:",
        "required_time":"start >= earliest extension; start < cutoff; end > start; end <= cutoff",
        "outcome_filters":[],"do_not_filter_on":["status","waterfall_status","demoddata","decoded_frame_count"],
        "audio_field":"payload","protocol_scope":"known AX.25 G3RUH common component profile; not all SatNOGS missions or modulations",
        "max_pages_per_interval":200,"max_total_pages":600,"page_download_cap_bytes":PAGE_CAP,"ogg_download_cap_bytes":OGG_CAP,
        "total_audio_transfer_cap_bytes":TOTAL_CAP,"concurrent_downloads":4,"minimum_disk_reserve_gib":25,
        "audio_contract":{"codec":"vorbis","sample_rate":48000,"channels":1,"duration_seconds":[1,1800]},
        "selection_frozen_before_audio_download":true,"no_decoder_tuning_on_this_cohort":true})
}
pub(crate) fn recovery_plan() -> Value {
    let mut value = legacy_plan();
    value["max_pages_per_interval"] = MAX_PAGES.into();
    value["max_total_pages"] = (3 * MAX_PAGES).into();
    value
}
fn plan() -> Value {
    let mut value = recovery_plan();
    value["schema"] = "innovation-benchmark-selection-v4".into();
    value["station_field"] = "ground_station".into();
    value["station_missing_policy"] = "ineligible; never group as null".into();
    value["fresh_independent_holdout_qualified"] = false.into();
    value["current_exposure_and_cross_station_pass_overlap_audit_required_before_dsp"] =
        true.into();
    value["correction"]="v2 selected station_id, absent from actual API rows; replay full immutable metadata using ground_station before any new audio".into();
    value
}
fn station(row: &Value) -> Result<u64, String> {
    row["ground_station"]
        .as_u64()
        .filter(|id| *id > 0)
        .ok_or_else(|| "missing or invalid actual API ground_station".into())
}
fn sha(text: &str) -> String {
    hex::encode(Sha256::digest(text.as_bytes()))
}
fn date(value: &Value, key: &str) -> Result<DateTime<Utc>, String> {
    DateTime::parse_from_rfc3339(
        value[key]
            .as_str()
            .ok_or_else(|| format!("missing {key}"))?,
    )
    .map(|v| v.with_timezone(&Utc))
    .map_err(|e| e.to_string())
}
fn eligible(row: &Value) -> Result<(DateTime<Utc>, u64), String> {
    let id = row["id"]
        .as_u64()
        .filter(|id| *id > 0)
        .ok_or("invalid id")?;
    let start = date(row, "start")?;
    let end = date(row, "end")?;
    let lower = DateTime::parse_from_rfc3339(START)
        .unwrap()
        .with_timezone(&Utc);
    let upper = DateTime::parse_from_rfc3339(CUTOFF)
        .unwrap()
        .with_timezone(&Utc);
    if start < lower || start >= upper || end <= start || end > upper {
        return Err("not completed within frozen today interval".into());
    }
    if row["norad_cat_id"].as_u64() != Some(68635)
        || row["transmitter_mode"] != "GMSK"
        || row["transmitter_baud"].as_f64() != Some(9600.0)
        || row["transmitter_uuid"] != TX
        || row["transmitter"] != TX
    {
        return Err("mismatching mission or transmitter profile".into());
    }
    station(row)?;
    let payload = row["payload"].as_str().ok_or("no audio payload")?;
    if !io::allowed_url(payload) || !payload.split('?').next().unwrap_or("").ends_with(".ogg") {
        return Err("no allowed public OGG payload".into());
    }
    Ok((start, id))
}
pub(crate) fn select(
    rows: &[Value],
    known: &BTreeSet<u64>,
) -> Result<(Vec<Value>, Vec<Value>, usize), String> {
    let mut unique = BTreeMap::new();
    let mut excluded = Vec::new();
    for (i, row) in rows.iter().enumerate() {
        let Some(id) = row["id"].as_u64().filter(|v| *v > 0) else {
            excluded.push(json!({"raw_row_index":i,"reason":"invalid_id"}));
            continue;
        };
        if let Some(old) = unique.insert(id, row) {
            if old != row {
                return Err(format!("conflicting duplicate {id}"));
            }
        }
    }
    let mut tiers: BTreeMap<u8, BTreeMap<(String, String), Vec<(String, u64, Value)>>> =
        BTreeMap::new();
    let mut eligible_count = 0;
    for (id, row) in unique {
        if known.contains(&id) {
            excluded.push(json!({"id":id,"reason":"prior_exposure"}));
            continue;
        }
        let start = match eligible(row) {
            Ok((start, _)) => start,
            Err(e) => {
                excluded.push(json!({"id":id,"reason":e}));
                continue;
            }
        };
        eligible_count += 1;
        let day = start.format("%Y-%m-%d").to_string();
        let tier = if day.as_str() >= "2026-08-11" {
            0
        } else if day.as_str() >= "2026-07-12" {
            1
        } else {
            2
        };
        let station = station(row)?.to_string();
        tiers
            .entry(tier)
            .or_default()
            .entry((day, station))
            .or_default()
            .push((
                sha(&format!("innovation-benchmark-v2:{id}")),
                id,
                row.clone(),
            ));
    }
    let mut selected = Vec::new();
    for (tier, groups) in tiers {
        let mut groups = groups
            .into_iter()
            .map(|((day, station), mut rows)| {
                rows.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
                (
                    sha(&format!("innovation-benchmark-v2-group:{day}:{station}")),
                    day,
                    station,
                    rows,
                )
            })
            .collect::<Vec<_>>();
        groups.sort_by(|a, b| (&a.0, &a.1, &a.2).cmp(&(&b.0, &b.1, &b.2)));
        let max = groups.iter().map(|g| g.3.len()).max().unwrap_or(0);
        for round in 0..max {
            for group in &groups {
                if let Some((_, id, row)) = group.3.get(round) {
                    if selected.len() < TARGET {
                        selected.push(row.clone())
                    } else {
                        excluded.push(json!({"id":id,"reason":"outside frozen target","tier":tier,"group_round":round}))
                    }
                }
            }
        }
    }
    Ok((selected, excluded, eligible_count))
}
fn ids_in(text: &str) -> Vec<u64> {
    text.split(|c: char| !c.is_ascii_digit())
        .filter(|s| (7..=9).contains(&s.len()))
        .filter_map(|s| s.parse().ok())
        .collect()
}
fn metadata_ids(value: &Value, ids: &mut BTreeSet<u64>) {
    match value {
        Value::Object(map) => {
            for key in ["observation_id", "obs_id"] {
                if let Some(id) = map
                    .get(key)
                    .and_then(Value::as_u64)
                    .filter(|id| *id >= 1_000_000 && *id < 1_000_000_000)
                {
                    ids.insert(id);
                }
            }
            if map.contains_key("start")
                && (map.contains_key("norad_cat_id") || map.contains_key("station_id"))
            {
                if let Some(id) = map.get("id").and_then(Value::as_u64) {
                    ids.insert(id);
                }
            }
            for (key, v) in map {
                if [
                    "frames",
                    "full_frames",
                    "union_full_frames",
                    "baseline_full_frames",
                    "supplemental_full_frames",
                    "demoddata",
                    "payload",
                    "payloads",
                    "samples",
                    "bits",
                    "soft",
                    "hex",
                    "data",
                    "results",
                ]
                .contains(&key.as_str())
                {
                    continue;
                }
                if ["ids", "observation_ids", "selected_ids", "completed_ids"]
                    .contains(&key.as_str())
                {
                    if let Some(list) = v.as_array() {
                        for id in list
                            .iter()
                            .filter_map(Value::as_u64)
                            .filter(|id| *id >= 1_000_000 && *id < 1_000_000_000)
                        {
                            ids.insert(id);
                        }
                    }
                } else {
                    metadata_ids(v, ids)
                }
            }
        }
        Value::Array(list) => {
            for v in list {
                metadata_ids(v, ids)
            }
        }
        _ => {}
    }
}
fn prior_exposure(work: &Path, output: &Path) -> Result<Value, String> {
    let explicit = [
        14366383, 14115025, 14956101, 14936397, 14206235, 14959745, 14963998, 14966639, 14967361,
        14967362, 14967367, 14967376, 14967384, 14967385, 14967393, 14967401, 14967406, 14967407,
        14967408, 14967410, 14967413, 14967415, 14967428, 14967432, 14967436,
    ];
    let mut known: BTreeSet<u64> = explicit.into_iter().collect();
    let mut stack = vec![work.to_path_buf()];
    let mut entries = 0usize;
    let mut json_bytes = 0u64;
    let mut sources = Vec::new();
    let mut skipped = Vec::new();
    let output = fs::canonicalize(output).map_err(|e| e.to_string())?;
    while let Some(dir) = stack.pop() {
        let listing = match fs::read_dir(&dir) {
            Ok(v) => v,
            Err(e) => {
                skipped.push(json!({"path":dir,"reason":e.to_string()}));
                continue;
            }
        };
        for entry in listing {
            let entry = entry.map_err(|e| e.to_string())?;
            entries += 1;
            if entries > 2_000_000 {
                return Err("prior exposure entry bound exceeded".into());
            }
            let path = entry.path();
            if path.starts_with(&output) {
                continue;
            }
            let ty = entry.file_type().map_err(|e| e.to_string())?;
            if ty.is_symlink() {
                skipped.push(json!({"path":path,"reason":"symlink not followed"}));
                continue;
            }
            let relative = path
                .strip_prefix(work)
                .map_err(|e| e.to_string())?
                .to_string_lossy();
            let mut found = ids_in(&relative).into_iter().collect::<BTreeSet<_>>();
            if ty.is_dir() {
                known.extend(found);
                stack.push(path);
                continue;
            }
            if !ty.is_file() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            // No waveform bytes or outcome payload values are inspected.
            if name.ends_with(".json")
                && ["cohort", "manifest", "summary", "result"]
                    .iter()
                    .any(|key| name.contains(key))
            {
                let size = entry.metadata().map_err(|e| e.to_string())?.len();
                if size <= 16 * 1024 * 1024 && json_bytes + size <= 256 * 1024 * 1024 {
                    json_bytes += size;
                    match read_json(&path) {
                        Ok(v) => metadata_ids(&v, &mut found),
                        Err(e) => skipped
                            .push(json!({"path":path,"reason":format!("metadata parse: {e}")})),
                    }
                } else {
                    skipped.push(json!({"path":path,"reason":"metadata size bound; pathname IDs still excluded"}))
                }
            }
            if !found.is_empty() {
                known.extend(&found);
                sources.push(json!({"path":path,"ids":found}));
            }
        }
    }
    Ok(
        json!({"schema":"local-prior-exposure-v2","snapshot_utc":io::utc(),"work_root":work,"explicit_ids":explicit,
        "ids":known,"entries_scanned":entries,"metadata_bytes_read":json_bytes,"sources":sources,"limitations":skipped,
        "scope":"existing in-scope path IDs plus metadata identifier keys from bounded aggregate research JSON; no waveform bytes or frame payload values inspected",
        "proof_of_global_nonexposure":false}),
    )
}
pub(crate) fn read_json(path: &Path) -> Result<Value, String> {
    serde_json::from_reader(File::open(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}
fn snapshot(out: &Path, work: &Path) -> Result<Value, String> {
    let exposure = prior_exposure(work, out)?;
    input::write_json_new(&out.join("prior-exposure.json"), &exposure)?;
    let known = exposure["ids"]
        .as_array()
        .ok_or("exposure missing IDs")?
        .iter()
        .filter_map(Value::as_u64)
        .collect();
    let frozen_plan = json!({"created_utc":io::utc(),"plan":plan(),"acquisition_executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?});
    input::write_json_new(&out.join("selection-plan.json"), &frozen_plan)?;
    let mut seen = BTreeSet::new();
    let mut rows = Vec::new();
    let mut pages = Vec::new();
    let mut tier_receipts = Vec::new();
    for (tier, (lower, upper)) in [
        ("2026-08-11T00:00:00Z", CUTOFF),
        ("2026-07-12T00:00:00Z", "2026-08-11T00:00:00Z"),
        (START, "2026-07-12T00:00:00Z"),
    ]
    .into_iter()
    .enumerate()
    {
        let mut query = required_query();
        query.insert("start".into(), lower.into());
        query.insert("start__lt".into(), upper.into());
        let mut next = Some(url(&query));
        let tier_start = rows.len();
        for page in 0..MAX_PAGES {
            let Some(current) = next.take() else { break };
            let canonical = validate_api_url(&current)?;
            // Canonical pagination must retain this tier's two exact bounds.
            if !canonical.contains(&format!("start={}", encode(lower)))
                || !canonical.contains(&format!("start__lt={}", encode(upper)))
            {
                return Err("pagination changed current tier".into());
            }
            if !seen.insert(canonical.clone()) {
                return Err("pagination cycle".into());
            }
            let dir = out.join(format!("metadata/tier-{tier}/page-{page:03}"));
            fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
            io::disk_guard(out)?;
            let target = dir.join("response.json");
            let receipt = io::fetch(&canonical, &target, PAGE_CAP)?;
            let http = read_json(&dir.join("response.json.http.json"))?;
            let last = http["hops"]
                .as_array()
                .and_then(|v| v.last())
                .ok_or("missing final HTTP hop")?;
            let header = dir.join(format!(
                "response.json.hop-{}.try-{}.headers",
                last["hop"], last["attempt"]
            ));
            if fs::metadata(&header).map_err(|e| e.to_string())?.len() > 256 * 1024 {
                return Err("oversized metadata headers".into());
            }
            let body = read_json(&target)?;
            let values = body.as_array().ok_or("API body is not an array")?;
            if values.len() > 1000 {
                return Err("oversized API row count".into());
            }
            rows.extend(values.iter().cloned());
            next = next_page(&fs::read_to_string(&header).map_err(|e| e.to_string())?)?;
            pages.push(
                json!({"tier":tier,"page":page,"rows":values.len(),"next":next,"download":receipt}),
            );
            io::replace_json(
                &out.join("metadata-progress.json"),
                &json!({"tier":tier,"pages":pages.len(),"rows":rows.len(),"tier_complete":next.is_none()}),
            )?;
            println!(
                "metadata tier {tier}: {} page(s), {} row(s)",
                pages.len(),
                rows.len()
            );
        }
        if next.is_some() {
            return Err("per-tier pagination bound reached before EOF".into());
        }
        let (_, _, eligible) = select(&rows, &known)?;
        tier_receipts.push(json!({"tier":tier,"start":lower,"end_exclusive":upper,"raw_rows":rows.len()-tier_start,"cumulative_fresh_eligible":eligible}));
        if eligible >= TARGET {
            break;
        }
    }
    let (selected, excluded, eligible_count) = select(&rows, &known)?;
    input::write_json_new(
        &out.join("metadata-all.json"),
        &json!({"observations":rows,"pages":pages}),
    )?;
    input::write_json_new(&out.join("exclusions.json"), &excluded)?;
    let cohort = json!({"schema":"innovation-benchmark-cohort-v2","frozen_utc":io::utc(),"plan":plan(),"selection_plan":input::identity(&out.join("selection-plan.json"))?,
        "metadata":input::identity(&out.join("metadata-all.json"))?,"tiers":tier_receipts,"metadata_page_count":pages.len(),"eligible_count":eligible_count,"requested_count":TARGET,"selected_count":selected.len(),"selection_ids_sha256":sha(&selected.iter().map(|r|r["id"].to_string()).collect::<Vec<_>>().join("\n")),
        "prior_exposure":input::identity(&out.join("prior-exposure.json"))?,"observations":selected});
    input::write_json_new(&out.join("cohort.json"), &cohort)?;
    Ok(cohort)
}

pub(crate) fn verify_audio(path: &Path, dir: &Path) -> Result<Value, String> {
    let probe_executable = input::identity(Path::new("/usr/bin/ffprobe"))?;
    let args = vec![
        "-v".into(),
        "error".into(),
        "-threads".into(),
        "1".into(),
        "-show_streams".into(),
        "-show_format".into(),
        "-of".into(),
        "json".into(),
        path.display().to_string(),
    ];
    let process = io::execute(
        Path::new("/usr/bin/ffprobe"),
        &args,
        dir,
        "audio-probe",
        30,
        1024 * 1024,
    )?;
    io::require_success(&process)?;
    let value = read_json(&dir.join("audio-probe.stdout.log"))?;
    let streams = value["streams"]
        .as_array()
        .ok_or("ffprobe streams missing")?;
    if streams.len() != 1 {
        return Err("original OGG must contain exactly one stream".into());
    }
    let stream = &streams[0];
    let duration = value["format"]["duration"]
        .as_str()
        .and_then(|s| s.parse::<f64>().ok())
        .ok_or("audio duration missing")?;
    if stream["codec_name"] != "vorbis"
        || stream["sample_rate"] != "48000"
        || stream["channels"] != 1
        || !duration.is_finite()
        || !(1.0..=1800.0).contains(&duration)
    {
        return Err(format!(
            "unsupported OGG audio contract: codec={}, rate={}, channels={}, duration={duration}",
            stream["codec_name"], stream["sample_rate"], stream["channels"]
        ));
    }
    if input::identity(Path::new("/usr/bin/ffprobe"))?.sha256 != probe_executable.sha256 {
        return Err("audio probe executable changed".into());
    }
    Ok(
        json!({"codec_name":"vorbis","sample_rate":48000,"channels":1,"duration_seconds":duration,"probe":process,"probe_executable":probe_executable}),
    )
}
fn prospective_download_plan() -> Result<Value, String> {
    let path = Path::new(
        "/home/ubuntu/telemetry-yield/work/innovation-prospective-20260912-v2/protocol.json",
    );
    let before = input::identity(path)?;
    if before.sha256 != "bef3cdb38676acea3ab0f10758a2e12f190bc3e2f836cd53eb53c1ac0e72994b" {
        return Err("prospective transport protocol differs from pin".into());
    }
    let raw = input::read_bytes_bounded(path, 64 * 1024 * 1024)?;
    if hex::encode(Sha256::digest(&raw)) != before.sha256 {
        return Err("prospective protocol changed during read".into());
    }
    let p: Value = serde_json::from_slice(&raw).map_err(|e| e.to_string())?;
    Ok(
        json!({"schema":"innovation-prospective-acquisition-plan-v1","norad_cat_id":68635,"mode":"GMSK","baud":9600,
        "selection":p["selection"],"metadata_filters":p["metadata_query"]["filters"],"outcome_filters":[],
        "input_contract":p["input_contract"],"resource_limits":p["resource_limits"]}),
    )
}
fn prospective_download_id(row: &Value) -> Result<u64, String> {
    let start = date(row, "start")?;
    let end = date(row, "end")?;
    let lower = DateTime::parse_from_rfc3339("2026-09-12T00:00:00Z").unwrap();
    let upper = DateTime::parse_from_rfc3339("2026-09-19T00:00:00Z").unwrap();
    if start < lower
        || start >= upper
        || end <= start
        || end > upper
        || row["norad_cat_id"] != 68635
        || row["transmitter"] != TX
        || row["transmitter_uuid"] != TX
        || row["transmitter_mode"] != "GMSK"
        || row["transmitter_baud"].as_f64() != Some(9600.0)
        || row["ground_station"].as_u64().is_none_or(|n| n == 0)
        || row["payload"]
            .as_str()
            .is_none_or(|s| !s.starts_with("https://"))
    {
        return Err("row differs from fixed prospective transport eligibility".into());
    }
    row["id"]
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or("prospective ID absent".into())
}
fn download_plan_kind(actual: &Value) -> Result<bool, String> {
    let prospective = actual["schema"] == "innovation-prospective-acquisition-plan-v1";
    if *actual
        != if prospective {
            prospective_download_plan()?
        } else {
            plan()
        }
    {
        return Err("cohort policy differs from executable".into());
    }
    Ok(prospective)
}
fn download_one(out: &Path, row: &Value, prospective: bool) -> Result<Value, String> {
    let id = if prospective {
        prospective_download_id(row)?
    } else {
        eligible(row)?.1
    };
    let dir = out.join(format!("obs-{id}"));
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let target = dir.join("capture.ogg");
    let receipt = dir.join("downloaded.json");
    if receipt.exists() {
        let old = read_json(&receipt)?;
        download_receipt::validate(&old, row)?;
        let identity =
            serde_json::to_value(input::identity(&target)?).map_err(|e| e.to_string())?;
        if old["identity"] != identity {
            return Err("existing capture receipt identity mismatch".into());
        }
        return Ok(old);
    }
    if target.exists() {
        return Err("unreceipted target already exists; no overwrite".into());
    }
    let attempt = dir.join("download-00");
    if attempt.exists() {
        return Err("prior failed attempt retained; no automatic whole-observation retry".into());
    }
    fs::create_dir(&attempt).map_err(|e| e.to_string())?;
    io::disk_guard(out)?;
    let source = attempt.join("capture.ogg");
    let download = io::fetch(row["payload"].as_str().unwrap(), &source, OGG_CAP)?;
    let mut magic = [0u8; 4];
    File::open(&source)
        .and_then(|mut f| f.read_exact(&mut magic))
        .map_err(|e| e.to_string())?;
    if &magic != b"OggS" {
        return Err("download is not an Ogg container".into());
    }
    let metadata = verify_audio(&source, &attempt)?;
    fs::hard_link(&source, &target).map_err(|e| e.to_string())?;
    let value = json!({"schema":"innovation-original-ogg-download-v1","status":"downloaded","source_url":row["payload"],"observation_id":id,"completed_utc":io::utc(),"identity":input::identity(&target)?,
        "audio_metadata":metadata,"download":download,"ogg_magic_valid":true,"original_bytes":true,
        "http_receipt":input::identity(&attempt.join("capture.ogg.http.json"))?});
    download_receipt::validate(&value, row)?;
    input::write_json_new(&receipt, &value)?;
    Ok(value)
}
/// Transport reuse only. The versioned waveform launcher must pass the repaired
/// receiver's exposure/runtime admission gate before calling this function.
pub(crate) fn download(out: &Path, cohort: &Value) -> Result<(), String> {
    use std::sync::{
        Mutex,
        atomic::{AtomicUsize, Ordering},
    };
    let prospective = download_plan_kind(&cohort["plan"])?;
    let observations = cohort["observations"]
        .as_array()
        .filter(|v| v.len() <= TARGET)
        .ok_or("invalid cohort observations")?;
    if input::identity(&out.join("selection-plan.json"))?.sha256
        != cohort["selection_plan"]["sha256"]
            .as_str()
            .ok_or("plan identity missing")?
    {
        return Err("selection plan identity mismatch".into());
    }
    let records = out.join("download-records");
    fs::create_dir_all(&records).map_err(|e| e.to_string())?;
    let progress = Mutex::new(Vec::new());
    let cursor = AtomicUsize::new(0);
    // Reserve a full 64 MiB per active request, release unused bytes only after
    // recording a final receipt. Failed partial response bytes are charged too.
    let mut prior_bytes = 0u64;
    for row in observations {
        let id = row["id"].as_u64().ok_or("missing ID")?;
        let http = out.join(format!("obs-{id}/download-00/capture.ogg.http.json"));
        if http.exists() {
            prior_bytes = prior_bytes.saturating_add(
                read_json(&http)?["downloaded_bytes"]
                    .as_u64()
                    .ok_or("missing transfer accounting")?,
            );
        } else {
            let attempt = out.join(format!("obs-{id}/download-00"));
            if attempt.exists() {
                prior_bytes = prior_bytes.saturating_add(OGG_CAP);
            }
        }
    }
    let budget = Mutex::new(prior_bytes);
    std::thread::scope(|scope| {
        for _ in 0..4 {
            scope.spawn(||loop {
            let i=cursor.fetch_add(1,Ordering::SeqCst);
            let Some(row)=observations.get(i) else{break};
            let id=row["id"].as_u64().unwrap();
            let record_path=records.join(format!("{id}.json"));
            let record=if record_path.exists() {
                match read_json(&record_path) {Ok(old)=>{
                    if old["status"]=="downloaded" {
                        match download_one(out,row,prospective) {Ok(_)=>old,Err(e)=>json!({"observation_id":id,"status":"failed_identity_recheck","error":e})}
                    } else {old}
                },Err(e)=>json!({"observation_id":id,"status":"failed_receipt_recheck","error":e})}
            } else {
                let reserved={
                    let mut used=budget.lock().unwrap();
                    if used.saturating_add(OGG_CAP)>TOTAL_CAP {false} else {*used+=OGG_CAP;true}
                };
                let value=if !reserved {json!({"observation_id":id,"status":"not_downloaded_budget","recorded_utc":io::utc(),"not_a_zero_frame_result":true})}
                else {
                    let outcome=download_one(out,row,prospective);
                    let http=out.join(format!("obs-{id}/download-00/capture.ogg.http.json"));
                    let transferred=if http.exists() {read_json(&http).ok().and_then(|v|v["downloaded_bytes"].as_u64()).unwrap_or(OGG_CAP)} else {OGG_CAP};
                    {let mut used=budget.lock().unwrap();*used=used.saturating_sub(OGG_CAP).saturating_add(transferred);}
                    match outcome {
                        Ok(receipt)=>json!({"observation_id":id,"status":"downloaded","recorded_utc":io::utc(),"receipt":receipt}),
                        Err(error)=>json!({"observation_id":id,"status":"acquisition_failed","recorded_utc":io::utc(),"error":error,"not_a_zero_frame_result":true})
                    }
                };
                if let Err(error)=input::write_json_new(&record_path,&value){eprintln!("record write failure {id}: {error}");}
                value
            };
            let mut completed=progress.lock().unwrap();completed.push(record.clone());
            let count=completed.iter().filter(|r|r["status"]=="downloaded").count();
            let value=json!({"recorded":completed.len(),"downloaded":count,"total":observations.len(),"charged_bytes":*budget.lock().unwrap(),"latest_id":id,"updated_utc":io::utc()});
            if let Err(error)=io::replace_json(&out.join("download-progress.json"),&value){eprintln!("progress write failure: {error}");}
            println!("acquisition {}/{}: {} {}",completed.len(),observations.len(),id,record["status"]);
        });
        }
    });
    let mut values = progress.into_inner().unwrap();
    values.sort_by_key(|v| v["observation_id"].as_u64());
    let success = values
        .iter()
        .filter(|v| v["status"] == "downloaded")
        .count();
    let summary = json!({"schema":"innovation-benchmark-acquisition-v2","finished_utc":io::utc(),
        "cohort":input::identity(&out.join("cohort.json"))?,"requested_count":TARGET,"selected_count":observations.len(),
        "downloaded_count":success,"not_downloaded_count":observations.len()-success,"charged_transfer_bytes":budget.into_inner().unwrap(),
        "complete_selected_cohort":success==observations.len(),"not_downloaded_are_not_zero_frames":true,"records":values});
    io::replace_json(&out.join("acquisition-complete.json"), &summary)?;
    if success == observations.len() {
        Ok(())
    } else {
        Err(format!(
            "{} of {} selected original OGG available; unavailable rows retained",
            success,
            observations.len()
        ))
    }
}

fn check_original_identity(path: &Path, expected: &Value) -> Result<(), String> {
    let actual = input::identity(path)?;
    if expected["sha256"] != actual.sha256 || expected["bytes"] != actual.bytes {
        return Err(format!(
            "immutable original metadata changed: {}",
            path.display()
        ));
    }
    Ok(())
}
pub(crate) fn exact_metadata_http(
    http: &Value,
    headers: &str,
    canonical: &str,
) -> Result<(), String> {
    if http["success"] != true {
        return Err("metadata request did not complete successfully".into());
    }
    for url_value in [
        &http["requested_url"],
        &http["result"]["url"],
        &http["result"]["final_url"],
    ] {
        if validate_api_url(
            url_value
                .as_str()
                .ok_or("metadata final/request URL absent")?,
        )? != canonical
        {
            return Err("metadata requested/final URL changed exact cursor or filters".into());
        }
    }
    let hops = http["hops"]
        .as_array()
        .filter(|v| !v.is_empty())
        .ok_or("metadata HTTP hops absent")?;
    for hop in hops {
        if validate_api_url(hop["url"].as_str().ok_or("metadata hop URL absent")?)? != canonical {
            return Err("metadata hop URL changed exact cursor or filters".into());
        }
        if hop["http_status"]
            .as_str()
            .is_some_and(|s| s.starts_with('3'))
        {
            return Err("metadata redirects are not accepted".into());
        }
    }
    let last = hops.last().unwrap();
    if last["http_status"] != "200" || last["process"]["success"] != true {
        return Err("metadata final hop is not successful HTTP200".into());
    }
    let status = headers
        .lines()
        .filter(|line| line.starts_with("HTTP/"))
        .last()
        .ok_or("metadata header status absent")?;
    if status.split_ascii_whitespace().nth(1) != Some("200") {
        return Err("metadata final header status is not HTTP200".into());
    }
    Ok(())
}
fn validate_eof_pages(metadata: &Value, old: &Value) -> Result<(), String> {
    let pages = metadata["pages"]
        .as_array()
        .ok_or("metadata pages missing")?;
    if old["full_metadata_eof"] != true
        || old["selection_pending_corrected_replay"] != true
        || pages.is_empty()
        || pages.len() > 3 * MAX_PAGES
        || old["metadata_page_count"].as_u64() != Some(pages.len() as u64)
    {
        return Err("explicit complete metadata EOF receipt absent".into());
    }
    let windows = [
        ("2026-08-11T00:00:00Z", CUTOFF),
        ("2026-07-12T00:00:00Z", "2026-08-11T00:00:00Z"),
        (START, "2026-07-12T00:00:00Z"),
    ];
    let mut tier = 0usize;
    let mut page_index = 0usize;
    let mut expected_url = url(&required_query());
    let mut joined = vec![];
    let mut seen = BTreeSet::new();
    for (index, page) in pages.iter().enumerate() {
        if page["tier"].as_u64() != Some(tier as u64)
            || page["page"].as_u64() != Some(page_index as u64)
            || page_index >= MAX_PAGES
        {
            return Err("noncontiguous or over-bound metadata page/tier".into());
        }
        let current = validate_api_url(page["requested_url"].as_str().ok_or("page URL absent")?)?;
        if !current.contains(&format!("start={}", encode(windows[tier].0)))
            || !current.contains(&format!("start__lt={}", encode(windows[tier].1)))
        {
            return Err("metadata cursor changed current date interval".into());
        }
        if current != expected_url || !seen.insert(current.clone()) {
            return Err("metadata cursor discontinuity/cycle".into());
        }
        for key in ["body", "headers", "http_receipt"] {
            let identity = page.get(key).ok_or("page identity absent")?;
            let path = Path::new(
                identity["path"]
                    .as_str()
                    .ok_or("page identity path absent")?,
            );
            let cap = if key == "headers" {
                256 * 1024
            } else {
                PAGE_CAP
            };
            if fs::symlink_metadata(path)
                .map_err(|e| e.to_string())?
                .file_type()
                .is_symlink()
            {
                return Err("symlink metadata evidence is not accepted".into());
            }
            if fs::metadata(path).map_err(|e| e.to_string())?.len() > cap {
                return Err("oversized metadata artifact".into());
            }
            check_original_identity(path, identity)?;
        }
        let body = read_json(Path::new(page["body"]["path"].as_str().unwrap()))?;
        let body_rows = body.as_array().ok_or("page body not array")?;
        if page["rows"].as_u64() != Some(body_rows.len() as u64) || body_rows.len() > 1000 {
            return Err("page row count mismatch".into());
        }
        joined.extend(body_rows.iter().cloned());
        let headers = fs::read_to_string(page["headers"]["path"].as_str().unwrap())
            .map_err(|e| e.to_string())?;
        let actual_next = next_page(&headers)?;
        if page.get("next") != Some(&json!(actual_next)) {
            return Err("header next cursor differs from frozen page".into());
        }
        let http = read_json(Path::new(page["http_receipt"]["path"].as_str().unwrap()))?;
        exact_metadata_http(&http, &headers, &current)?;
        let last = http["hops"].as_array().unwrap().last().unwrap();
        let hop = last["hop"]
            .as_u64()
            .filter(|n| *n < 6)
            .ok_or("invalid final metadata hop index")?;
        let attempt = last["attempt"]
            .as_u64()
            .filter(|n| *n < 3)
            .ok_or("invalid final metadata attempt index")?;
        let body_path = Path::new(page["body"]["path"].as_str().unwrap());
        let parent = body_path.parent().ok_or("metadata body parent absent")?;
        if body_path.file_name().and_then(|n| n.to_str()) != Some("response.json")
            || Path::new(page["http_receipt"]["path"].as_str().unwrap())
                != parent.join("response.json.http.json")
            || Path::new(page["headers"]["path"].as_str().unwrap())
                != parent.join(format!("response.json.hop-{hop}.try-{attempt}.headers"))
        {
            return Err(
                "metadata header/receipt path does not correspond to the final body hop/attempt"
                    .into(),
            );
        }
        if http["success"] != true
            || http["result"]["identity"]["sha256"] != page["body"]["sha256"]
            || http["result"]["identity"]["bytes"] != page["body"]["bytes"]
            || validate_api_url(
                http["requested_url"]
                    .as_str()
                    .ok_or("HTTP requested URL absent")?,
            )? != current
        {
            return Err("metadata HTTP completion/identity differs".into());
        }
        for key in ["body", "headers", "http_receipt"] {
            check_original_identity(Path::new(page[key]["path"].as_str().unwrap()), &page[key])?;
        }
        if let Some(next) = actual_next {
            if index + 1 == pages.len() {
                return Err("full metadata EOF missing; never select a partial prefix".into());
            }
            expected_url = next;
            page_index += 1;
        } else if index + 1 < pages.len() {
            tier += 1;
            page_index = 0;
            if tier >= windows.len() {
                return Err("extra metadata tier".into());
            }
            let mut query = required_query();
            query.insert("start".into(), windows[tier].0.into());
            query.insert("start__lt".into(), windows[tier].1.into());
            expected_url = url(&query);
        }
    }
    if metadata["observations"] != json!(joined) {
        return Err("aggregate metadata rows differ from hash-verified pages".into());
    }
    Ok(())
}
fn replay(out: &Path, original: &Path) -> Result<Value, String> {
    let original = original.canonicalize().map_err(|e| e.to_string())?;
    let old_path = original.join("cohort.json");
    let old_identity = input::identity(&old_path)?;
    let old = read_json(&old_path)?;
    if old["schema"] != "innovation-benchmark-cohort-v2" || old["plan"] != recovery_plan() {
        return Err("original complete metadata freeze has different policy".into());
    }
    check_original_identity(&original.join("metadata-all.json"), &old["metadata"])?;
    check_original_identity(
        &original.join("prior-exposure.json"),
        &old["prior_exposure"],
    )?;
    check_original_identity(
        &original.join("selection-plan.json"),
        &old["selection_plan"],
    )?;
    let metadata = read_json(&original.join("metadata-all.json"))?;
    validate_eof_pages(&metadata, &old)?;
    let amendment = &old["page_bound_amendment"];
    let amendment_path = Path::new(
        amendment["path"]
            .as_str()
            .ok_or("page-bound amendment absent")?,
    );
    check_original_identity(amendment_path, amendment)?;
    let amendment_value = read_json(amendment_path)?;
    if amendment_value["schema"] != "innovation-metadata-page-bound-amendment-v5"
        || amendment_value["old_max_pages_per_interval"] != 200
        || amendment_value["new_max_pages_per_interval"] != MAX_PAGES
        || amendment_value["old_max_total_pages"] != 600
        || amendment_value["new_max_total_pages"] != 3 * MAX_PAGES
        || amendment_value["changes_only_pagination_bounds"] != true
    {
        return Err("missing or incompatible explicit page-bound amendment".into());
    }
    let rows = metadata["observations"]
        .as_array()
        .ok_or("original metadata rows absent")?;
    let pages = metadata["pages"]
        .as_array()
        .ok_or("original metadata pages absent")?;
    if pages.is_empty()
        || old["metadata_page_count"].as_u64() != Some(pages.len() as u64)
        || pages.last().ok_or("no lastpage")?["next"] != Value::Null
    {
        return Err(
            "full metadata snapshot EOF proof absent; never select from partial prefix".into(),
        );
    }
    let exposure = read_json(&original.join("prior-exposure.json"))?;
    let known = exposure["ids"]
        .as_array()
        .ok_or("original prior IDs missing")?
        .iter()
        .map(|id| id.as_u64().ok_or("invalid frozen exposure ID"))
        .collect::<Result<BTreeSet<_>, _>>()?;
    let (selected, excluded, eligible) = select(rows, &known)?;
    let amendment = json!({"schema":"station-field-correction-v4-after-page-bound-amendment","created_utc":io::utc(),
        "reason":"Actual SatNOGS API station key is ground_station, not station_id; oldselector would collapse allstations tonull.",
        "old_selection_used_for_audio_or_decoding":false,"new_audio_access_before_correction":false,
        "original_cohort_discarded_for_selection":old_identity,"original_metadata":old["metadata"],
        "original_prior_exposure":old["prior_exposure"],"new_station_field":"ground_station","page_bound_amendment":old["page_bound_amendment"],
        "dates_target_rowrank_grouprank_roundrobin_outcome_filters_unchanged":true,
        "no_extra_api_requests":true,"full_metadata_eof_required":true});
    input::write_json_new(&out.join("station-field-amendment.json"), &amendment)?;
    let frozen = json!({"created_utc":io::utc(),"plan":plan(),"acquisition_executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "original_selection_plan":old["selection_plan"],"station_field_amendment":input::identity(&out.join("station-field-amendment.json"))?});
    input::write_json_new(&out.join("selection-plan.json"), &frozen)?;
    input::write_json_new(&out.join("exclusions.json"), &excluded)?;
    let cohort = json!({"schema":"innovation-benchmark-cohort-v2","frozen_utc":io::utc(),"plan":plan(),
        "selection_plan":input::identity(&out.join("selection-plan.json"))?,"metadata":old["metadata"],"metadata_page_count":pages.len(),
        "tiers":old["tiers"],"eligible_count":eligible,"requested_count":TARGET,"selected_count":selected.len(),
        "selection_ids_sha256":sha(&selected.iter().map(|r|r["id"].to_string()).collect::<Vec<_>>().join("\n")),
        "prior_exposure":old["prior_exposure"],"station_field_amendment":input::identity(&out.join("station-field-amendment.json"))?,
        "old_selection_not_used":old_identity,"observations":selected,
        "fresh_independent_holdout_qualified":false,"current_exposure_and_cross_station_pass_overlap_audit_required_before_dsp":true,
        "waveform_downloads":0,"decoder_runs":0,"publication_ready":false});
    check_original_identity(&old_path, &json!(old_identity))?;
    check_original_identity(&original.join("metadata-all.json"), &old["metadata"])?;
    check_original_identity(
        &original.join("prior-exposure.json"),
        &old["prior_exposure"],
    )?;
    check_original_identity(
        &original.join("selection-plan.json"),
        &old["selection_plan"],
    )?;
    validate_eof_pages(&metadata, &old)?;
    input::write_json_new(&out.join("cohort.json"), &cohort)?;
    let label = out.join("original-selection-not-used-v4.json");
    if !label.exists() {
        input::write_json_new(
            &label,
            &json!({"reason":"stationfield correction beforeaudio; originalcohort retained onlyasmetadataEOF receipt",
        "original_cohort":old_identity,"corrected_cohort":input::identity(&out.join("cohort.json"))?,"original_files_unchanged":true}),
        )?;
    }
    Ok(cohort)
}
fn run(args: Args) -> Result<(), String> {
    if args.resume || !args.metadata_only {
        return Err("v4 is metadata selection only; waveform acquisition and DSP require a separately frozen run".into());
    }
    let cohort = if args.resume {
        if args.replay_frozen_metadata.is_some() {
            return Err("resume cannot resnapshot metadata".into());
        }
        read_json(&args.output.join("cohort.json"))?
    } else {
        let original = args
            .replay_frozen_metadata
            .as_deref()
            .ok_or("v4 only replays full frozen metadata with the explicit 300-page amendment; no direct API acquisition allowed")?;
        if let Some(parent) = args.output.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?
        }
        fs::create_dir(&args.output).map_err(|e| e.to_string())?;
        replay(&args.output, original)?
    };
    if args.metadata_only {
        println!(
            "frozen correctedselected={} eligible={} cohort={}",
            cohort["selected_count"],
            cohort["eligible_count"],
            args.output.join("cohort.json").display()
        );
        Ok(())
    } else {
        download(&args.output, &cohort)
    }
}

fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("acquisition failed: {error}");
        std::process::exit(1)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_metadata_http_rejects_redirects_changed_filters_missing_hops_and_bad_status() {
        let request = url(&required_query());
        let good = json!({"success":true,"requested_url":request,"result":{"url":request,"final_url":request},
            "hops":[{"url":request,"http_status":"200","hop":0,"attempt":0,"process":{"success":true}}]});
        let headers = "HTTP/1.1 200 OK\r\n\r\n";
        assert!(exact_metadata_http(&good, headers, &request).is_ok());
        for variant in 0..6 {
            let mut bad = good.clone();
            match variant {
                0 => bad["result"]["final_url"] = format!("{request}&status=good").into(),
                1 => bad["hops"][0]["url"] = format!("{request}&cursor=changed").into(),
                2 => bad["hops"] = json!([]),
                3 => bad["hops"][0]["http_status"] = "302".into(),
                4 => bad["hops"][0]["process"]["success"] = false.into(),
                _ => {
                    bad["result"].as_object_mut().unwrap().remove("final_url");
                }
            }
            assert!(
                exact_metadata_http(&bad, headers, &request).is_err(),
                "variant {variant}"
            );
        }
        assert!(exact_metadata_http(&good, "HTTP/1.1 503 Busy\r\n\r\n", &request).is_err());
    }
    #[test]
    fn standalone_eof_validation_rejects_cross_tier_cursor() {
        let mut query = required_query();
        query.insert("start".into(), "2026-07-12T00:00:00Z".into());
        query.insert("start__lt".into(), "2026-08-11T00:00:00Z".into());
        let metadata =
            json!({"pages":[{"tier":0,"page":0,"requested_url":url(&query)}],"observations":[]});
        let old = json!({"full_metadata_eof":true,"selection_pending_corrected_replay":true,"metadata_page_count":1});
        assert!(
            validate_eof_pages(&metadata, &old)
                .unwrap_err()
                .contains("date interval")
        );
    }
    fn row(id: u64, date: &str, station: u64) -> Value {
        json!({"id":id,"start":format!("{date}T00:00:00Z"),"end":format!("{date}T00:10:00Z"),
        "ground_station":station,"norad_cat_id":68635,"transmitter_mode":"GMSK","transmitter_baud":9600,"transmitter_uuid":TX,"transmitter":TX,
        "payload":"https://network-satnogs.freetls.fastly.net/a.ogg"})
    }
    #[test]
    fn prospective_transport_plan_is_exact_not_a_generic_policy_bypass() {
        assert!(!download_plan_kind(&plan()).unwrap());
        let p = prospective_download_plan().unwrap();
        assert!(download_plan_kind(&p).unwrap());
        for (key, bad) in [
            ("baud", json!(4800)),
            ("outcome_filters", json!(["good"])),
            ("resource_limits", json!({})),
            ("metadata_filters", json!({})),
        ] {
            let mut changed = p.clone();
            changed[key] = bad;
            assert!(download_plan_kind(&changed).is_err());
        }
    }
    #[test]
    fn prospective_transport_rows_keep_original_window_profile_and_outcome_blindness() {
        let r = row(1, "2026-09-12", 2);
        assert_eq!(prospective_download_id(&r).unwrap(), 1);
        let mut outcome = r.clone();
        outcome["status"] = json!("bad");
        outcome["demoddata"] = json!(["unused"]);
        assert_eq!(prospective_download_id(&outcome).unwrap(), 1);
        for (key, bad) in [
            ("start", json!("2026-09-11T00:00:00Z")),
            ("end", json!("2026-09-20T00:00:00Z")),
            ("ground_station", json!(0)),
            ("transmitter_baud", json!(4800)),
            ("transmitter", json!("unknown")),
        ] {
            let mut changed = r.clone();
            changed[key] = bad;
            assert!(prospective_download_id(&changed).is_err());
        }
        assert!(prospective_download_id(&row(2, "2026-09-19", 2)).is_err());
        assert!(
            eligible(&r).is_err(),
            "legacy historical selector must not silently gain future scope"
        );
    }
    #[test]
    fn stable_outcome_independent_selection_excludes_prior() {
        let mut rows = (1..=600)
            .map(|n| row(10_000_000 + n, "2026-08-20", n % 4 + 1))
            .collect::<Vec<_>>();
        let known = BTreeSet::from([10_000_001]);
        let original = select(&rows, &known).unwrap();
        for r in &mut rows {
            r["status"] = "good".into();
            r["demoddata"] = json!([{"payload":"do not use"}]);
        }
        rows.reverse();
        let changed = select(&rows, &known).unwrap();
        assert_eq!(original.0.len(), 500);
        assert_eq!(
            original
                .0
                .iter()
                .map(|r| r["id"].clone())
                .collect::<Vec<_>>(),
            changed
                .0
                .iter()
                .map(|r| r["id"].clone())
                .collect::<Vec<_>>()
        );
        assert!(!original.0.iter().any(|r| r["id"] == 10_000_001));
    }
    #[test]
    fn extension_never_precedes_primary_and_underfull_retained() {
        let rows = vec![
            row(10_000_001, "2026-06-20", 1),
            row(10_000_002, "2026-08-20", 1),
            row(10_000_003, "2026-07-20", 1),
        ];
        let selected = select(&rows, &BTreeSet::new()).unwrap().0;
        assert_eq!(
            selected
                .iter()
                .map(|r| r["id"].as_u64().unwrap())
                .collect::<Vec<_>>(),
            vec![10_000_002, 10_000_003, 10_000_001]
        );
    }
    #[test]
    fn no_outcome_query_and_metadata_extractor_skips_payload() {
        let base = url(&required_query());
        assert!(validate_api_url(&base).is_ok());
        assert!(validate_api_url(&(base + "&status=good")).is_err());
        let mut ids = BTreeSet::new();
        metadata_ids(
            &json!({"observation_id":14967361,"frames":[{"observation_id":19999999}],"payload":{"observation_id":18888888}}),
            &mut ids,
        );
        assert_eq!(ids, BTreeSet::from([14967361]));
    }
    #[test]
    fn actual_api_station_field_creates_distinct_strata_and_missing_rejects() {
        let rows = vec![
            row(10_000_001, "2026-08-20", 1616),
            row(10_000_002, "2026-08-20", 1616),
            row(10_000_003, "2026-08-20", 3000),
            row(10_000_004, "2026-08-20", 3000),
        ];
        let selected = select(&rows, &BTreeSet::new()).unwrap().0;
        assert_ne!(selected[0]["ground_station"], selected[1]["ground_station"]);
        let mut bad = row(10_000_005, "2026-08-20", 1616);
        bad.as_object_mut().unwrap().remove("ground_station");
        bad["station_id"] = 1616.into();
        assert!(
            eligible(&bad).is_err(),
            "absent actual API field must not collapse to null"
        );
    }
    #[test]
    fn full_snapshot_replay_is_new_and_partial_prefix_cannot_freeze() {
        for eof in [true, false] {
            let temp = tempfile::tempdir().unwrap();
            let original = temp.path().join("original");
            let out = temp.path().join("corrected");
            fs::create_dir(&original).unwrap();
            fs::create_dir(&out).unwrap();
            let rows = vec![
                row(10_000_001, "2026-08-20", 1616),
                row(10_000_002, "2026-08-20", 3000),
            ];
            let body_path = original.join("response.json");
            input::write_json_new(&body_path, &json!(rows)).unwrap();
            let header_path = original.join("response.json.hop-0.try-0.headers");
            let next = if eof {
                None
            } else {
                Some(
                    validate_api_url(&format!("{}&cursor=nextpage", url(&required_query())))
                        .unwrap(),
                )
            };
            io::new_text(
                &header_path,
                &format!(
                    "HTTP/1.1 200 OK\r\n{}\r\n",
                    next.as_ref()
                        .map(|v| format!("Link: <{v}>; rel=\"next\"\r\n"))
                        .unwrap_or_default()
                ),
            )
            .unwrap();
            let http_path = original.join("response.json.http.json");
            let request = url(&required_query());
            input::write_json_new(&http_path,&json!({"success":true,"requested_url":request,
                "result":{"identity":input::identity(&body_path).unwrap(),"url":request,"final_url":request},
                "hops":[{"url":request,"http_status":"200","hop":0,"attempt":0,"process":{"success":true}}]})).unwrap();
            input::write_json_new(&original.join("metadata-all.json"),&json!({"observations":rows,"pages":[{"tier":0,"page":0,"rows":rows.len(),"requested_url":url(&required_query()),"next":next,
                "body":input::identity(&body_path).unwrap(),"headers":input::identity(&header_path).unwrap(),"http_receipt":input::identity(&http_path).unwrap()}]})).unwrap();
            input::write_json_new(&original.join("prior-exposure.json"), &json!({"ids":[]}))
                .unwrap();
            input::write_json_new(
                &original.join("selection-plan.json"),
                &json!({"plan":recovery_plan()}),
            )
            .unwrap();
            let bound_path = original.join("page-bound-amendment.json");
            input::write_json_new(&bound_path,&json!({"schema":"innovation-metadata-page-bound-amendment-v5","old_max_pages_per_interval":200,"new_max_pages_per_interval":300,
                "old_max_total_pages":600,"new_max_total_pages":900,"changes_only_pagination_bounds":true})).unwrap();
            let old = json!({"schema":"innovation-benchmark-cohort-v2","plan":recovery_plan(),"metadata_page_count":1,
                "metadata":input::identity(&original.join("metadata-all.json")).unwrap(),
                "prior_exposure":input::identity(&original.join("prior-exposure.json")).unwrap(),
                "selection_plan":input::identity(&original.join("selection-plan.json")).unwrap(),"tiers":[],
                "full_metadata_eof":true,"selection_pending_corrected_replay":true,"page_bound_amendment":input::identity(&bound_path).unwrap()});
            input::write_json_new(&original.join("cohort.json"), &old).unwrap();
            if eof {
                let alternate = original.join("unrelated-but-valid.headers");
                fs::copy(&header_path, &alternate).unwrap();
                let mut mislabeled = read_json(&original.join("metadata-all.json")).unwrap();
                mislabeled["pages"][0]["headers"] = json!(input::identity(&alternate).unwrap());
                assert!(
                    validate_eof_pages(&mislabeled, &old)
                        .unwrap_err()
                        .contains("does not correspond")
                );
                let mut wrong_hop = read_json(&http_path).unwrap();
                wrong_hop["hops"][0]["hop"] = 99.into();
                io::replace_json(&http_path, &wrong_hop).unwrap();
                let mut invalid_index = read_json(&original.join("metadata-all.json")).unwrap();
                invalid_index["pages"][0]["http_receipt"] =
                    json!(input::identity(&http_path).unwrap());
                assert!(
                    validate_eof_pages(&invalid_index, &old)
                        .unwrap_err()
                        .contains("hop index")
                );
                wrong_hop["hops"][0]["hop"] = 0.into();
                io::replace_json(&http_path, &wrong_hop).unwrap();
                // replace_json emits the same canonical pretty JSON bytes as the original fixture.
                assert_eq!(
                    input::identity(&http_path).unwrap().sha256,
                    read_json(&original.join("metadata-all.json")).unwrap()["pages"][0]["http_receipt"]
                        ["sha256"]
                );
            }
            let before = input::identity(&original.join("cohort.json"))
                .unwrap()
                .sha256;
            let replayed = replay(&out, &original);
            if eof {
                let value = replayed.unwrap();
                assert_eq!(value["selected_count"], 2);
                assert_eq!(value["plan"]["station_field"], "ground_station");
                assert_ne!(
                    value["observations"][0]["ground_station"],
                    value["observations"][1]["ground_station"]
                );
                assert!(out.join("original-selection-not-used-v4.json").exists());
                assert!(!out.join("obs-10000001/capture.ogg").exists());
            } else {
                assert!(replayed.unwrap_err().contains("EOF"));
                assert!(!out.join("cohort.json").exists());
            }
            assert_eq!(
                before,
                input::identity(&original.join("cohort.json"))
                    .unwrap()
                    .sha256
            );
        }
    }
}
