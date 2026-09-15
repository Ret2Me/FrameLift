//! Metadata-only continuation of the exact preserved 200-page CANVAS prefix.
//! No waveform download, decoder invocation, or selection from a partial tier.
#[allow(dead_code)]
#[path = "innovation_benchmark_acquire_v4.rs"]
mod selector;

use chrono::{DateTime, Utc};
use clap::Parser;
use selector::{io, read_json};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::input;

const OLD_PLAN_SHA: &str = "4f8e8f21479e2e9a46e727c20d8074d8fd51ed5e45748af4716aff4e9993b265";
const RESUMED_PLAN_SHA: &str = "e8a5e680ceaad9dd013fd28963917cb8971848605d2dd6a10ae43b927456a435";
const EXPOSURE_SHA: &str = "624d4106a75f4e6f0ec3867800ed4b2ca9a02847aac4c83ed98a0a035a552a80";
const MAX_PAGES: usize = 300;
const PAGE_CAP: u64 = 4 * 1024 * 1024;
const CUTOFF: &str = "2026-09-10T00:00:00Z";
const WINDOWS: [(&str, &str); 3] = [
    ("2026-08-11T00:00:00Z", CUTOFF),
    ("2026-07-12T00:00:00Z", "2026-08-11T00:00:00Z"),
    ("2026-06-12T00:00:00Z", "2026-07-12T00:00:00Z"),
];

#[derive(Parser)]
struct Args {
    #[arg(long)]
    output: PathBuf,
    #[arg(long)]
    original_prefix: PathBuf,
    #[arg(long)]
    resumed_prefix: PathBuf,
    /// Verify/freeze existing metadata evidence only, making no API requests.
    #[arg(long)]
    audit_only: bool,
}

fn exact_identity(path: &Path, sha: &str) -> Result<Value, String> {
    let actual = input::identity(path)?;
    if actual.sha256 != sha {
        return Err(format!("pinned identity changed: {}", path.display()));
    }
    Ok(json!(actual))
}

fn unchanged(path: &Path, expected: &Value) -> Result<(), String> {
    let actual = input::identity(path)?;
    if expected["sha256"] != actual.sha256 || expected["bytes"].as_u64() != Some(actual.bytes) {
        return Err(format!("artifact identity mismatch: {}", path.display()));
    }
    Ok(())
}

fn cooldown_elapsed(http: &Value, now: DateTime<Utc>) -> Result<(), String> {
    for hop in http["hops"].as_array().ok_or("HTTP hops absent")? {
        if let Some(delay) = hop["shared_host_cooldown_seconds"].as_f64() {
            if !delay.is_finite() || delay < 0.0 || delay > i64::MAX as f64 / 1000.0 {
                return Err("invalid preserved cooldown".into());
            }
            let recorded = DateTime::parse_from_rfc3339(
                hop["cooldown_recorded_utc"]
                    .as_str()
                    .ok_or("cooldown time missing")?,
            )
            .map_err(|e| e.to_string())?
            .with_timezone(&Utc);
            let deadline = recorded
                .checked_add_signed(chrono::Duration::milliseconds(
                    (delay * 1000.0).ceil() as i64
                ))
                .ok_or("cooldown deadline overflow")?;
            if now < deadline {
                return Err(format!(
                    "preserved Retry-After remains active until {deadline}; no request made"
                ));
            }
        }
    }
    Ok(())
}

