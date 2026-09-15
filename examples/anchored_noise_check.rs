//! Exact sample-level audit of declared semi-synthetic anchor controls.
use serde_json::json;
use std::path::Path;
use telemetry_yield_rs::input;
const FIRST: usize = 9_648_000;
const LAST: usize = 9_936_000;
const TOTAL: usize = 28_800_000;
fn use_anchor(index: usize) -> bool {
    (FIRST..LAST).contains(&index)
}
fn reader(path: &Path) -> Result<hound::WavReader<std::io::BufReader<std::fs::File>>, String> {
    let r = hound::WavReader::open(path).map_err(|e| e.to_string())?;
    let s = r.spec();
    if s.channels != 1
        || s.sample_rate != 48000
        || s.bits_per_sample != 32
        || s.sample_format != hound::SampleFormat::Float
    {
        return Err("mono48k float32 required".into());
    }
    Ok(r)
}
fn run() -> Result<(), String> {
    let a = std::env::args().skip(1).collect::<Vec<_>>();
    if a.len() != 2 {
        return Err("usage: anchored_noise_check ROOT NEW_RECEIPT.json".into());
    }
    let root = Path::new(&a[0]);
    let plan = input::read_json(&root.join("anchored-noise-plan-v1.json"))?;
    let plan_id = input::identity(&root.join("anchored-noise-plan-v1.json"))?;
    let anchor = Path::new(
        plan["anchor_source"]["path"]
            .as_str()
            .ok_or("anchor path absent")?,
    );
    let anchor_id = input::identity(anchor)?;
    if plan["anchor_source"]["sha256"] != anchor_id.sha256
        || plan["samples_per_output"] != TOTAL
        || plan["anchor_interval_samples"] != json!([FIRST, LAST])
    {
        return Err("composition plan mismatch".into());
    }
    let mut rows = Vec::new();
    for name in ["white", "pink", "brown", "tones"] {
        let noise = root.join(format!("negative-{name}-600s.wav"));
        let mixed = root.join(format!("anchored-{name}-600s.wav"));
        let noise_id = input::identity(&noise)?;
        let mixed_id = input::identity(&mixed)?;
        let mut n = reader(&noise)?;
        let mut m = reader(&mixed)?;
        let mut ar = reader(anchor)?;
        if n.len() as usize != TOTAL || m.len() as usize != TOTAL || (ar.len() as usize) < LAST {
            return Err("sample geometry mismatch".into());
        }
        let mut ni = n.samples::<f32>();
        let mut mi = m.samples::<f32>();
        let mut ai = ar.samples::<f32>();
        for i in 0..TOTAL {
            let nv = ni.next().ok_or("noise EOF")?.map_err(|e| e.to_string())?;
            let mv = mi
                .next()
                .ok_or("composite EOF")?
                .map_err(|e| e.to_string())?;
            let av = if i < LAST {
                Some(ai.next().ok_or("anchor EOF")?.map_err(|e| e.to_string())?)
            } else {
                None
            };
            let expected = if use_anchor(i) {
                av.ok_or("missing anchor sample")?
            } else {
                nv
            };
            if !nv.is_finite()
                || !mv.is_finite()
                || av.is_some_and(|x| !x.is_finite())
                || expected.to_bits() != mv.to_bits()
            {
                return Err(format!("nonfinite or unequal sample {name}/{i}"));
            }
        }
        if input::identity(&noise)?.sha256 != noise_id.sha256
            || input::identity(&mixed)?.sha256 != mixed_id.sha256
        {
            return Err("input changed".into());
        }
        rows.push(json!({"case":name,"noise":noise_id,"composite":mixed_id,"exact_sample_composition":true,"verified_samples":TOTAL}));
    }
    if input::identity(anchor)?.sha256 != anchor_id.sha256
        || input::identity(&root.join("anchored-noise-plan-v1.json"))?.sha256 != plan_id.sha256
    {
        return Err("plan/anchor changed".into());
    }
    input::write_json_new(
        Path::new(&a[1]),
        &json!({"schema":"anchored-noise-sample-audit-v1","status":"pass","plan":plan_id,"anchor":anchor_id,"cases":rows,"additional_independent_noise_exposure_hours":0,"decoding_performed":false}),
    )
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_half_open_anchor_boundary() {
        assert!(!use_anchor(FIRST - 1));
        assert!(use_anchor(FIRST));
        assert!(use_anchor(LAST - 1));
        assert!(!use_anchor(LAST));
    }
}
