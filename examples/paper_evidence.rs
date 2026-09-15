//! Freeze a portable, count-level manuscript bundle from the completed CANVAS study.
//! This is a reporting tool, not a replacement for raw-frame or runtime admission audits.

#[path = "support/readme_figures.rs"]
mod readme_figures;

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fs;
use std::path::Path;

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const ARMS: [&str; 5] = [
    "direwolf",
    "gr_satellites",
    "progressive_v3",
    "innovation_v1_no_codec",
    "innovation_v2",
];
const CAMPAIGN: &str = "work/publication-speed-restart-20260912-v1";

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn read(path: &Path) -> Result<Value> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}
fn count(v: &Value) -> Result<u64> {
    v.as_u64().ok_or_else(|| "missing unsigned count".into())
}
fn number(v: &Value) -> Result<f64> {
    v.as_f64()
        .filter(|n| n.is_finite() && *n >= 0.0)
        .ok_or_else(|| "missing finite nonnegative measurement".into())
}
fn set(v: &Value) -> Result<BTreeSet<String>> {
    v.as_array()
        .ok_or("missing packet set")?
        .iter()
        .map(|p| {
            let s = p.as_str().ok_or("packet is not hex text")?;
            let bytes = hex::decode(s)?;
            if bytes.len() < 16 {
                return Err("packet is shorter than an AX.25 UI header".into());
            }
            Ok(hex::encode(bytes))
        })
        .collect()
}
fn comparison(a: &BTreeSet<String>, b: &BTreeSet<String>) -> Value {
    let added: Vec<_> = a.difference(b).collect();
    let lost: Vec<_> = b.difference(a).collect();
    json!({"ours":a.len(),"baseline":b.len(),"added":added.len(),"lost":lost.len(),
        "added_pdu_bytes":added.iter().map(|p|p.len()/2).sum::<usize>(),
        "lost_pdu_bytes":lost.iter().map(|p|p.len()/2).sum::<usize>()})
}
fn summarize(rows: &[Value]) -> Result<Value> {
    let mut out = json!({"observations":rows.len()});
    for field in [
        "ours",
        "baseline",
        "added",
        "lost",
        "added_pdu_bytes",
        "lost_pdu_bytes",
    ] {
        let total = rows
            .iter()
            .map(|r| count(&r["primary"][field]))
            .collect::<Result<Vec<_>>>()?
            .iter()
            .sum::<u64>();
        out[field] = json!(total);
    }
    let net = count(&out["ours"])? as i64 - count(&out["baseline"])? as i64;
    out["net"] = json!(net);
    out["net_percent"] = if count(&out["baseline"])? == 0 {
        Value::Null
    } else {
        json!(100.0 * net as f64 / count(&out["baseline"])? as f64)
    };
    out["observations_with_gain"] = json!(
        rows.iter()
            .filter(|r| r["primary"]["added"].as_u64().unwrap_or(0) > 0)
            .count()
    );
    out["observations_with_loss"] = json!(
        rows.iter()
            .filter(|r| r["primary"]["lost"].as_u64().unwrap_or(0) > 0)
            .count()
    );
    out["audio_seconds"] = json!(
        rows.iter()
            .map(|r| number(&r["audio_seconds"]))
            .collect::<Result<Vec<_>>>()?
            .iter()
            .sum::<f64>()
    );
    for arm in ARMS {
        out["arms"][arm] = json!({
            "frames":rows.iter().map(|r|count(&r["counts"][arm])).collect::<Result<Vec<_>>>()?.iter().sum::<u64>(),
            "wall_seconds":rows.iter().map(|r|number(&r["wall_seconds"][arm])).collect::<Result<Vec<_>>>()?.iter().sum::<f64>()
        });
    }
    Ok(out)
}

