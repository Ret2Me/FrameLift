//! Sample-exact two-anchor composite verification; no decoder invocation.
use serde_json::json;
use telemetry_yield_rs::input;
use std::path::Path;
const FIRST:usize=9_648_000; const LAST:usize=9_936_000;const EARLY:usize=7_200_000; const LENGTH:usize=288_000;const TOTAL:usize=28_800_000;
fn offset(i:usize)->Option<usize>{if(EARLY..EARLY+LENGTH).contains(&i){Some(i-EARLY)}else if(FIRST..LAST).contains(&i){Some(i-FIRST)}else{None}}
fn reader(p:&Path)->Result<hound::WavReader<std::io::BufReader<std::fs::File>>,String>{let r=hound::WavReader::open(p).map_err(|e|e.to_string())?;let s=r.spec();if s.channels!=1||s.sample_rate!=48000||s.bits_per_sample!=32||s.sample_format!=hound::SampleFormat::Float{return Err("expected mono48k float32".into());}Ok(r)}
fn run()->Result<(),String>{
 let args=std::env::args().skip(1).collect::<Vec<_>>();if args.len()!=2{return Err("usage ROOT NEW_RECEIPT".into());}let root=Path::new(&args[0]);let pp=root.join("two-anchor-noise-plan-v1.json");let plan=input::read_json(&pp)?;let pi=input::identity(&pp)?;
 if plan["source_interval_samples"]!=json!([FIRST,LAST])||plan["insertions"]!=json!([{"target_samples":[EARLY,EARLY+LENGTH],"target_seconds":[150,156]},{"target_samples":[FIRST,LAST],"target_seconds":[201,207]}])||plan["samples_per_output"]!=TOTAL{return Err("unexpected plan geometry".into());}
 let ap=Path::new(plan["anchor_source"]["path"].as_str().ok_or("missing anchor")?);let ai=input::identity(ap)?;if plan["anchor_source"]["sha256"]!=ai.sha256{return Err("anchor identity changed".into());}
 let mut ar=reader(ap)?;ar.seek(FIRST as u32).map_err(|e|e.to_string())?;let anchor=ar.samples::<f32>().take(LENGTH).collect::<Result<Vec<_>,_>>().map_err(|e|e.to_string())?;if anchor.len()!=LENGTH||anchor.iter().any(|x|!x.is_finite()){return Err("invalid anchor interval".into());}
 let mut rows=Vec::new();for kind in ["white","pink","brown","tones"]{let np=root.join(format!("negative-{kind}-600s.wav"));let cp=root.join(format!("two-anchored-{kind}-600s.wav"));let ni=input::identity(&np)?;let ci=input::identity(&cp)?;if plan["noise_sources"][kind]!=ni.sha256{return Err("noise source mismatch".into());}let mut nr=reader(&np)?;let mut cr=reader(&cp)?;if nr.len()as usize!=TOTAL||cr.len()as usize!=TOTAL{return Err("wrong total length".into());}let mut ns=nr.samples::<f32>();let mut cs=cr.samples::<f32>();for i in 0..TOTAL{let n=ns.next().ok_or("noise EOF")?.map_err(|e|e.to_string())?;let c=cs.next().ok_or("composite EOF")?.map_err(|e|e.to_string())?;let e=offset(i).map_or(n,|o|anchor[o]);if !n.is_finite()||!c.is_finite()||e.to_bits()!=c.to_bits(){return Err(format!("sample mismatch or nonfinite {kind}/{i}"));}}
 if input::identity(&np)?.sha256!=ni.sha256||input::identity(&cp)?.sha256!=ci.sha256{return Err("source changed during audit".into());}rows.push(json!({"kind":kind,"noise":ni,"composite":ci,"verified_samples":TOTAL,"exact_sample_composition":true}));}
 if input::identity(ap)?.sha256!=ai.sha256||input::identity(&pp)?.sha256!=pi.sha256{return Err("anchor or plan changed".into());}
 input::write_json_new(Path::new(&args[1]),&json!({"schema":"two-anchor-noise-sample-audit-v1","status":"pass","plan":pi,"anchor":ai,"cases":rows,"additional_independent_noise_exposure_hours":0,"decoding_performed":false}))
}
fn main(){if let Err(e)=run(){eprintln!("{e}");std::process::exit(1);}}
#[cfg(test)]mod tests{use super::*;#[test]fn exact_half_open_boundaries(){assert_eq!(offset(EARLY-1),None);assert_eq!(offset(EARLY),Some(0));assert_eq!(offset(EARLY+LENGTH-1),Some(LENGTH-1));assert_eq!(offset(EARLY+LENGTH),None);assert_eq!(offset(FIRST-1),None);assert_eq!(offset(FIRST),Some(0));assert_eq!(offset(LAST-1),Some(LENGTH-1));assert_eq!(offset(LAST),None);}}