/// Return raw rows, canonical next link, and newly hash-bound receipt/header identities.
fn verified_page(
    dir: &Path,
    expected_url: &str,
) -> Result<(Vec<Value>, Option<String>, Value), String> {
    let body = dir.join("response.json");
    let receipt = dir.join("response.json.http.json");
    let body_identity = input::identity(&body)?;
    if body_identity.bytes > PAGE_CAP
        || fs::metadata(&receipt).map_err(|e| e.to_string())?.len() > PAGE_CAP
    {
        return Err("oversized preserved metadata body/receipt".into());
    }
    let receipt_identity = input::identity(&receipt)?;
    let http = read_json(&receipt)?;
    if http["success"] != true
        || http["result"]["identity"]["sha256"] != body_identity.sha256
        || http["result"]["identity"]["bytes"].as_u64() != Some(body_identity.bytes)
        || selector::validate_api_url(
            http["requested_url"]
                .as_str()
                .ok_or("requested URL absent")?,
        )? != expected_url
    {
        return Err("cached completion, body hash/bytes or exact cursor mismatch".into());
    }
    let now = DateTime::parse_from_rfc3339(&io::utc())
        .map_err(|e| e.to_string())?
        .with_timezone(&Utc);
    cooldown_elapsed(&http, now)?;
    let last = http["hops"]
        .as_array()
        .and_then(|h| h.last())
        .ok_or("final HTTP hop absent")?;
    if last["http_status"] != "200" || last["process"]["success"] != true {
        return Err("last preserved HTTP attempt was not successful 200".into());
    }
    let hop = last["hop"]
        .as_u64()
        .filter(|x| *x < 6)
        .ok_or("invalid hop index")?;
    let attempt = last["attempt"]
        .as_u64()
        .filter(|x| *x < 3)
        .ok_or("invalid attempt index")?;
    let header = dir.join(format!("response.json.hop-{hop}.try-{attempt}.headers"));
    if fs::metadata(&header).map_err(|e| e.to_string())?.len() > 256 * 1024 {
        return Err("oversized header".into());
    }
    let header_identity = input::identity(&header)?;
    let header_text = fs::read_to_string(&header).map_err(|e| e.to_string())?;
    let status = header_text
        .lines()
        .filter(|l| l.starts_with("HTTP/"))
        .last()
        .ok_or("HTTP status absent")?;
    if status.split_ascii_whitespace().nth(1) != Some("200") {
        return Err("header HTTP status is not 200".into());
    }
    let next = selector::next_page(&header_text)?;
    let parsed = read_json(&body)?;
    let rows = parsed
        .as_array()
        .filter(|rows| rows.len() <= 1000)
        .ok_or("API row array absent/oversized")?
        .clone();
    // The exact same bytes were parsed and identified; mutations during this read fail closed.
    unchanged(&body, &json!(body_identity))?;
    unchanged(&receipt, &json!(receipt_identity))?;
    unchanged(&header, &json!(header_identity))?;
    let evidence = json!({"requested_url":expected_url,"body":body_identity,"http_receipt":receipt_identity,
        "headers":header_identity,"download":http["result"],"next":next,"rows":rows.len()});
    Ok((rows, next, evidence))
}

fn tier_url(tier: usize) -> String {
    let mut query = selector::required_query();
    query.insert("start".into(), WINDOWS[tier].0.into());
    query.insert("start__lt".into(), WINDOWS[tier].1.into());
    selector::url(&query)
}

fn correct_tier_url(value: &str, tier: usize) -> Result<String, String> {
    let canonical = selector::validate_api_url(value)?;
    if !canonical.contains(&format!("start={}", selector::encode(WINDOWS[tier].0)))
        || !canonical.contains(&format!("start__lt={}", selector::encode(WINDOWS[tier].1)))
    {
        return Err("pagination changed current frozen tier".into());
    }
    Ok(canonical)
}

fn no_waveforms_or_cohort(root: &Path) -> Result<(), String> {
    if root.join("cohort.json").exists() || root.join("metadata-all.json").exists() {
        return Err(
            "expected an unfinished prefix, not an existing selection/final snapshot".into(),
        );
    }
    let mut pending = vec![root.to_path_buf()];
    let mut seen = 0usize;
    while let Some(dir) = pending.pop() {
        for entry in fs::read_dir(dir).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            seen += 1;
            if seen > 30000 {
                return Err("prefix file audit bound exceeded".into());
            }
            let kind = entry.file_type().map_err(|e| e.to_string())?;
            if kind.is_symlink() {
                return Err("symlink in frozen prefix".into());
            }
            if kind.is_dir() {
                pending.push(entry.path());
            }
            if let Some(ext) = entry.path().extension().and_then(|v| v.to_str()) {
                if ["ogg", "wav", "iq", "ci16", "cf32", "f32", "u8"].contains(&ext) {
                    return Err("waveform/decoder input artifact already present in prefix".into());
                }
            }
        }
    }
    Ok(())
}