fn freeze(root: &Path, out: &Path) -> Result<()> {
    if out.exists() {
        return Err("bundle already exists; choose a new version".into());
    }
    let campaign = root.join(CAMPAIGN);
    let analysis_path = campaign.join("analysis-v2/yield.json");
    let analysis = read(&analysis_path)?;
    let costs = read(&campaign.join("analysis-v2/costs.json"))?;
    if count(&analysis["selected"])? != 266 || count(&analysis["all_five_complete"])? != 266 {
        return Err("this exporter requires the completed 266-observation study".into());
    }
    let mut rows = Vec::new();
    let mut seen = BTreeSet::new();
    for audited in analysis["rows"].as_array().ok_or("missing analyzed rows")? {
        let id = count(&audited["id"])?;
        if !seen.insert(id) {
            return Err("duplicate observation".into());
        }
        let result_path = campaign.join(format!("comparison/obs-{id}/result.json"));
        let bytes = fs::read(&result_path)?;
        let result: Value = serde_json::from_slice(&bytes)?;
        if result["status"] != "complete" || count(&result["observation_id"])? != id {
            return Err("incomplete or mismatched result".into());
        }
        let mut sets = BTreeMap::new();
        let mut walls = BTreeMap::new();
        for arm in ARMS {
            let d = &result["decoders"][arm];
            if d["status"] != "complete"
                || d["process"]["success"] != true
                || d["process"]["timed_out"] != false
            {
                return Err(format!("incomplete arm {id}/{arm}").into());
            }
            let packets = set(&d["strict_ui_payloads"])?;
            if packets.len() as u64 != count(&d["strict_ui_unique_count"])? {
                return Err("packet count mismatch".into());
            }
            sets.insert(arm, packets);
            walls.insert(arm, number(&d["process"]["wall_seconds"])?);
        }
        let external = sets["direwolf"]
            .union(&sets["gr_satellites"])
            .cloned()
            .collect();
        let primary = comparison(&sets["progressive_v3"], &external);
        for (ours, theirs) in [
            ("baseline", "baseline"),
            ("added", "gain_count"),
            ("lost", "loss_count"),
        ] {
            if primary[ours] != audited["primary"][theirs] {
                return Err("final analyzer disagrees with per-observation sets".into());
            }
        }
        for (ours, theirs) in [("added", "gained"), ("lost", "lost")] {
            let expected: BTreeSet<_> = if ours == "added" {
                sets["progressive_v3"]
                    .difference(&external)
                    .cloned()
                    .collect()
            } else {
                external
                    .difference(&sets["progressive_v3"])
                    .cloned()
                    .collect()
            };
            if expected != set(&audited["primary"][theirs])? {
                return Err("final analyzer packet identity mismatch".into());
            }
        }
        let codec_delta = comparison(&sets["innovation_v2"], &sets["innovation_v1_no_codec"]);
        if codec_delta["added"] != audited["innovation_vs_v1"]["gain_count"]
            || codec_delta["lost"] != audited["innovation_vs_v1"]["loss_count"]
        {
            return Err("codec comparison mismatch".into());
        }
        rows.push(json!({"id":id,"result_sha256":sha(&bytes),"source_sha256":result["source"]["sha256"],
            "input_sha256":result["input"]["sha256"],"source_url":result["row"]["payload"],
            "start":result["row"]["start"],"station":result["row"]["ground_station"],
            "waterfall_status":result["row"]["waterfall_status"],"audio_seconds":result["audio_seconds"],
            "counts":sets.iter().map(|(a,s)|(*a,s.len())).collect::<BTreeMap<_,_>>(),
            "wall_seconds":walls,"primary":primary,"codec_delta":codec_delta}));
    }
    rows.sort_by_key(|r| r["id"].as_u64());
    if rows.len() != 266 {
        return Err("incomplete exported cohort".into());
    }
    let all = summarize(&rows)?;
    for arm in ARMS {
        if all["arms"][arm]["frames"] != analysis["pair_counts"][arm] {
            return Err("aggregate frame mismatch".into());
        }
        let expected = number(
            &costs["per_arm"][arm]["final_arm_costs"]["groups"]["all"]["resources"]["wall_seconds"]
                ["observed_value"],
        )?;
        if (number(&all["arms"][arm]["wall_seconds"])? - expected).abs() > 0.001 {
            return Err("aggregate wall-time mismatch".into());
        }
    }
    let signal: Vec<_> = rows
        .iter()
        .filter(|r| r["waterfall_status"] == "with-signal")
        .cloned()
        .collect();
    let summary = json!({"schema":"paper-evidence-v2","study":"CANVAS historical disclosed repeat",
        "publication_ready":false,"independent_holdout":false,"raw_artifact_audit_reperformed_by_exporter":false,
        "all":all,"with_signal":summarize(&signal)?,
        "station_count":rows.iter().map(|r|r["station"].to_string()).collect::<BTreeSet<_>>().len(),
        "day_count":rows.iter().map(|r|r["start"].as_str().unwrap_or("").chars().take(10).collect::<String>()).collect::<BTreeSet<_>>().len(),
        "analysis": {"verification_scope":analysis["verification_scope"],"primary":analysis["analyses"]["primary"],
            "codec":analysis["analyses"]["innovation_vs_v1"],"innovation":analysis["analyses"]["innovation_vs_external"]},
        "source_reports": {"yield_sha256":sha(&fs::read(&analysis_path)?),"costs_sha256":sha(&fs::read(campaign.join("analysis-v2/costs.json"))?)}});
    fs::create_dir_all(out)?;
    fs::write(
        out.join("summary.json"),
        serde_json::to_vec_pretty(&summary)?,
    )?;
    fs::write(
        out.join("observations.json"),
        serde_json::to_vec_pretty(&rows)?,
    )?;
    for (source, dest) in [
        ("receiver-freeze.json", "receiver-freeze.json"),
        ("protocol.md", "protocol.md"),
        (
            "prior-evaluation-exposure.json",
            "prior-evaluation-exposure.json",
        ),
        ("comparison/grsat-profile.yml", "grsat-profile.yml"),
        (
            "analysis-v2/registration.json",
            "analysis-registration.json",
        ),
    ] {
        fs::copy(campaign.join(source), out.join(dest))?;
    }
    let mut names = fs::read_dir(out)?
        .map(|e| e.map(|e| e.file_name()))
        .collect::<std::io::Result<Vec<_>>>()?;
    names.sort();
    let checksums = names
        .iter()
        .map(|n| {
            Ok(format!(
                "{}  {}\n",
                sha(&fs::read(out.join(n))?),
                n.to_string_lossy()
            ))
        })
        .collect::<Result<String>>()?;
    fs::write(out.join("checksums.sha256"), checksums)?;
    println!(
        "Frozen {} observations; {} signal-labelled. No decoder reruns.",
        rows.len(),
        signal.len()
    );
    Ok(())
}

