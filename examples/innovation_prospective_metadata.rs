//! Fixed-week metadata acquisition/selection. No waveform or receiver execution.
//! A complete snapshot is still NOT an independent-cohort release.
#[allow(dead_code)]
#[path = "support/innovation_acquire_io.rs"]
mod io;
use chrono::{DateTime, Utc};
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::input;
const START: &str = "2026-09-12T00:00:00Z";
const END: &str = "2026-09-19T00:00:00Z";
const TX: &str = "GCmN6RULea8dAT7Qoat8z2";
const API: &str = "https://network.satnogs.org/api/observations/";
const PROTOCOL_SHA: &str = "bef3cdb38676acea3ab0f10758a2e12f190bc3e2f836cd53eb53c1ac0e72994b";
const PAGE_CAP: u64 = 4 * 1024 * 1024;
const TOTAL_CAP: u64 = 64 * 1024 * 1024;
#[derive(Parser)]
struct Args {
    #[arg(long)]
    protocol: PathBuf,
    #[arg(long)]
    amendment: PathBuf,
    #[arg(long)]
    output: PathBuf,
    /// Recheck an existing complete page inventory; never refetch it.
    #[arg(long)]
    replay_source: Option<PathBuf>,
}
fn sha(b: &[u8]) -> String {
    hex::encode(Sha256::digest(b))
}
fn time(s: &str) -> Result<i64, String> {
    DateTime::parse_from_rfc3339(s)
        .map(|t| t.timestamp())
        .map_err(|e| e.to_string())
}
fn closed_window(now: i64) -> Result<(), String> {
    if now < time(END)? {
        Err(
            "fixed observation window has not closed; no metadata/audio fetched or cohort created"
                .into(),
        )
    } else {
        Ok(())
    }
}
fn id(p: &Path) -> Result<Value, String> {
    Ok(json!(input::identity(p)?))
}
fn verify(v: &Value) -> Result<(), String> {
    let p = Path::new(v["path"].as_str().ok_or("identity path missing")?);
    if id(p)? != *v {
        Err("artifact identity changed".into())
    } else {
        Ok(())
    }
}
fn doc(p: &Path, cap: u64) -> Result<(Value, Value), String> {
    let before = id(p)?;
    let b = input::read_bytes_bounded(p, cap)?;
    if before["bytes"].as_u64() != Some(b.len() as u64) || before["sha256"] != sha(&b) {
        return Err("JSON read changed".into());
    }
    verify(&before)?;
    Ok((
        serde_json::from_slice(&b).map_err(|e| e.to_string())?,
        before,
    ))
}
fn encode(s: &str) -> String {
    s.bytes()
        .map(|c| {
            if c.is_ascii_alphanumeric() || b"-._~".contains(&c) {
                (c as char).to_string()
            } else {
                format!("%{c:02X}")
            }
        })
        .collect()
}
fn decode(s: &str) -> Result<String, String> {
    let mut b = vec![];
    let mut i = 0;
    while i < s.len() {
        match s.as_bytes()[i] {
            b'%' => {
                let h = s.get(i + 1..i + 3).ok_or("bad percent escape")?;
                b.push(u8::from_str_radix(h, 16).map_err(|e| e.to_string())?);
                i += 3
            }
            b'+' => {
                b.push(b' ');
                i += 1
            }
            c => {
                b.push(c);
                i += 1
            }
        }
    }
    String::from_utf8(b).map_err(|e| e.to_string())
}
fn query() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("norad_cat_id".into(), "68635".into()),
        ("start".into(), START.into()),
        ("start__lt".into(), END.into()),
        ("format".into(), "json".into()),
    ])
}
fn url(q: &BTreeMap<String, String>) -> String {
    format!(
        "{API}?{}",
        q.iter()
            .map(|(k, v)| format!("{}={}", encode(k), encode(v)))
            .collect::<Vec<_>>()
            .join("&")
    )
}
fn canonical(s: &str) -> Result<String, String> {
    if s.len() > 16384
        || !s.is_ascii()
        || s.bytes().any(|b| b <= 32 || b == 127)
        || s.contains(['#', '\\'])
    {
        return Err("invalid API URL".into());
    }
    let raw = s
        .strip_prefix(&format!("{API}?"))
        .ok_or("not exact HTTPS metadata endpoint")?;
    let mut got = BTreeMap::new();
    for part in raw.split('&') {
        let (k, v) = part.split_once('=').ok_or("query lacks value")?;
        if got.insert(decode(k)?, decode(v)?).is_some() {
            return Err("duplicate query key".into());
        }
    }
    let required = query();
    if required.iter().any(|(k, v)| got.get(k) != Some(v))
        || got
            .keys()
            .any(|k| !required.contains_key(k) && k != "cursor")
        || got.get("cursor").is_some_and(String::is_empty)
    {
        return Err("changed mission/window or unregistered filter".into());
    }
    Ok(url(&got))
}
fn next(headers: &str) -> Result<Option<String>, String> {
    let mut links = vec![];
    let mut status = None;
    for l in headers.lines() {
        if l.starts_with("HTTP/") {
            status = l.split_ascii_whitespace().nth(1);
            links.clear();
        } else if l.starts_with([' ', '\t']) {
            return Err("folded header".into());
        } else if let Some((k, v)) = l.split_once(':') {
            if k.eq_ignore_ascii_case("link") {
                links.push(v.trim());
            }
        }
    }
    if status != Some("200") {
        return Err("final HTTP header not200".into());
    }
    let mut target = None;
    for part in links.iter().flat_map(|l| l.split(',')) {
        let (u, params) = part
            .trim()
            .strip_prefix('<')
            .and_then(|s| s.split_once('>'))
            .ok_or("invalid Link header")?;
        let mut rel = None;
        for p in params.split(';').filter(|s| !s.trim().is_empty()) {
            let (k, v) = p.trim().split_once('=').ok_or("bad Link parameter")?;
            if k == "rel" {
                if rel.is_some() {
                    return Err("duplicate relation".into());
                }
                let v = v.trim();
                rel = Some(if let Some(v) = v.strip_prefix('"') {
                    v.strip_suffix('"').ok_or("unclosed relation")?
                } else {
                    v
                });
            }
        }
        if rel
            .ok_or("Link relation missing")?
            .split_ascii_whitespace()
            .any(|r| r == "next")
        {
            if target.is_some() {
                return Err("multiple next links".into());
            }
            target = Some(canonical(u)?);
        }
    }
    Ok(target)
}
fn check_http(http: &Value, request: &str) -> Result<(), String> {
    if http["success"] != true {
        return Err("HTTP fetch failed".into());
    }
    for s in [
        &http["requested_url"],
        &http["result"]["url"],
        &http["result"]["final_url"],
    ] {
        if canonical(s.as_str().ok_or("URL missing")?)? != request {
            return Err("HTTP URL changed".into());
        }
    }
    let hops = http["hops"]
        .as_array()
        .filter(|a| !a.is_empty())
        .ok_or("HTTP hops missing")?;
    for (index, h) in hops.iter().enumerate() {
        if canonical(h["url"].as_str().ok_or("hop URL absent")?)? != request
            || h["hop"] != 0
            || h["attempt"].as_u64() != Some(index as u64)
            || index >= 3
        {
            return Err("redirect or inconsistent attempt sequence".into());
        }
        let p = &h["process"];
        for k in ["stdout", "stderr"] {
            verify(&p[k])?;
        }
    }
    let last = hops.last().unwrap();
    if last["http_status"] != "200"
        || last["process"]["success"] != true
        || last["process"]["returncode"] != 0
        || last["process"]["timed_out"] != false
    {
        return Err("final process not complete HTTP200".into());
    }
    Ok(())
}
fn page(dir: &Path, request: &str) -> Result<(Vec<Value>, Option<String>, Value), String> {
    let (http, http_id) = doc(&dir.join("response.json.http.json"), PAGE_CAP)?;
    check_http(&http, request)?;
    let last = http["hops"].as_array().unwrap().last().unwrap();
    let attempt = last["attempt"].as_u64().ok_or("attempt absent")?;
    let hp = dir.join(format!("response.json.hop-0.try-{attempt}.headers"));
    let hb = input::read_bytes_bounded(&hp, 256 * 1024)?;
    let hi = id(&hp)?;
    if hi["sha256"] != sha(&hb) {
        return Err("headers changed during read".into());
    }
    let headers = std::str::from_utf8(&hb).map_err(|e| e.to_string())?;
    let (body, body_id) = doc(&dir.join("response.json"), PAGE_CAP)?;
    if http["result"]["identity"] != body_id {
        return Err("HTTP body identity mismatch".into());
    }
    let wire = id(&dir.join(format!("response.json.hop-0.try-{attempt}.body")))?;
    if wire["sha256"] != body_id["sha256"] || wire["bytes"] != body_id["bytes"] {
        return Err("body differs from final attempt".into());
    }
    let rows = body
        .as_array()
        .filter(|a| a.len() <= 1000)
        .ok_or("metadata body not bounded row array")?
        .clone();
    let next = next(headers)?;
    let charged = http["downloaded_bytes"]
        .as_u64()
        .ok_or("HTTP byte accounting absent")?;
    if charged > PAGE_CAP || charged < body_id["bytes"].as_u64().unwrap() {
        return Err("invalid page byte accounting".into());
    }
    Ok((
        rows,
        next.clone(),
        json!({"requested_url":request,"next":next,"body":body_id,"wire_body":wire,"headers":hi,"http_receipt":http_id,"charged_bytes":charged}),
    ))
}
fn select(rows: &[Value]) -> Result<(Vec<Value>, usize, Vec<Value>), String> {
    let mut groups = BTreeMap::<String, Vec<Value>>::new();
    let mut ids = BTreeSet::new();
    let mut excluded = vec![];
    for r in rows {
        let id = r["id"]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or("invalid metadata observation ID")?;
        if !ids.insert(id) {
            return Err("duplicate observation across metadata pages".into());
        }
        let t = DateTime::parse_from_rfc3339(r["start"].as_str().ok_or("start missing")?)
            .map_err(|e| e.to_string())?;
        let end = time(r["end"].as_str().ok_or("end missing")?)?;
        // Fail rather than silently selecting a subset of an ignored API window.
        if t.timestamp() < time(START)? || t.timestamp() >= time(END)? {
            return Err("API returned row outside fixed start bounds".into());
        }
        let station = r["ground_station"].as_u64().filter(|n| *n > 0);
        let eligible = end > t.timestamp()
            && end <= time(END)?
            && station.is_some()
            && r["norad_cat_id"] == 68635
            && r["transmitter"] == TX
            && r["transmitter_uuid"] == TX
            && r["transmitter_mode"] == "GMSK"
            && r["transmitter_baud"].as_f64() == Some(9600.0)
            && r["payload"]
                .as_str()
                .is_some_and(|s| s.starts_with("https://"));
        if !eligible {
            excluded.push(json!({"id":id,"reason":"fixed published metadata eligibility","no_decode_outcome_used":true}));
            continue;
        }
        let key = format!(
            "{}:{}",
            station.unwrap(),
            t.with_timezone(&Utc).format("%Y-%m-%d")
        );
        groups.entry(key).or_default().push(r.clone());
    }
    let eligible = groups.values().map(Vec::len).sum();
    for rs in groups.values_mut() {
        rs.sort_by_cached_key(|r| {
            (
                sha(format!("innovation-prospective-canvas-v1-row:{}", r["id"]).as_bytes()),
                r["id"].as_u64().unwrap(),
            )
        });
    }
    let mut gs = groups.into_iter().collect::<Vec<_>>();
    gs.sort_by_cached_key(|(key, _)| {
        (
            sha(format!("innovation-prospective-canvas-v1-group:{key}").as_bytes()),
            key.clone(),
        )
    });
    let mut selected = vec![];
    let mut rank = 0;
    loop {
        let before = selected.len();
        for (_, rs) in &gs {
            if let Some(r) = rs.get(rank) {
                selected.push(r.clone());
                if selected.len() == 500 {
                    break;
                }
            }
        }
        if selected.len() == 500 || selected.len() == before {
            break;
        }
        rank += 1;
    }
    Ok((selected, eligible, excluded))
}
fn run(a: Args) -> Result<(), String> {
    closed_window(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_secs() as i64,
    )?;
    let (protocol, protocol_id) = doc(&a.protocol, TOTAL_CAP)?;
    if protocol_id["sha256"] != PROTOCOL_SHA {
        return Err("protocol differs from fixed API-corrected registration".into());
    }
    let (amend, amend_id) = doc(&a.amendment, TOTAL_CAP)?;
    if amend["schema"] != "innovation-prospective-selection-amendment-v1"
        || amend["source_protocol"] != protocol_id
        || amend["status"] != "registered_before_observation_window"
        || time(
            amend["declared_utc"]
                .as_str()
                .ok_or("amendment time absent")?,
        )? >= time(START)? - 7200
        || amend["station_day_group_key_encoding"]
            != "decimal ground_station + ':' + UTC start date formatted YYYY-MM-DD"
    {
        return Err("unbound/late selection amendment".into());
    }
    fs::create_dir(&a.output).map_err(|e| e.to_string())?;
    let out = a.output.canonicalize().map_err(|e| e.to_string())?;
    let replay = a.replay_source.is_some();
    let source = if let Some(p) = a.replay_source {
        p.canonicalize().map_err(|e| e.to_string())?
    } else {
        out.clone()
    };
    let original_snapshot = if replay {
        let (completion, completion_id) = doc(&source.join("completion.json"), TOTAL_CAP)?;
        if completion["schema"] != "innovation-prospective-metadata-completion-v1"
            || completion["status"]
                != "metadata_complete_pending_independent_selection_and_exposure_review"
            || completion["metadata"]["path"] != json!(source.join("metadata-complete.json"))
        {
            return Err("replay requires exact complete source snapshot".into());
        }
        verify(&completion["metadata"])?;
        let (snapshot, snapshot_id) = doc(&source.join("metadata-complete.json"), TOTAL_CAP)?;
        if snapshot["schema"] != "innovation-prospective-metadata-snapshot-v1"
            || snapshot["true_eof"] != true
            || snapshot["selection_registration"] != protocol_id
            || snapshot["selection_amendment"] != amend_id
        {
            return Err("replay snapshot is incomplete or has a different registration".into());
        }
        Some((snapshot, snapshot_id, completion_id))
    } else {
        None
    };
    let mut all = vec![];
    let mut pages = vec![];
    let mut seen = BTreeSet::new();
    let mut current = Some(url(&query()));
    let mut total = 0u64;
    for index in 0..300 {
        let Some(request) = current.take() else {
            break;
        };
        if !seen.insert(request.clone()) {
            return Err("metadata cursor cycle".into());
        }
        let dir = source.join(format!("metadata/page-{index:03}"));
        if !replay {
            fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
            io::disk_guard(&out)?;
            io::fetch(
                &request,
                &dir.join("response.json"),
                PAGE_CAP.min(TOTAL_CAP.saturating_sub(total)),
            )?;
        }
        let (rows, nxt, mut receipt) = page(&dir, &request)?;
        total = total
            .checked_add(receipt["charged_bytes"].as_u64().unwrap())
            .ok_or("byte overflow")?;
        if total > TOTAL_CAP {
            return Err("metadata total safety bound reached; not EOF".into());
        }
        receipt["page"] = json!(index);
        receipt["rows"] = json!(rows.len());
        pages.push(receipt);
        all.extend(rows);
        current = nxt;
    }
    if current.is_some() || pages.is_empty() {
        return Err("metadata page bound reached before true EOF".into());
    }
    if let Some((snapshot, snapshot_id, completion_id)) = &original_snapshot {
        if snapshot["pages"] != json!(pages)
            || snapshot["observations"] != json!(all)
            || snapshot["page_count"] != json!(pages.len())
            || snapshot["charged_bytes"] != total
        {
            return Err("replayed pages or metadata differ from complete source snapshot".into());
        }
        verify(snapshot_id)?;
        verify(completion_id)?;
    }
    let (selected, eligible, excluded) = select(&all)?;
    let plan = json!({"schema":"innovation-prospective-acquisition-plan-v1","norad_cat_id":68635,"mode":"GMSK","baud":9600,"selection":protocol["selection"],"metadata_filters":protocol["metadata_query"]["filters"],"outcome_filters":[],"input_contract":protocol["input_contract"],"resource_limits":protocol["resource_limits"]});
    let plan_path = out.join("selection-plan.json");
    input::write_json_new(&plan_path, &plan)?;
    let metadata = out.join("metadata-complete.json");
    input::write_json_new(
        &metadata,
        &json!({"schema":"innovation-prospective-metadata-snapshot-v1","finished_utc":io::utc(),"true_eof":true,"page_count":pages.len(),"charged_bytes":total,"pages":pages,"observations":all,"selection_registration":protocol_id,"selection_amendment":amend_id,"metadata_only":true}),
    )?;
    input::write_json_new(&out.join("exclusions.json"), &json!(excluded))?;
    // A snapshot that cannot be read by the bounded replay is not committed.
    let _ = doc(&metadata, TOTAL_CAP)?;
    let cohort = json!({"schema":"innovation-benchmark-cohort-v2","frozen_utc":io::utc(),"plan":plan,"selection_plan":id(&plan_path)?,"selection_registration":protocol_id,"selection_amendment":amend_id,"metadata":id(&metadata)?,"full_metadata_eof":true,"eligible_count":eligible,"requested_count":500,"selected_count":selected.len(),"selection_ids_sha256":sha(selected.iter().map(|r|r["id"].to_string()).collect::<Vec<_>>().join("\n").as_bytes()),"observations":selected,"current_exposure_check_passed":false,"evaluation_release_permitted":false,"fresh_independent_holdout_qualified":false,"publication_ready":false,"waveform_downloads":0,"decoder_runs":0});
    verify(&protocol_id)?;
    verify(&amend_id)?;
    for p in &pages {
        for key in ["body", "wire_body", "headers", "http_receipt"] {
            verify(&p[key])?;
        }
    }
    input::write_json_new(&out.join("cohort.json"), &cohort)?;
    input::write_json_new(
        &out.join("completion.json"),
        &json!({"schema":"innovation-prospective-metadata-completion-v1","status":"metadata_complete_pending_independent_selection_and_exposure_review","program":id(&std::env::current_exe().map_err(|e|e.to_string())?)?,"cohort":id(&out.join("cohort.json"))?,"metadata":id(&metadata)?,"replay":replay,"network_fetches":if replay{0}else{pages.len()},"waveform_downloads":0,"decoder_runs":0,"evaluation_release_permitted":false}),
    )
}
fn main() {
    if let Err(e) = run(Args::parse()) {
        eprintln!("prospective metadata failed: {e}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn page_fixture() -> (tempfile::TempDir, String) {
        let t = tempfile::tempdir().unwrap();
        let dir = t.path();
        let request = url(&query());
        fs::write(
            dir.join("response.json"),
            serde_json::to_vec(&json!([row(1, 1)])).unwrap(),
        )
        .unwrap();
        fs::copy(
            dir.join("response.json"),
            dir.join("response.json.hop-0.try-0.body"),
        )
        .unwrap();
        fs::write(
            dir.join("response.json.hop-0.try-0.headers"),
            b"HTTP/1.1 200 OK\r\n\r\n",
        )
        .unwrap();
        fs::write(dir.join("stdout.log"), b"200").unwrap();
        fs::write(dir.join("stderr.log"), b"").unwrap();
        let body_id = id(&dir.join("response.json")).unwrap();
        let http = json!({"success":true,"requested_url":request,"result":{"url":request,"final_url":request,"identity":body_id},
            "downloaded_bytes":body_id["bytes"],"hops":[{"url":request,"hop":0,"attempt":0,"http_status":"200",
            "process":{"success":true,"returncode":0,"timed_out":false,"stdout":id(&dir.join("stdout.log")).unwrap(),"stderr":id(&dir.join("stderr.log")).unwrap()}}]});
        fs::write(
            dir.join("response.json.http.json"),
            serde_json::to_vec(&http).unwrap(),
        )
        .unwrap();
        (t, request)
    }
    #[test]
    fn complete_offline_metadata_page_is_accepted_not_a_live_http_attestation() {
        let (t, request) = page_fixture();
        let (rows, next, receipt) = page(t.path(), &request).unwrap();
        assert_eq!(rows.len(), 1);
        assert!(next.is_none());
        assert!(receipt["charged_bytes"].as_u64().unwrap() > 0);
    }
    #[test]
    fn changed_body_log_and_failed_http_do_not_prove_eof() {
        let (t, request) = page_fixture();
        fs::write(t.path().join("response.json"), b"[]").unwrap();
        assert!(page(t.path(), &request).is_err());
        let (t, request) = page_fixture();
        fs::write(t.path().join("stdout.log"), b"changed").unwrap();
        assert!(page(t.path(), &request).is_err());
        let (t, request) = page_fixture();
        let p = t.path().join("response.json.http.json");
        let (mut v, _) = doc(&p, PAGE_CAP).unwrap();
        v["hops"][0]["process"]["timed_out"] = json!(true);
        fs::write(&p, serde_json::to_vec(&v).unwrap()).unwrap();
        assert!(page(t.path(), &request).is_err());
    }
    #[test]
    fn forged_redirect_cursor_and_byte_accounting_reject() {
        for (field, bad) in [
            ("hop", json!(1)),
            ("attempt", json!(3)),
            ("url", json!(format!("{}&cursor=changed", url(&query())))),
        ] {
            let (t, request) = page_fixture();
            let p = t.path().join("response.json.http.json");
            let (mut v, _) = doc(&p, PAGE_CAP).unwrap();
            v["hops"][0][field] = bad;
            fs::write(&p, serde_json::to_vec(&v).unwrap()).unwrap();
            assert!(page(t.path(), &request).is_err());
        }
        let (t, request) = page_fixture();
        let p = t.path().join("response.json.http.json");
        let (mut v, _) = doc(&p, PAGE_CAP).unwrap();
        v["downloaded_bytes"] = json!(0);
        fs::write(&p, serde_json::to_vec(&v).unwrap()).unwrap();
        assert!(page(t.path(), &request).is_err());
    }
    fn row(id: u64, station: u64) -> Value {
        json!({"id":id,"ground_station":station,"start":"2026-09-12T01:00:00Z","end":"2026-09-12T01:10:00Z","norad_cat_id":68635,"transmitter":TX,"transmitter_uuid":TX,"transmitter_mode":"GMSK","transmitter_baud":9600,"payload":"https://example.invalid/a.ogg"})
    }
    #[test]
    fn window_guard_has_no_early_commit() {
        assert!(closed_window(time(END).unwrap() - 1).is_err());
        assert!(closed_window(time(END).unwrap()).is_ok());
    }
    #[test]
    fn exact_wire_filters_not_old_aliases_or_outcomes() {
        let s = url(&query());
        assert_eq!(canonical(&s).unwrap(), s);
        for bad in [
            s.replace("norad_cat_id=", "satellite__norad_cat_id="),
            s.replace("start=", "start__gte="),
            format!("{s}&status=good"),
            format!("{s}&cursor="),
            format!("{s}&start=x"),
            s.replace("network.satnogs.org", "example.invalid"),
        ] {
            assert!(canonical(&bad).is_err(), "{bad}");
        }
    }
    #[test]
    fn link_eof_and_cursor_are_strict() {
        assert!(next("HTTP/1.1 200 OK\r\n\r\n").unwrap().is_none());
        let s = format!("{}&cursor=a", url(&query()));
        assert_eq!(
            next(&format!("HTTP/1.1 200 OK\r\nLink: <{s}>; rel=\"next\"\r\n")).unwrap(),
            Some(canonical(&s).unwrap())
        );
        assert!(next("HTTP/1.1 503 Busy\r\n").is_err());
        assert!(next("HTTP/1.1 200 OK\r\nLink: <https://example.invalid/>; rel=next\r\n").is_err());
    }
    #[test]
    fn selection_outcome_blind_stable_and_underfull() {
        let rs = vec![row(1, 1), row(2, 1), row(3, 2)];
        let (a, n, _) = select(&rs).unwrap();
        assert_eq!(n, 3);
        let mut changed = rs.clone();
        changed.reverse();
        for r in &mut changed {
            r["status"] = json!("bad");
            r["demoddata"] = json!(["not used"]);
        }
        let (b, _, _) = select(&changed).unwrap();
        assert_eq!(
            a.iter().map(|r| r["id"].clone()).collect::<Vec<_>>(),
            b.iter().map(|r| r["id"].clone()).collect::<Vec<_>>()
        );
        assert_ne!(a[0]["ground_station"], a[1]["ground_station"]);
    }
    #[test]
    fn window_duplicate_and_profile_checks() {
        let a = row(1, 1);
        assert!(select(&[a.clone(), a.clone()]).is_err());
        let mut bad = a.clone();
        bad["start"] = json!("2026-09-11T00:00:00Z");
        assert!(select(&[bad]).is_err());
        let mut bad = a.clone();
        bad["transmitter_uuid"] = json!("wrong");
        assert_eq!(select(&[bad]).unwrap().1, 0);
        let mut bad = a;
        bad["end"] = json!("2026-09-20T00:00:00Z");
        assert_eq!(select(&[bad]).unwrap().1, 0);
    }
    #[test]
    fn utc_group_key_and_five_hundred_cap() {
        let mut rs = (1..=501).map(|i| row(i, 1)).collect::<Vec<_>>();
        let (a, n, _) = select(&rs).unwrap();
        assert_eq!((a.len(), n), (500, 501));
        rs[0]["start"] = json!("2026-09-12T03:00:00+02:00");
        let (b, _, _) = select(&rs).unwrap();
        assert_eq!(
            a.iter().map(|r| r["id"].clone()).collect::<Vec<_>>(),
            b.iter().map(|r| r["id"].clone()).collect::<Vec<_>>()
        );
    }
}
