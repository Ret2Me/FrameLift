//! Waveform-only admission wrapper for the audited historical CANVAS cohort.
//! No metadata refetch, outcome selection, DSP, or implicit release override.
#[allow(dead_code)]
#[path = "innovation_benchmark_acquire_v4.rs"]
mod acquisition;
use acquisition::io;
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
};
use telemetry_yield_rs::input;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    cohort: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long)]
    runner: PathBuf,
    #[arg(long)]
    runner_sha256: String,
    #[arg(long)]
    improved: PathBuf,
    #[arg(long)]
    release_receipt: PathBuf,
    #[arg(long)]
    current_exposure_check: Option<PathBuf>,
    #[arg(long)]
    resume: bool,
}
fn read(path: &Path) -> Result<Value, String> {
    checked_read(path).map(|(value, _)| value)
}
fn checked_read(path: &Path) -> Result<(Value, Value), String> {
    use std::io::Read;
    let before = checked_identity(path)?;
    let mut file = fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(64 * 1024 * 1024 + 1);
    let mut bytes = vec![];
    file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    if bytes.len() > 64 * 1024 * 1024 {
        return Err("oversized JSON input".into());
    }
    if before["bytes"].as_u64() != Some(bytes.len() as u64)
        || before["sha256"] != hex::encode(Sha256::digest(&bytes))
    {
        return Err("JSON bytes changed during checked read".into());
    }
    verify(&before)?;
    Ok((
        serde_json::from_slice(&bytes).map_err(|e| e.to_string())?,
        before,
    ))
}
fn checked_identity(path: &Path) -> Result<Value, String> {
    Ok(json!(input::identity(path)?))
}
fn verify(identity: &Value) -> Result<(), String> {
    let path = Path::new(identity["path"].as_str().ok_or("identity path missing")?);
    if checked_identity(path)? != *identity {
        return Err(format!("artifact changed: {}", path.display()));
    }
    Ok(())
}
fn digest(text: &str) -> bool {
    text.len() == 64
        && text
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn resolve_selection(cohort: &Value) -> Result<Value, String> {
    // The exposure audit retains the exact original rows but stores the plan
    // identity in its hash-bound parent. Permit that one documented level only.
    let owner = if cohort.get("source_cohort").is_some() {
        if cohort["schema"] != "innovation-benchmark-cohort-v2"
            || cohort["subset_amendment_schema"] != "innovation-benchmark-exposure-subset-v1"
        {
            return Err("unsupported derived cohort contract".into());
        }
        let parent_id = &cohort["source_cohort"];
        verify(parent_id)?;
        let (parent, read_id) = checked_read(Path::new(parent_id["path"].as_str().unwrap()))?;
        if read_id != *parent_id || parent.get("source_cohort").is_some() {
            return Err("changed or recursively derived selection parent".into());
        }
        if parent["schema"] != cohort["schema"] || parent["plan"] != cohort["plan"] {
            return Err("derived selection plan differs from parent".into());
        }
        let original = parent["observations"]
            .as_array()
            .ok_or("parent rows absent")?;
        let subset = cohort["observations"]
            .as_array()
            .ok_or("subset rows absent")?;
        if parent["selected_count"].as_u64() != Some(original.len() as u64)
            || cohort["selected_count"].as_u64() != Some(subset.len() as u64)
        {
            return Err("selection row count mismatch".into());
        }
        let mut cursor = 0;
        let mut seen = std::collections::BTreeSet::new();
        for row in subset {
            let id = row["id"].as_u64().ok_or("subset id absent")?;
            if !seen.insert(id) {
                return Err("duplicate subset id".into());
            }
            let offset = original[cursor..]
                .iter()
                .position(|r| r == row)
                .ok_or("subset is not an exact original-order subsequence")?;
            cursor += offset + 1;
        }
        if let Some(own) = cohort.get("selection_plan") {
            if *own != parent["selection_plan"] {
                return Err("conflicting inherited selection identity".into());
            }
        }
        parent
    } else {
        cohort.clone()
    };
    let selection = owner
        .get("selection_plan")
        .ok_or("selection identity missing")?
        .clone();
    verify(&selection)?;
    Ok(selection)
}
fn require_preflight(value: &Value, cohort: &Value, invocation: &Value) -> Result<(), String> {
    let release = &invocation["release_receipt"];
    if value["schema"] != "innovation-benchmark-preflight-v1"
        || value["status"] != "preflight_complete"
        || value["waveform_reads"] != 0
        || value["decoder_runs"] != 0
        || value["benchmark_complete"] != false
        || value["cohort"] != *cohort
        || value["release_receipt"] != *release
    {
        return Err("missing/mismatched no-DSP preflight receipt".into());
    }
    verify(value.get("manifest").ok_or("preflight manifest absent")?)?;
    let m = read(Path::new(value["manifest"]["path"].as_str().unwrap()))?;
    if m["schema"] != "innovation-benchmark-manifest-v3"
        || m["cohort"] != *cohort
        || m["network_submission"] != false
        || m["source_archive_used_for_search"] != false
    {
        return Err("preflight manifest contract mismatch".into());
    }
    for key in [
        "runner",
        "improved",
        "release_receipt",
        "workers",
        "threads",
        "decoder_seconds",
    ] {
        if m[key] != invocation[key] {
            return Err(format!("preflight manifest {key} differs from invocation"));
        }
    }
    let runtime = m["runtime"]
        .as_array()
        .filter(|x| !x.is_empty())
        .ok_or("runtime absent")?;
    for item in runtime {
        verify(item)?;
    }
    let admission_id = value
        .get("admission")
        .ok_or("preflight admission receipt absent")?;
    verify(admission_id)?;
    let admission = read(Path::new(
        admission_id["path"]
            .as_str()
            .ok_or("admission path absent")?,
    ))?;
    if admission["schema"] != "innovation-benchmark-admission-v1"
        || admission["status"] != "admitted"
        || admission["cohort"] != *cohort
        || admission["registered_cohort"] != invocation["source_cohort"]
        || admission["release"] != *release
        || admission["freshness_checked"] != true
        || admission["waveform_reads"] != 0
        || admission["decoder_runs"] != 0
    {
        return Err("preflight admission not bound to acquisition".into());
    }
    verify(&admission["receiver_freeze"])?;
    Ok(())
}
fn transport_view(cohort: &Value, selection: &Value) -> Result<Value, String> {
    if resolve_selection(cohort)? != *selection {
        return Err("transport selection differs from admitted invocation".into());
    }
    // Transport needs the inherited plan identity, but the registered/staged
    // cohort bytes must remain unchanged. Only this ephemeral view adds it.
    let mut view = cohort.clone();
    view["selection_plan"] = selection.clone();
    Ok(view)
}
fn copy_exact_new(source: &Path, destination: &Path, resume: bool) -> Result<(), String> {
    let before = input::identity(source)?;
    if destination.exists() {
        if !resume {
            return Err("existing output requires --resume".into());
        }
    } else {
        let mut src = fs::File::open(source).map_err(|e| e.to_string())?;
        let mut dst = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(destination)
            .map_err(|e| e.to_string())?;
        std::io::copy(&mut src, &mut dst).map_err(|e| e.to_string())?;
        dst.sync_all().map_err(|e| e.to_string())?;
    }
    let after = input::identity(source)?;
    let copied = input::identity(destination)?;
    if before.sha256 != after.sha256
        || before.bytes != after.bytes
        || before.sha256 != copied.sha256
        || before.bytes != copied.bytes
    {
        return Err("copied artifact identity mismatch".into());
    }
    Ok(())
}
fn admission_then<F, G>(admit: F, fetch: G) -> Result<(), String>
where
    F: FnOnce() -> Result<(), String>,
    G: FnOnce() -> Result<(), String>,
{
    admit()?;
    fetch()
}
fn run(mut a: Args) -> Result<(), String> {
    if !digest(&a.runner_sha256) {
        return Err("explicit lowercase runner SHA256 required".into());
    }
    a.cohort = a.cohort.canonicalize().map_err(|e| e.to_string())?;
    a.runner = a.runner.canonicalize().map_err(|e| e.to_string())?;
    a.improved = a.improved.canonicalize().map_err(|e| e.to_string())?;
    a.release_receipt = a
        .release_receipt
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if let Some(p) = a.current_exposure_check.as_mut() {
        *p = p.canonicalize().map_err(|e| e.to_string())?;
    }
    let runner = checked_identity(&a.runner)?;
    if runner["sha256"] != a.runner_sha256 {
        return Err("runner executable differs from pin".into());
    }
    let (cohort, cohort_id) = checked_read(&a.cohort)?;
    let selection = resolve_selection(&cohort)?;
    let (release, release_id) = checked_read(&a.release_receipt)?;
    if release["schema"] != "innovation-waveform-release-v1"
        || release["evaluation_release_permitted"] != true
        || release["current_exposure_check_passed"] != true
    {
        return Err("no completed explicit waveform release; no audio fetched".into());
    }
    let manifest = json!({"schema":"innovation-waveform-acquisition-invocation-v1",
        "launcher":checked_identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,
        "source_cohort":cohort_id,"runner":runner,
        "improved":checked_identity(&a.improved)?,"release_receipt":release_id,
        "selection_plan":selection,"workers":4,"threads":2,"decoder_seconds":900,
        "transport":"bounded versioned v4; exact original OGG; metadata-only selection never rerun",
        "cap_bytes":12884901888u64,"minimum_disk_reserve_gib":25,
        "publication_ready":false});
    if a.output.exists() {
        if !a.resume {
            return Err("output exists; use explicit --resume".into());
        }
    } else {
        if a.resume {
            return Err("resume output missing".into());
        }
        fs::create_dir_all(&a.output).map_err(|e| e.to_string())?;
    }
    a.output = a.output.canonicalize().map_err(|e| e.to_string())?;
    let manifest_path = a.output.join("waveform-invocation.json");
    if a.resume {
        if read(&manifest_path)? != manifest {
            return Err("frozen acquisition invocation changed".into());
        }
    } else {
        input::write_json_new(&manifest_path, &manifest)?;
    }
    copy_exact_new(&a.cohort, &a.output.join("cohort.json"), a.resume)?;
    verify(&manifest["source_cohort"])?;
    let (admitted_cohort, admitted_cohort_id) = checked_read(&a.output.join("cohort.json"))?;
    if admitted_cohort != cohort
        || admitted_cohort_id["sha256"] != cohort_id["sha256"]
        || admitted_cohort_id["bytes"] != cohort_id["bytes"]
    {
        return Err("staged cohort differs from originally parsed admitted bytes".into());
    }
    copy_exact_new(
        Path::new(selection["path"].as_str().unwrap()),
        &a.output.join("selection-plan.json"),
        a.resume,
    )?;
    let preflight = tempfile::Builder::new()
        .prefix("admission-")
        .tempdir_in(&a.output)
        .map_err(|e| e.to_string())?
        .keep();
    let mut args = vec![
        "--acquisition".into(),
        a.output.display().to_string(),
        "--output".into(),
        preflight.join("gate-output").display().to_string(),
        "--improved".into(),
        a.improved.display().to_string(),
        "--release-receipt".into(),
        a.release_receipt.display().to_string(),
        "--workers".into(),
        "4".into(),
        "--threads".into(),
        "2".into(),
        "--decoder-seconds".into(),
        "900".into(),
        "--preflight-only".into(),
    ];
    if let Some(p) = &a.current_exposure_check {
        args.extend(["--current-exposure-check".into(), p.display().to_string()]);
    }
    admission_then(
        || {
            for key in [
                "launcher",
                "source_cohort",
                "runner",
                "improved",
                "release_receipt",
                "selection_plan",
            ] {
                verify(&manifest[key])?;
            }
            let outcome = io::execute(
                &a.runner,
                &args,
                &preflight,
                "preflight",
                180,
                128 * 1024 * 1024,
            )?;
            input::write_json_new(&preflight.join("process.json"), &outcome)?;
            if outcome["success"] != true
                || outcome["returncode"] != 0
                || outcome["timed_out"] != false
            {
                return Err(
                    "receiver admission failed; no audio fetched (see retained process/logs)"
                        .into(),
                );
            }
            for key in [
                "launcher",
                "source_cohort",
                "runner",
                "improved",
                "release_receipt",
                "selection_plan",
            ] {
                verify(&manifest[key])?;
            }
            require_preflight(
                &read(&preflight.join("gate-output/preflight.json"))?,
                &admitted_cohort_id,
                &manifest,
            )?;
            verify(&admitted_cohort_id)?;
            Ok(())
        },
        || {
            let view = transport_view(&admitted_cohort, &manifest["selection_plan"])?;
            acquisition::download(&a.output, &view)
        },
    )
}
fn main() {
    if let Err(e) = run(Args::parse()) {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn inherited_selection_requires_exact_hash_bound_ordered_subset() {
        let t = tempfile::tempdir().unwrap();
        let plan_path = t.path().join("plan.json");
        fs::write(&plan_path, b"{}").unwrap();
        let selection = checked_identity(&plan_path).unwrap();
        let parent_path = t.path().join("parent.json");
        let parent = json!({"schema":"innovation-benchmark-cohort-v2","selection_plan":selection,
            "plan":{"fixed":true},"selected_count":3,"observations":[{"id":1},{"id":2},{"id":3}]});
        fs::write(&parent_path, serde_json::to_vec(&parent).unwrap()).unwrap();
        let child = json!({"schema":"innovation-benchmark-cohort-v2",
            "subset_amendment_schema":"innovation-benchmark-exposure-subset-v1",
            "source_cohort":checked_identity(&parent_path).unwrap(),"plan":{"fixed":true},
            "selected_count":2,"observations":[{"id":1},{"id":3}]});
        assert_eq!(resolve_selection(&parent).unwrap(), selection);
        assert_eq!(resolve_selection(&child).unwrap(), selection);
        let view = transport_view(&child, &selection).unwrap();
        assert_eq!(view["selection_plan"], selection);
        assert_eq!(view["observations"], child["observations"]);
        assert_eq!(view["plan"], child["plan"]);
        assert!(child.get("selection_plan").is_none());
        assert!(transport_view(&child, &json!({})).is_err());
        for (key, bad) in [
            ("plan", json!({})),
            ("selected_count", json!(3)),
            ("observations", json!([{"id":3},{"id":1}])),
            ("observations", json!([{"id":1},{"id":1}])),
            (
                "observations",
                json!([{"id":1},{"id":3,"payload":"changed"}]),
            ),
            ("selection_plan", json!({})),
            ("subset_amendment_schema", json!("unknown")),
        ] {
            let mut changed = child.clone();
            changed[key] = bad;
            assert!(resolve_selection(&changed).is_err(), "{key}");
        }
        let mut recursive = parent.clone();
        recursive["source_cohort"] = json!({});
        fs::write(&parent_path, serde_json::to_vec(&recursive).unwrap()).unwrap();
        assert!(resolve_selection(&child).is_err());
        let mut relinked = child.clone();
        relinked["source_cohort"] = checked_identity(&parent_path).unwrap();
        assert!(resolve_selection(&relinked).is_err());
        fs::write(&parent_path, serde_json::to_vec(&parent).unwrap()).unwrap();
        fs::write(&plan_path, b"changed").unwrap();
        assert!(resolve_selection(&child).is_err());
    }
    #[test]
    fn preflight_proof_is_bound_and_cannot_be_a_fake_complete_benchmark() {
        let t = tempfile::tempdir().unwrap();
        let path = t.path().join("manifest.json");
        let cohort = json!({"sha256":"cohort"});
        let release = json!({"sha256":"release"});
        let runtime = t.path().join("runtime");
        fs::write(&runtime, b"fixture").unwrap();
        let runtime_id = checked_identity(&runtime).unwrap();
        let invocation = json!({"runner":runtime_id,"improved":runtime_id,"release_receipt":release,
            "source_cohort":cohort,"workers":4,"threads":2,"decoder_seconds":900});
        let m = json!({"schema":"innovation-benchmark-manifest-v3","cohort":cohort,"runner":runtime_id,
            "improved":runtime_id,"release_receipt":release,"workers":4,"threads":2,"decoder_seconds":900,
            "network_submission":false,"source_archive_used_for_search":false,"runtime":[runtime_id]});
        fs::write(&path, serde_json::to_vec(&m).unwrap()).unwrap();
        let admission = t.path().join("admission.json");
        fs::write(&admission,serde_json::to_vec(&json!({"schema":"innovation-benchmark-admission-v1","status":"admitted",
            "release":release,"cohort":cohort,"registered_cohort":cohort,"receiver_freeze":runtime_id,
            "freshness_checked":true,"waveform_reads":0,"decoder_runs":0})).unwrap()).unwrap();
        let proof = json!({"schema":"innovation-benchmark-preflight-v1","status":"preflight_complete",
            "waveform_reads":0,"decoder_runs":0,"benchmark_complete":false,
            "cohort":cohort,"release_receipt":release,"manifest":checked_identity(&path).unwrap(),"admission":checked_identity(&admission).unwrap()});
        require_preflight(&proof, &cohort, &invocation).unwrap();
        for (key, bad) in [
            ("status", json!("complete")),
            ("waveform_reads", json!(1)),
            ("decoder_runs", json!(1)),
            ("benchmark_complete", json!(true)),
            ("cohort", json!({})),
            ("release_receipt", json!({})),
        ] {
            let mut v = proof.clone();
            v[key] = bad;
            assert!(
                require_preflight(&v, &cohort, &invocation).is_err(),
                "{key}"
            );
        }
        fs::write(&path, b"{}").unwrap();
        let mut empty = proof.clone();
        empty["manifest"] = checked_identity(&path).unwrap();
        assert!(require_preflight(&empty, &cohort, &invocation).is_err());
        let mut wrong = m.clone();
        wrong["workers"] = json!(1);
        fs::write(&path, serde_json::to_vec(&wrong).unwrap()).unwrap();
        empty["manifest"] = checked_identity(&path).unwrap();
        assert!(require_preflight(&empty, &cohort, &invocation).is_err());
        fs::write(&path, b"changed").unwrap();
        assert!(require_preflight(&proof, &cohort, &invocation).is_err());
    }
    #[test]
    fn parsed_bytes_and_identity_are_the_same_snapshot() {
        let t = tempfile::tempdir().unwrap();
        let p = t.path().join("cohort.json");
        fs::write(&p, b"{\"rows\":[1]}").unwrap();
        let (v, id) = checked_read(&p).unwrap();
        assert_eq!(v["rows"], json!([1]));
        fs::write(&p, b"{\"rows\":[2]}").unwrap();
        assert!(verify(&id).is_err());
        let (new, new_id) = checked_read(&p).unwrap();
        assert_eq!(new["rows"], json!([2]));
        assert_ne!(id, new_id);
    }
    #[test]
    fn failed_gate_never_fetches() {
        let seen = std::cell::Cell::new(false);
        assert!(
            admission_then(
                || Err("blocked".into()),
                || {
                    seen.set(true);
                    Ok(())
                }
            )
            .is_err()
        );
        assert!(!seen.get());
    }
    #[test]
    fn passed_gate_fetches_once() {
        let seen = std::cell::Cell::new(0);
        admission_then(
            || Ok(()),
            || {
                seen.set(seen.get() + 1);
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(seen.get(), 1);
    }
    #[test]
    fn pin_is_strict() {
        assert!(digest(&"a".repeat(64)));
        assert!(!digest(&"A".repeat(64)));
        assert!(!digest(&"a".repeat(63)));
    }
    #[test]
    fn copies_recheck_and_never_overwrite() {
        let t = tempfile::tempdir().unwrap();
        let src = t.path().join("s");
        let dst = t.path().join("d");
        fs::write(&src, b"source").unwrap();
        copy_exact_new(&src, &dst, false).unwrap();
        assert!(copy_exact_new(&src, &dst, false).is_err());
        copy_exact_new(&src, &dst, true).unwrap();
        fs::write(&dst, b"changed").unwrap();
        assert!(copy_exact_new(&src, &dst, true).is_err());
        assert_eq!(fs::read(&dst).unwrap(), b"changed");
    }
}