fn freeze_prefix(
    original: &Path,
    resumed: &Path,
) -> Result<(Vec<Value>, Vec<Value>, String, Value), String> {
    no_waveforms_or_cohort(original)?;
    no_waveforms_or_cohort(resumed)?;
    let old_plan = exact_identity(&original.join("selection-plan.json"), OLD_PLAN_SHA)?;
    let resumed_plan = exact_identity(&resumed.join("selection-plan.json"), RESUMED_PLAN_SHA)?;
    let exposure = exact_identity(&original.join("prior-exposure.json"), EXPOSURE_SHA)?;
    exact_identity(&resumed.join("prior-exposure.json"), EXPOSURE_SHA)?;
    let old = read_json(&original.join("selection-plan.json"))?;
    let resumed_record = read_json(&resumed.join("selection-plan.json"))?;
    if old["plan"] != selector::legacy_plan()
        || resumed_record["plan"] != selector::legacy_plan()
        || resumed_record["reused_prefix"]["selection_plan"]["sha256"] != OLD_PLAN_SHA
        || resumed_record["reused_prefix"]["prior_exposure"]["sha256"] != EXPOSURE_SHA
    {
        return Err("pinned prefix policy/ancestry differs".into());
    }
    let progress = read_json(&resumed.join("metadata-progress.json"))?;
    if progress != json!({"pages":200,"rows":5000,"tier":0,"tier_complete":false}) {
        return Err("preserved recovery progress differs from the audited 200-page stop".into());
    }
    let mut next = tier_url(0);
    let mut seen = BTreeSet::new();
    let mut rows = vec![];
    let mut pages = vec![];
    for page in 0..200 {
        if !seen.insert(next.clone()) {
            return Err("cycle in preserved cursor chain".into());
        }
        let root = if page < 59 { original } else { resumed };
        let dir = root.join(format!("metadata/tier-0/page-{page:03}"));
        let (mut values, following, mut evidence) = verified_page(&dir, &next)?;
        evidence["tier"] = 0.into();
        evidence["page"] = page.into();
        evidence["reused"] = true.into();
        rows.append(&mut values);
        pages.push(evidence);
        next = correct_tier_url(
            following
                .as_deref()
                .ok_or("unexpected EOF within preserved unfinished prefix")?,
            0,
        )?;
    }
    if rows.len() != 5000 {
        return Err("preserved row count differs".into());
    }
    let evidence = json!({"original_plan":old_plan,"resumed_plan":resumed_plan,"prior_exposure":exposure,
        "original_pages":59,"resumed_pages":141,"total_pages":200,"rows":rows.len(),"next":next,
        "body_hashes_verified_against_original_receipts":true,"headers_newly_hashed_at_recovery":true,
        "original_header_hashes_previously_absent":true,"prefix_trees_contain_no_waveform_or_cohort":true,
        "scope":"metadata-only local provenance; not proof of global waveform nonexposure"});
    Ok((rows, pages, next, evidence))
}

fn require_eof(next: &Option<String>) -> Result<(), String> {
    if next.is_some() {
        Err("amended 300-page tier bound reached before EOF; no cohort or truncated selection written".into())
    } else {
        Ok(())
    }
}

