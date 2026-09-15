//! Recover the immutable API metadata prefix only; waveform selection must use
//! the separately frozen station-field-corrected v3 replay after complete EOF.
#[path = "support/innovation_acquire_io.rs"]
mod io;

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
const MAX_PAGES: usize = 200;
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
    #[arg(long, default_value = "/home/ubuntu/telemetry-yield/work")]
    work_root: PathBuf,
    #[arg(long)]
    reuse_prefix: Option<PathBuf>,
}

fn encode(text: &str) -> String {
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
fn required_query() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("transmitter_uuid".into(), TX.into()),
        ("start".into(), "2026-08-11T00:00:00Z".into()),
        ("start__lt".into(), CUTOFF.into()),
        ("format".into(), "json".into()),
    ])
}
fn url(query: &BTreeMap<String, String>) -> String {
    format!(
        "{API}?{}",
        query
            .iter()
            .map(|(k, v)| format!("{}={}", encode(k), encode(v)))
            .collect::<Vec<_>>()
            .join("&")
    )
}
fn validate_api_url(value: &str) -> Result<String, String> {
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
fn next_page(headers: &str) -> Result<Option<String>, String> {
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
fn plan() -> Value {
    json!({"schema":"innovation-benchmark-selection-v2","primary_start_utc":"2026-08-11T00:00:00Z",
        "extension_starts_utc":["2026-07-12T00:00:00Z",START],"cutoff_utc":CUTOFF,
        "norad_cat_id":68635,"mission":"CANVAS","transmitter_uuid":TX,"mode":"GMSK","baud":9600,
        "target":TARGET,"selection":"later interval tiers first; station/day stratified round-robin; SHA256 frozen ranks",
        "row_rank_prefix":"innovation-benchmark-v2:","group_rank_prefix":"innovation-benchmark-v2-group:",
        "required_time":"start >= earliest extension; start < cutoff; end > start; end <= cutoff",
        "outcome_filters":[],"do_not_filter_on":["status","waterfall_status","demoddata","decoded_frame_count"],
        "audio_field":"payload","protocol_scope":"known AX.25 G3RUH common component profile; not all SatNOGS missions or modulations",
        "max_pages_per_interval":MAX_PAGES,"max_total_pages":3*MAX_PAGES,"page_download_cap_bytes":PAGE_CAP,"ogg_download_cap_bytes":OGG_CAP,
        "total_audio_transfer_cap_bytes":TOTAL_CAP,"concurrent_downloads":4,"minimum_disk_reserve_gib":25,
        "audio_contract":{"codec":"vorbis","sample_rate":48000,"channels":1,"duration_seconds":[1,1800]},
        "selection_frozen_before_audio_download":true,"no_decoder_tuning_on_this_cohort":true})
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
    let payload = row["payload"].as_str().ok_or("no audio payload")?;
    if !io::allowed_url(payload) || !payload.split('?').next().unwrap_or("").ends_with(".ogg") {
        return Err("no allowed public OGG payload".into());
    }
    Ok((start, id))
}
fn select(
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
        let station = row.get("station_id").unwrap_or(&Value::Null).to_string();
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
fn read_json(path: &Path) -> Result<Value, String> {
    serde_json::from_reader(File::open(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}
fn cached_page(dir: &Path, canonical: &str) -> Result<Value, String> {
    let http = read_json(&dir.join("response.json.http.json"))?;
    let actual = input::identity(&dir.join("response.json"))?;
    if http["success"] != true
        || http["result"]["identity"]["sha256"] != actual.sha256
        || http["result"]["identity"]["bytes"].as_u64() != Some(actual.bytes)
        || validate_api_url(http["requested_url"].as_str().ok_or("cached URL missing")?)?
            != canonical
    {
        return Err("cached page completion, identity or cursor mismatch".into());
    }
    Ok(http["result"].clone())
}

fn snapshot(out: &Path, work: &Path, prefix: Option<&Path>) -> Result<Value, String> {
    let exposure = if let Some(prefix) = prefix {
        if input::identity(&prefix.join("selection-plan.json"))?.sha256
            != "4f8e8f21479e2e9a46e727c20d8074d8fd51ed5e45748af4716aff4e9993b265"
        {
            return Err(
                "recovery is bound to the preserved September10 prefix and its cooldown".into(),
            );
        }
        let old_plan = read_json(&prefix.join("selection-plan.json"))?;
        if old_plan["plan"] != plan() {
            return Err("reused metadata prefix policy differs".into());
        }
        // The preserved response's Retry-After:2863 expired at this UTC bound.
        // Recovery cannot be used to bypass that wait, even if run early.
        let not_before =
            DateTime::parse_from_rfc3339("2026-09-10T21:47:31Z").map_err(|e| e.to_string())?;
        if DateTime::parse_from_rfc3339(&io::utc()).map_err(|e| e.to_string())? < not_before {
            return Err("preserved server cooldown has not elapsed".into());
        }
        read_json(&prefix.join("prior-exposure.json"))?
    } else {
        prior_exposure(work, out)?
    };
    input::write_json_new(&out.join("prior-exposure.json"), &exposure)?;
    let known = exposure["ids"]
        .as_array()
        .ok_or("exposure missing IDs")?
        .iter()
        .filter_map(Value::as_u64)
        .collect();
    let prefix_evidence = prefix.map(|p| -> Result<Value,String> { Ok(json!({
        "path":p,"selection_plan":input::identity(&p.join("selection-plan.json"))?,
        "prior_exposure":input::identity(&p.join("prior-exposure.json"))?,
        "qualification":"reuse complete SHA-verified cursor-linked pages only; incomplete page retried after original cooldown; old station selector is not for waveform selection"
    })) }).transpose()?;
    let frozen_plan = json!({"created_utc":io::utc(),"plan":plan(),"reused_prefix":prefix_evidence,"acquisition_executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?});
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
            let relative = format!("metadata/tier-{tier}/page-{page:03}");
            let cached = prefix
                .map(|p| p.join(&relative))
                .filter(|p| p.join("response.json").is_file());
            let (dir, receipt) = if let Some(dir) = cached {
                let receipt = cached_page(&dir, &canonical)?;
                (dir, receipt)
            } else {
                let dir = out.join(&relative);
                fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
                io::disk_guard(out)?;
                let receipt = io::fetch(&canonical, &dir.join("response.json"), PAGE_CAP)?;
                (dir, receipt)
            };
            let target = dir.join("response.json");
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

fn verify_audio(path: &Path, dir: &Path) -> Result<Value, String> {
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
    Ok(
        json!({"codec_name":"vorbis","sample_rate":48000,"channels":1,"duration_seconds":duration,"probe":process}),
    )
}
fn download_one(out: &Path, row: &Value) -> Result<Value, String> {
    let (_, id) = eligible(row)?;
    let dir = out.join(format!("obs-{id}"));
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let target = dir.join("capture.ogg");
    let receipt = dir.join("downloaded.json");
    if receipt.exists() {
        let old = read_json(&receipt)?;
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
    let value = json!({"observation_id":id,"completed_utc":io::utc(),"identity":input::identity(&target)?,
        "audio_metadata":metadata,"download":download,"ogg_magic_valid":true,"original_bytes":true,
        "http_receipt":input::identity(&attempt.join("capture.ogg.http.json"))?});
    input::write_json_new(&receipt, &value)?;
    Ok(value)
}
fn download(out: &Path, cohort: &Value) -> Result<(), String> {
    use std::sync::{
        Mutex,
        atomic::{AtomicUsize, Ordering},
    };
    if cohort["plan"] != plan() {
        return Err("cohort policy differs from executable".into());
    }
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
                        match download_one(out,row) {Ok(_)=>old,Err(e)=>json!({"observation_id":id,"status":"failed_identity_recheck","error":e})}
                    } else {old}
                },Err(e)=>json!({"observation_id":id,"status":"failed_receipt_recheck","error":e})}
            } else {
                let reserved={
                    let mut used=budget.lock().unwrap();
                    if used.saturating_add(OGG_CAP)>TOTAL_CAP {false} else {*used+=OGG_CAP;true}
                };
                let value=if !reserved {json!({"observation_id":id,"status":"not_downloaded_budget","recorded_utc":io::utc(),"not_a_zero_frame_result":true})}
                else {
                    let outcome=download_one(out,row);
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
fn run(args: Args) -> Result<(), String> {
    if args.resume || !args.metadata_only || args.reuse_prefix.is_none() {
        return Err("recovery requires --metadata-only and --reuse-prefix; audio selection belongs to the corrected v3 replay".into());
    }
    let cohort = if args.resume {
        read_json(&args.output.join("cohort.json"))?
    } else {
        if let Some(parent) = args.output.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?
        }
        fs::create_dir(&args.output).map_err(|e| e.to_string())?;
        snapshot(&args.output, &args.work_root, args.reuse_prefix.as_deref())?
    };
    if args.metadata_only {
        println!(
            "frozen selected={} eligible={} cohort={}",
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
    fn cached_page_requires_success_exact_cursor_and_unchanged_body() {
        let temp = tempfile::tempdir().unwrap();
        let body = temp.path().join("response.json");
        input::write_json_new(&body, &json!([])).unwrap();
        let canonical = url(&required_query());
        let receipt = json!({"success":true,"requested_url":canonical,
            "result":{"identity":input::identity(&body).unwrap(),"url":canonical}});
        input::write_json_new(&temp.path().join("response.json.http.json"), &receipt).unwrap();
        assert!(cached_page(temp.path(), &canonical).is_ok());
        let other = format!("{canonical}&cursor=different");
        assert!(cached_page(temp.path(), &validate_api_url(&other).unwrap()).is_err());
        std::fs::write(&body, b"[1]").unwrap();
        assert!(cached_page(temp.path(), &canonical).is_err());
    }
    fn row(id: u64, date: &str, station: u64) -> Value {
        json!({"id":id,"start":format!("{date}T00:00:00Z"),"end":format!("{date}T00:10:00Z"),
        "station_id":station,"norad_cat_id":68635,"transmitter_mode":"GMSK","transmitter_baud":9600,"transmitter_uuid":TX,"transmitter":TX,
        "payload":"https://network-satnogs.freetls.fastly.net/a.ogg"})
    }
    #[test]
    fn stable_outcome_independent_selection_excludes_prior() {
        let mut rows = (1..=600)
            .map(|n| row(10_000_000 + n, "2026-08-20", n % 4))
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
}