fn verify(out: &Path) -> Result<()> {
    let manifest = fs::read_to_string(out.join("checksums.sha256"))?;
    let mut files = BTreeSet::new();
    for line in manifest.lines() {
        let (expected, name) = line.split_once("  ").ok_or("invalid checksum row")?;
        if name.contains(['/', '\\']) || name == "." || name == ".." || !files.insert(name) {
            return Err("invalid or duplicate bundle filename".into());
        }
        if sha(&fs::read(out.join(name))?) != expected {
            return Err(format!("checksum mismatch: {name}").into());
        }
    }
    for required in [
        "summary.json",
        "observations.json",
        "receiver-freeze.json",
        "protocol.md",
        "prior-evaluation-exposure.json",
        "grsat-profile.yml",
        "analysis-registration.json",
    ] {
        if !files.contains(required) {
            return Err(format!("missing manifest entry: {required}").into());
        }
    }
    let summary = read(&out.join("summary.json"))?;
    let value = read(&out.join("observations.json"))?;
    let rows = value.as_array().ok_or("missing observations")?;
    if summary["schema"] != "paper-evidence-v2" || rows.len() != 266 {
        return Err("unexpected schema or incomplete historical cohort".into());
    }
    let mut seen = BTreeSet::new();
    for row in rows {
        if !seen.insert(count(&row["id"])?) {
            return Err("duplicate observation".into());
        }
        let p = &row["primary"];
        if count(&p["ours"])? + count(&p["lost"])? != count(&p["baseline"])? + count(&p["added"])? {
            return Err("set arithmetic mismatch".into());
        }
        if p["ours"] != row["counts"]["progressive_v3"] {
            return Err("progressive count mismatch".into());
        }
    }
    let signal: Vec<_> = rows
        .iter()
        .filter(|r| r["waterfall_status"] == "with-signal")
        .cloned()
        .collect();
    if summarize(rows)? != summary["all"] || summarize(&signal)? != summary["with_signal"] {
        return Err("summary mismatch".into());
    }
    if summary["publication_ready"] != false || summary["independent_holdout"] != false {
        return Err("historical evidence cannot certify publication or independence".into());
    }
    println!(
        "PASS: bundle checksums and count arithmetic for {} observations. Not a raw-waveform re-audit.",
        rows.len()
    );
    Ok(())
}