fn run(args: Args) -> Result<(), String> {
    let original = args
        .original_prefix
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let resumed = args
        .resumed_prefix
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let (mut rows, mut pages, continued, prefix) = freeze_prefix(&original, &resumed)?;
    if let Some(parent) = args.output.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::create_dir(&args.output).map_err(|e| e.to_string())?;
    let out = args.output.canonicalize().map_err(|e| e.to_string())?;
    let amendment = json!({"schema":"innovation-metadata-page-bound-amendment-v5","created_utc":io::utc(),
        "reason":"Original 200-page cap stopped with a next cursor at 5000 rows; an administrative pagination cap is not date-window EOF.",
        "old_max_pages_per_interval":200,"new_max_pages_per_interval":MAX_PAGES,"old_max_total_pages":600,"new_max_total_pages":3*MAX_PAGES,
        "changes_only_pagination_bounds":true,"dates_target_rowrank_grouprank_exposure_outcome_filters_unchanged":true,
        "selection_requires_full_tier_eof":true,"selection_station_field":"ground_station",
        "station_field_correction_is_separate_from_bound_amendment":true,
        "no_audio_download_or_dsp_in_this_executable":true,"audio_access_before_this_amendment_by_this_executable":false,
        "global_nonexposure_claimed":false,"fresh_independent_holdout_qualified":false,
        "current_exposure_and_cross_station_pass_overlap_audit_required_before_dsp":true,
        "exposure_limitation":"Preserved exclusions predate later CANVAS private-IQ/OGG development; different public observation IDs may share those passes. Preserve original ranks/exposure for recovery, then audit current exposure and pass overlap before any confirmatory release.",
        "prefix":prefix,"new_executable":input::identity(&std::env::current_exe().map_err(|e| e.to_string())?)?});
    input::write_json_new(&out.join("page-bound-amendment.json"), &amendment)?;
    input::write_json_new(
        &out.join("prefix-audit.json"),
        &json!({"prefix":prefix,"pages":pages}),
    )?;
    let exposure = read_json(&original.join("prior-exposure.json"))?;
    // Byte-identical copy, never rescan exposure during recovery or silently update exclusions.
    fs::copy(
        original.join("prior-exposure.json"),
        out.join("prior-exposure.json"),
    )
    .map_err(|e| e.to_string())?;
    exact_identity(&out.join("prior-exposure.json"), EXPOSURE_SHA)?;
    let known = exposure["ids"]
        .as_array()
        .ok_or("exposure IDs absent")?
        .iter()
        .map(|id| id.as_u64().ok_or("invalid frozen exposure ID"))
        .collect::<Result<BTreeSet<_>, _>>()?;
    let frozen = json!({"created_utc":io::utc(),"plan":selector::recovery_plan(),
        "page_bound_amendment":input::identity(&out.join("page-bound-amendment.json"))?,
        "prefix_audit":input::identity(&out.join("prefix-audit.json"))?,"metadata_only":true,
        "acquisition_executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?});
    input::write_json_new(&out.join("selection-plan.json"), &frozen)?;
    if args.audit_only {
        return Ok(());
    }
    let mut tier_receipts = vec![];
    let mut seen = pages
        .iter()
        .filter_map(|p| p["requested_url"].as_str().map(String::from))
        .collect::<BTreeSet<_>>();
    for tier in 0..3 {
        let tier_start_rows = if tier == 0 { 0 } else { rows.len() };
        let mut next = Some(if tier == 0 {
            continued.clone()
        } else {
            tier_url(tier)
        });
        let start_page = if tier == 0 { 200 } else { 0 };
        for page in start_page..MAX_PAGES {
            let Some(current) = next.take() else { break };
            let canonical = correct_tier_url(&current, tier)?;
            if !seen.insert(canonical.clone()) {
                return Err("pagination cycle".into());
            }
            let dir = out.join(format!("metadata/tier-{tier}/page-{page:03}"));
            fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
            io::disk_guard(&out)?;
            io::fetch(&canonical, &dir.join("response.json"), PAGE_CAP)?;
            let (mut values, following, mut evidence) = verified_page(&dir, &canonical)?;
            evidence["tier"] = tier.into();
            evidence["page"] = page.into();
            evidence["reused"] = false.into();
            rows.append(&mut values);
            pages.push(evidence);
            next = following;
            io::replace_json(
                &out.join("metadata-progress.json"),
                &json!({"tier":tier,"pages":pages.len(),"rows":rows.len(),
                "reused_pages":200,"new_pages":pages.len()-200,"tier_complete":next.is_none(),"max_pages_per_interval":MAX_PAGES,
                "waveform_downloads":0,"decoder_runs":0,"cohort_frozen":false}),
            )?;
        }
        require_eof(&next)?;
        let (_, _, eligible) = selector::select(&rows, &known)?;
        tier_receipts.push(
            json!({"tier":tier,"start":WINDOWS[tier].0,"end_exclusive":WINDOWS[tier].1,
            "raw_rows":rows.len()-tier_start_rows,"cumulative_fresh_eligible":eligible,"eof":true}),
        );
        if eligible >= 500 {
            break;
        }
    }
    // Recheck every preserved and newly downloaded artifact before freezing complete metadata.
    for page in &pages {
        for key in ["body", "http_receipt", "headers"] {
            let identity = &page[key];
            unchanged(
                Path::new(
                    identity["path"]
                        .as_str()
                        .ok_or("page identity path missing")?,
                ),
                identity,
            )?;
        }
    }
    exact_identity(&original.join("selection-plan.json"), OLD_PLAN_SHA)?;
    exact_identity(&resumed.join("selection-plan.json"), RESUMED_PLAN_SHA)?;
    exact_identity(&original.join("prior-exposure.json"), EXPOSURE_SHA)?;
    input::write_json_new(
        &out.join("metadata-all.json"),
        &json!({"observations":rows,"pages":pages}),
    )?;
    let receipt = json!({"schema":"innovation-benchmark-cohort-v2","purpose":"metadata-only EOF receipt; corrected waveform selection not yet performed",
        "frozen_utc":io::utc(),"plan":selector::recovery_plan(),"full_metadata_eof":true,"selection_pending_corrected_replay":true,
        "selection_plan":input::identity(&out.join("selection-plan.json"))?,"metadata":input::identity(&out.join("metadata-all.json"))?,
        "prior_exposure":input::identity(&out.join("prior-exposure.json"))?,"page_bound_amendment":input::identity(&out.join("page-bound-amendment.json"))?,
        "tiers":tier_receipts,"metadata_page_count":pages.len(),"requested_count":500,"selected_count":Value::Null,"observations":[],
        "waveform_downloads":0,"decoder_runs":0,"publication_ready":false,"fresh_independent_holdout_qualified":false,
        "current_exposure_and_cross_station_pass_overlap_audit_required_before_dsp":true});
    input::write_json_new(&out.join("cohort.json"), &receipt)?;
    io::replace_json(
        &out.join("metadata-progress.json"),
        &json!({"pages":pages.len(),"rows":rows.len(),"reused_pages":200,
        "new_pages":pages.len()-200,"full_metadata_eof":true,"max_pages_per_interval":MAX_PAGES,"cohort_frozen":false,
        "metadata_eof_receipt":input::identity(&out.join("cohort.json"))?,"waveform_downloads":0,"decoder_runs":0}),
    )?;
    Ok(())
}

fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("metadata recovery failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn amendment_changes_only_two_bounds() {
        let mut old = selector::legacy_plan();
        old["max_pages_per_interval"] = 300.into();
        old["max_total_pages"] = 900.into();
        assert_eq!(old, selector::recovery_plan());
    }
    #[test]
    fn partial_tier_never_qualifies_as_eof() {
        assert!(require_eof(&Some(tier_url(0))).is_err());
        assert!(require_eof(&None).is_ok());
    }
    #[test]
    fn tier_cursor_cannot_change_filters_or_interval() {
        assert!(correct_tier_url(&tier_url(0), 0).is_ok());
        assert!(correct_tier_url(&tier_url(1), 0).is_err());
        assert!(correct_tier_url(&(tier_url(0) + "&status=good"), 0).is_err());
        assert!(correct_tier_url(&(tier_url(0) + "&cursor="), 0).is_err());
    }
    #[test]
    fn preserved_retry_after_never_shortened() {
        let now = DateTime::parse_from_rfc3339("2026-09-11T01:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        let receipt = json!({"hops":[{"shared_host_cooldown_seconds":3600,"cooldown_recorded_utc":"2026-09-11T00:30:00Z"}]});
        assert!(cooldown_elapsed(&receipt, now).is_err());
        assert!(cooldown_elapsed(&receipt, now + chrono::Duration::hours(1)).is_ok());
    }
    #[test]
    fn changed_or_missing_page_fails_closed() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path();
        let body = dir.join("response.json");
        let url = tier_url(0);
        assert!(verified_page(dir, &url).is_err());
        input::write_json_new(&body, &json!([])).unwrap();
        let receipt = json!({"success":true,"requested_url":url,"result":{"identity":input::identity(&body).unwrap()},
            "hops":[{"http_status":"200","hop":0,"attempt":0,"process":{"success":true}}]});
        input::write_json_new(&dir.join("response.json.http.json"), &receipt).unwrap();
        io::new_text(
            &dir.join("response.json.hop-0.try-0.headers"),
            "HTTP/1.1 200 OK\r\n\r\n",
        )
        .unwrap();
        assert!(verified_page(dir, &url).unwrap().1.is_none());
        assert!(verified_page(dir, &(url + "&cursor=other")).is_err());
        fs::write(&body, b"[1]").unwrap();
        assert!(verified_page(dir, &tier_url(0)).is_err());
    }
}