fn render(bundle: &Path, output: &Path) -> Result<()> {
    verify(bundle)?;
    let s = read(&bundle.join("summary.json"))?;
    let mut metrics =
        String::from("% Generated by paper_evidence; source: ../evidence/summary.json\n");
    for (name, pointer) in [
        ("StudyN", "/all/observations"),
        ("Ours", "/all/ours"),
        ("Reference", "/all/baseline"),
        ("Added", "/all/added"),
        ("Lost", "/all/lost"),
        ("Net", "/all/net"),
        ("GainObservations", "/all/observations_with_gain"),
        ("AddedBytes", "/all/added_pdu_bytes"),
        ("LostBytes", "/all/lost_pdu_bytes"),
        ("SignalN", "/with_signal/observations"),
        ("SignalOurs", "/with_signal/ours"),
        ("SignalReference", "/with_signal/baseline"),
        ("SignalAdded", "/with_signal/added"),
        (
            "SignalGainObservations",
            "/with_signal/observations_with_gain",
        ),
    ] {
        metrics.push_str(&format!(
            "\\newcommand{{\\{name}}}{{{}}}\n",
            count(s.pointer(pointer).ok_or("missing metric")?)?
        ));
    }
    for (name, value) in [
        ("NetPercent", number(&s["all"]["net_percent"])?),
        ("SignalPercent", number(&s["with_signal"]["net_percent"])?),
        ("AudioHours", number(&s["all"]["audio_seconds"])? / 3600.0),
    ] {
        metrics.push_str(&format!("\\newcommand{{\\{name}}}{{{value:.2}}}\n"));
    }
    let mut table = String::from(
        "% Generated from the frozen evidence; final process wall sums, not elapsed campaign time.\n\\begin{tabular}{lrrr}\n\\toprule Arm & PDUs & Wall sum (s) & Mean (s)\\\\\n\\midrule\n",
    );
    for (arm, label) in [
        ("direwolf", "Dire Wolf"),
        ("gr_satellites", "gr-satellites"),
        ("progressive_v3", "Progressive P"),
        ("innovation_v1_no_codec", "Innovations I1"),
        ("innovation_v2", "Innovations I2"),
    ] {
        let a = &s["all"]["arms"][arm];
        let wall = number(&a["wall_seconds"])?;
        table.push_str(&format!(
            "{label} & {} & {wall:.2} & {:.2}\\\\\n",
            count(&a["frames"])?,
            wall / count(&s["all"]["observations"])? as f64
        ));
    }
    table.push_str("\\bottomrule\n\\end{tabular}\n");
    let mut svg = String::from(
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="440" viewBox="0 0 1040 440" role="img" aria-labelledby="title desc">
<title id="title">CANVAS historical replay: unique packets within observations</title>
<desc id="desc">266 exposed development recordings. Progressive: 6220 packets; reference union: 4066. 2158 added, four missed. Not an independent holdout or equal-compute comparison.</desc>
<rect width="1040" height="440" fill="#f4f7fb"/>
<g font-family="Arial, Helvetica, sans-serif" fill="#14253c">
<text x="36" y="45" font-size="15" letter-spacing="2">MEASURED RECOVERY / CANVAS</text>
<text x="36" y="84" font-size="28" font-weight="700">Same recordings. Additional validated packets.</text>
<text x="36" y="114" font-size="16">266 historical observations · unique PDUs counted within each observation</text>
"##,
    );
    let n = count(&s["all"]["ours"])?;
    for (label, value, y, color) in [
        (
            "Reference union",
            count(&s["all"]["baseline"])?,
            177,
            "#73839a",
        ),
        ("Progressive receiver", n, 247, "#175f9e"),
    ] {
        let width = value as f64 / 7000.0 * 590.0;
        svg.push_str(&format!("<text x=\"36\" y=\"{}\" font-size=\"17\">{label}</text><rect x=\"234\" y=\"{y}\" width=\"{width:.3}\" height=\"34\" fill=\"{color}\"/><text x=\"{}\" y=\"{}\" font-size=\"20\" font-weight=\"700\">{value}</text>\n",y+23,248.0+width,y+24));
    }
    svg.push_str(&format!("<text x=\"36\" y=\"336\" font-size=\"22\" font-weight=\"700\">+{} added  /  {} missed  /  +{:.2}% net</text>\n",count(&s["all"]["added"])?,count(&s["all"]["lost"])?,number(&s["all"]["net_percent"])?));
    svg.push_str("<text x=\"36\" y=\"374\" font-size=\"15\">Reference = tested Dire Wolf + gr-satellites union. Previously exposed single-mission cohort.</text>\n<text x=\"36\" y=\"401\" font-size=\"15\">Additional recovery uses substantially more computation; no equal-compute superiority claimed.</text>\n</g></svg>\n");
    fs::create_dir_all(output)?;
    for (name, content) in [
        ("metrics.tex", metrics),
        ("yield-table.tex", table),
        ("yield.svg", svg),
    ] {
        fs::write(output.join(name), content)?;
    }
    println!(
        "Rendered evidence-derived tables, metrics and SVG to {}",
        output.display()
    );
    Ok(())
}

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    match args.as_slice() {
        [mode, root, out] if mode == "freeze" => freeze(Path::new(root), Path::new(out)),
        [mode, out] if mode == "verify" => verify(Path::new(out)),
        [mode, bundle, out] if mode == "render" => render(Path::new(bundle),Path::new(out)),
        [mode, bundle, out] if mode == "render-readme" => {
            verify(Path::new(bundle))?;
            let summary = read(&Path::new(bundle).join("summary.json"))?;
            readme_figures::render(&summary, Path::new(out))
        }
        _ => Err("usage: paper_evidence freeze REPOSITORY NEW_BUNDLE | verify BUNDLE | render BUNDLE GENERATED_DIR | render-readme BUNDLE GENERATED_DIR".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bundle_copy() -> tempfile::TempDir {
        let source =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("publication/decoder-paper-v2/evidence");
        let out = tempfile::tempdir().unwrap();
        for entry in fs::read_dir(source).unwrap() {
            let entry = entry.unwrap();
            fs::copy(entry.path(), out.path().join(entry.file_name())).unwrap();
        }
        out
    }

    fn update_checksum(bundle: &Path, name: &str) {
        let path = bundle.join("checksums.sha256");
        let original = fs::read_to_string(&path).unwrap();
        let lines: String = original
            .lines()
            .map(|line| {
                let (_, file) = line.split_once("  ").unwrap();
                if file == name {
                    format!("{}  {name}\n", sha(&fs::read(bundle.join(name)).unwrap()))
                } else {
                    format!("{line}\n")
                }
            })
            .collect();
        fs::write(path, lines).unwrap();
    }

    #[test]
    fn checked_in_bundle_passes() {
        let bundle = bundle_copy();
        verify(bundle.path()).unwrap();
    }

    #[test]
    fn changed_bytes_fail_checksum() {
        let bundle = bundle_copy();
        fs::write(bundle.path().join("summary.json"), "{}").unwrap();
        assert!(
            verify(bundle.path())
                .unwrap_err()
                .to_string()
                .contains("checksum mismatch")
        );
    }

    #[test]
    fn missing_manifest_entry_fails() {
        let bundle = bundle_copy();
        let path = bundle.path().join("checksums.sha256");
        let original = fs::read_to_string(&path).unwrap();
        fs::write(
            path,
            original
                .lines()
                .filter(|l| !l.ends_with("summary.json"))
                .map(|l| format!("{l}\n"))
                .collect::<String>(),
        )
        .unwrap();
        assert!(
            verify(bundle.path())
                .unwrap_err()
                .to_string()
                .contains("missing manifest entry")
        );
    }

    #[test]
    fn self_consistent_hashes_do_not_hide_bad_totals() {
        let bundle = bundle_copy();
        let path = bundle.path().join("summary.json");
        let mut summary = read(&path).unwrap();
        summary["all"]["ours"] = json!(99999);
        fs::write(path, serde_json::to_vec(&summary).unwrap()).unwrap();
        update_checksum(bundle.path(), "summary.json");
        assert!(
            verify(bundle.path())
                .unwrap_err()
                .to_string()
                .contains("summary mismatch")
        );
    }

    #[test]
    fn duplicate_observation_fails_even_with_new_hash() {
        let bundle = bundle_copy();
        let path = bundle.path().join("observations.json");
        let mut rows = read(&path).unwrap();
        rows[1]["id"] = rows[0]["id"].clone();
        fs::write(path, serde_json::to_vec(&rows).unwrap()).unwrap();
        update_checksum(bundle.path(), "observations.json");
        assert!(
            verify(bundle.path())
                .unwrap_err()
                .to_string()
                .contains("duplicate observation")
        );
    }

    #[test]
    fn generated_assets_are_deterministic() {
        let bundle = bundle_copy();
        let a = tempfile::tempdir().unwrap();
        let b = tempfile::tempdir().unwrap();
        render(bundle.path(), a.path()).unwrap();
        render(bundle.path(), b.path()).unwrap();
        for name in ["metrics.tex", "yield-table.tex", "yield.svg"] {
            assert_eq!(
                fs::read(a.path().join(name)).unwrap(),
                fs::read(b.path().join(name)).unwrap()
            );
        }
    }
    #[test]
    fn additions_and_losses_are_separate() {
        let a = BTreeSet::from(["00".repeat(16), "01".repeat(16)]);
        let b = BTreeSet::from(["00".repeat(16), "02".repeat(16)]);
        let c = comparison(&a, &b);
        assert_eq!(c["added"], 1);
        assert_eq!(c["lost"], 1);
        assert_eq!(c["added_pdu_bytes"], 16);
    }
    #[test]
    fn zero_denominator_is_not_zero_percent() {
        assert_eq!(summarize(&[]).unwrap()["net_percent"], Value::Null);
    }
    #[test]
    fn malformed_packet_is_rejected() {
        assert!(set(&json!(["zz"])).is_err());
        assert!(set(&json!(["00"])).is_err());
    }
    #[test]
    fn existing_evidence_is_never_overwritten() {
        let dir = tempfile::tempdir().unwrap();
        assert!(freeze(dir.path(), dir.path()).is_err());
    }
}
