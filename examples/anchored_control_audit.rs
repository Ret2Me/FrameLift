//! Independent artifact audit: no demodulation and no packet-guided search.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet}, fs, io::{Read, Write}, path::Path};
type R<T> = Result<T, Box<dyn std::error::Error>>;
const BASE: &str = "/home/ubuntu/telemetry-yield";
const DECODER: &str = "e59e8dac6991984de1b7b1e7238835dd3ad740150ae5004070c215290ee2fda3";
const INNOVATION: &str = "638066b9c3edee6227373aa7919c4ee8d9705ca4ad89426f30398987b6e5042a";
const PCM: &str = "b55880744b79706d5df8e8bc4fdded271bc6a3e747b238cabee1b2c28ee6e307";
const OGG: &str = "db9c4f43f5d707847191b6a1ad9e44d99a4a104ef3234767f401c2707e61bd9a";
fn require(ok: bool, why: &str) -> R<()> { if ok { Ok(()) } else { Err(why.into()) } }
fn sha(path: impl AsRef<Path>) -> R<String> {
    let mut file = fs::File::open(path)?; let mut hash = Sha256::new(); let mut buf=[0;65536];
    loop { let n=file.read(&mut buf)?; if n==0 {break;} hash.update(&buf[..n]); }
    Ok(hex::encode(hash.finalize()))
}
fn identity(path: impl AsRef<Path>) -> R<Value> { let p=path.as_ref(); Ok(json!({"path":p,"bytes":fs::metadata(p)?.len(),"sha256":sha(p)?})) }
fn load(p: impl AsRef<Path>) -> R<Value> { Ok(serde_json::from_slice(&fs::read(p)?)?) }
fn a(v:&Value)->R<&Vec<Value>> { v.as_array().ok_or_else(||"missing array".into()) }
fn s(v:&Value)->R<&str> { v.as_str().ok_or_else(||"missing string".into()) }
fn n(v:&Value)->R<u64> { v.as_u64().ok_or_else(||"missing unsigned count".into()) }
fn frames(v:&Value)->R<BTreeSet<String>> {
    let set=a(v)?.iter().map(|x|s(x).map(str::to_owned)).collect::<R<BTreeSet<_>>>()?;
    require(set.len()==a(v)?.len(),"duplicate frame in declared set")?; Ok(set)
}
fn bound(v:&Value)->R<Value> { let id=identity(s(&v["path"])?)?; require(id==*v,"artifact identity mismatch")?; Ok(id) }
fn crc(data:&[u8])->u16 { let mut c=0xffffu16; for b in data { c^=*b as u16; for _ in 0..8 {c=if c&1==1 {(c>>1)^0x8408} else {c>>1};}} !c }
fn frame(f:&str)->R<Value> {
    let b=hex::decode(f)?; require(b.len()>=18,"short frame")?; let p=&b[..b.len()-2];
    require(crc(p)==u16::from_le_bytes(b[b.len()-2..].try_into()?),"received FCS mismatch")?;
    let mut at=0; let mut count=0;
    loop { require(at+7<=p.len(),"truncated address")?;
        require(p[at..at+6].iter().all(|c|c&1==0 && ((*c>>1)==b' ' || (*c>>1).is_ascii_uppercase() || (*c>>1).is_ascii_digit())),"invalid shifted AX25 address")?;
        count+=1; let last=p[at+6]&1==1; at+=7; if last {break;} require(count<10,"too many addresses")?;
    }
    require(count>=2 && at+2<=p.len() && p[at]&0xef==3,"not AX25 UI")?;
    Ok(json!({"frame_sha256":hex::encode(Sha256::digest(&b)),"pdu_sha256":hex::encode(Sha256::digest(p)),"pdu_bytes":p.len(),"received_fcs_le_hex":hex::encode(&b[b.len()-2..]),"independent_bitwise_crc_valid":true,"ax25_ui_structure_valid":true}))
}
fn task(raw:&[u8])->R<Value> {
    let wrapper:Value=serde_json::from_slice(raw)?; let hash=s(&wrapper["sha256"])?;
    let prefix=format!("{{\"sha256\":\"{hash}\",\"task\":");
    require(raw.starts_with(prefix.as_bytes())&&raw.ends_with(b"}"),"noncanonical task wrapper")?;
    let inner=&raw[prefix.len()..raw.len()-1]; require(hex::encode(Sha256::digest(inner))==hash,"canonical task digest mismatch")?;
    let inner:Value=serde_json::from_slice(inner)?; require(inner==wrapper["task"],"task parse mismatch")?; Ok(inner)
}
fn write_new(p:&Path,v:&Value)->R<()> { let mut file=fs::File::create_new(p)?; serde_json::to_writer_pretty(&mut file,v)?;file.write_all(b"\n")?;Ok(()) }
fn separated(a:u64,b:u64)->bool {a*3+7<=b*3 || b*3+7<=a*3}
fn verify_multi_trials(details:&[Value],window:u64,stage:&str,run:&Path,expected:&str)->R<()> {
    let mut banks=BTreeMap::<String,BTreeSet<(u64,String)>>::new();
    for d in details {
        require(d["method"]=="multi-anchor"&&d["window_index"]==window,"not actual multi-anchor trial")?;
        let frontend=s(&d["frontend"])?;
        let ids=a(&d["anchor_ids"])?; require(ids.len()>=2&&ids.len()<=4,"multi trial has fewer than two sources")?;
        let mut windows=BTreeSet::new();
        for id in ids {
            let id=s(id)?;let (prefix,suffix)=id.split_once('/').ok_or("invalid anchor identifier")?;
            require(suffix==frontend,"cross-frontend anchor")?;
            let source:u64=prefix.strip_prefix("window-").ok_or("invalid source-window identifier")?.parse()?;
            require(source<199&&source.abs_diff(window)*3<=60&&separated(source,window),"source-target geometry violates frozen guard")?;
            require(windows.insert(source),"duplicate multi source")?;
            let generations=if stage=="early-multi" {vec!["quick"]}else{vec!["quick","baseline-remainder"]};
            let mut present=false;
            for generation in generations {let p=run.join("tasks").join(format!("{generation}-{source:06}.json"));let t=task(&fs::read(p)?)?;
                if a(&t["detail"]["anchors"])?.iter().any(|v|v["id"]==id) {require(frames(&t["frame_with_fcs_hex"] )?.contains(expected),"source anchor lacks expected valid native frame")?;present=true;}}
            require(present,"multi source absent from bound baseline generation")?;
        }
        for x in &windows {for y in &windows {require(x==y||separated(*x,*y),"multi source windows overlap")?;}}
        let rank=n(&d["timing_rank"])?;let gain=d["gain"].as_f64().ok_or("missing multi gain")?;
        require(rank<8&&[0.75,1.0,1.5].contains(&gain),"wrong multi timing/gain")?;
        require(banks.entry(frontend.into()).or_default().insert((rank,gain.to_string())),"duplicate multi trial")?;
    }
    for bank in banks.values(){require(bank.len()==24,"incomplete executed multi timing/gain bank")?;}Ok(())
}
fn anchored(two:bool)->R<Value> {
    let root=Path::new(BASE); let ready=root.join("work/decoder-readiness-20260911"); let audit=root.join("work/canvas-reference-audit-20260911");
    let plan_name=if two {"two-anchor-noise-plan-v1.json"} else {"anchored-noise-plan-v1.json"};
    let sample_name=if two {"two-anchor-noise-sample-audit-v1.json"} else {"anchored-noise-sample-audit-v1.json"};
    let prefix=if two {"two-anchored"}else{"anchored"};
    let plan=load(ready.join(plan_name))?;
    require(sha(ready.join(plan_name))?==if two {"3edd949a07cd5bbb17a1ecc21dc0907ed65618f7dfdd5d5e35d61ee1cdc3795e"}else{"d1a7c533d9482a94466151e52311ff8f05cc47cea98d1f036823ed8d5f53c6fc"},"anchored plan changed")?;
    bound(&plan["expected_pdu_reference"])?;
    let decoder_path=s(&plan["decoder"]["path"])?; require(sha(decoder_path)?==DECODER,"wrong actual decoder")?;
    let splice=load(ready.join(sample_name))?;
    require(sha(ready.join(sample_name))?==if two {"c0e4f0d200897a2b2bc72eba113f8ddebb5727f5b3a9c2196b74baf3181b6a1e"}else{"c35c46d48d6c7fb1115becdb9740b1a39d3e65cdbc45f7c4ebd72f24ed7b9d0b"},"splice audit changed")?;
    require(splice["status"]=="pass","splice audit failed")?;
    bound(&splice["anchor"])?;
    let expected=fs::read(s(&plan["expected_pdu_reference"]["path"])?)?;
    let mut expected_frame=expected.clone(); expected_frame.extend(crc(&expected).to_le_bytes()); let expected_hex=hex::encode(expected_frame);
    let stages=["quick","baseline-remainder","early-nearest","blind","early-multi","nearest","multi-anchor"];
    let mut cases=Vec::new();
    for kind in ["white","pink","brown","tones"] {
        let run=ready.join(format!("{prefix}-{kind}-full")); let m=load(run.join("manifest.json"))?; let result=load(run.join("result.json"))?;
        let sid=sha(run.join("manifest.json"))?;
        require(m["executable_sha256"]==DECODER && result["session_sha256"]==sid && result["status"]=="complete","session/executable/completion mismatch")?;
        require(m["audio"]["samples"]==28800000 && m["audio"]["sample_rate"]==48000,"wrong geometry")?;
        require(m["policy"]==json!({"anchor_generations":"frozen quick prefix for exploratory tasks; frozen complete baseline for final tasks","baud":9600.0,"blind":true,"fast_gardner":2,"fast_timings":8,"hop_seconds":3.0,"multi_anchor":true,"protocol":"AX25_UI_received_FCS_plain_or_G3RUH","version":2,"window_seconds":6.0}),"wrong full policy")?;
        let source=bound(&m["audio"]["source"])?; require(source==m["audio"]["wav"],"different canonical audio")?;
        let row=a(&splice["cases"])?.iter().find(|v|v[if two {"kind"}else{"case"}]==kind).ok_or("missing splice case")?;
        require(row["composite"]==source && row["exact_sample_composition"]==true && row["verified_samples"]==28800000,"splice does not bind actual input")?; bound(&row["noise"])?;
        let mut keys=BTreeSet::new(); let mut all=BTreeSet::new(); let mut stats=BTreeMap::<String,Value>::new(); let mut stage_sets=BTreeMap::<String,BTreeSet<String>>::new();
        for entry in fs::read_dir(run.join("tasks"))? {
            let p=entry?.path(); if p.extension().is_none_or(|e|e!="json") {continue;}
            let t=task(&fs::read(&p)?)?; let st=s(&t["stage"])?; let win=n(&t["window"])?;
            require(t["session_sha256"]==sid && stages.contains(&st) && win<199,"unexpected task")?;
            require(p.file_name().unwrap().to_str()==Some(&format!("{st}-{win:06}.json")) && keys.insert((st.to_owned(),win)),"duplicate/noncanonical task path")?;
            let set=frames(&t["frame_with_fcs_hex"])?;
            let details=if t["detail"].is_array() {a(&t["detail"])?} else {a(&t["detail"]["trials"])?};
            if two && ["early-multi","multi-anchor"].contains(&st) {verify_multi_trials(details,win,st,&run,&expected_hex)?;}
            let detail_union=details.iter().map(|d|frames(&d["frame_with_fcs_hex"])).collect::<R<Vec<_>>>()?.into_iter().flatten().collect::<BTreeSet<_>>();
            require(detail_union==set,"task summary differs from trial union")?;
            all.extend(set.clone());stage_sets.entry(st.into()).or_default().extend(set);
            let stat=stats.entry(st.into()).or_insert(json!({"tasks":0,"tasks_with_executed_trials":0,"executed_trials":0,"tasks_without_trials":0,"explicit_skip_entries":0}));
            for (field,add) in [("tasks",1),("tasks_with_executed_trials",u64::from(!details.is_empty())),("executed_trials",details.len() as u64),("tasks_without_trials",u64::from(details.is_empty())),("explicit_skip_entries",t["detail"]["skips"].as_array().map_or(0,|a|a.len() as u64))] {stat[field]=json!(n(&stat[field])?+add);}
        }
        require(keys.len()==1393 && result["total_tasks"]==1393 && result["completed_tasks"]==1393,"incomplete task count")?;
        for st in stages { for w in 0..199 {require(keys.contains(&(st.into(),w)),"missing stage/window")?;} require(result["stage_counts"][st]==199,"stage summary count mismatch")?; require(frames(&result["stage_frame_with_fcs_hex"][st])?==stage_sets[st],"stage frame union mismatch")?; }
        require(all==frames(&result["frame_with_fcs_hex"])? && all==BTreeSet::from([expected_hex.clone()]) && result["union_count"]==1,"unexpected or missing accepted frame")?;
        for st in ["nearest","early-nearest"] {require(n(&stats[st]["executed_trials"])? >0,"anchor-nearest stage never executed")?;}
        if two {for st in ["early-multi","multi-anchor"] {require(n(&stats[st]["executed_trials"])? >0,"two-anchor control did not activate multi-anchor stage")?;}}
        let subordinate=load(audit.join(format!("{prefix}-{kind}-independent-audit-v1.json")))?;
        require(subordinate["result_sha256"]==sha(run.join("result.json"))? && subordinate["verified_task_commit_count"]==1393,"subordinate audit mismatch")?;
        cases.push(json!({"kind":kind,"input":source,"manifest":identity(run.join("manifest.json"))?,"result":identity(run.join("result.json"))?,"independent_frame_audit":identity(audit.join(format!("{prefix}-{kind}-independent-audit-v1.json")))?,"verified_canonical_tasks":keys.len(),"stages":stats,"exact_expected_frame":frame(&expected_hex)?,"unexpected_accepted_frames":[],"union_count":1}));
    }
    Ok(json!({"schema":if two {"independent-two-anchor-control-suite-audit-v1"}else{"independent-anchored-control-suite-audit-v1"},"status":"pass","plan":identity(ready.join(plan_name))?,"sample_splice_audit":identity(ready.join(sample_name))?,"actual_decoder":identity(decoder_path)?,"cases":cases,"additional_independent_negative_exposure_hours":0,"limitations":["Semi-synthetic development controls reuse the previous 40 minutes of noise, not another 40 independent minutes.",if two {"Two copies of the same development packet count as one unique PDU; this does not validate codec pooling requiring distinct packets."}else{"A single inserted packet activates nearest-anchor transfer but does not exercise multi-anchor transfer when its two-disjoint-source gate is unsatisfied."},"Current hashes and canonical commitments are consistency evidence, not trusted execution attestation or external preregistration timestamps.","Not an estimate of real-RFI false-alarm rate or a publication/deployment qualification."],"publication_ready":false,"deployment_ready":false}))
}

fn trial_coverage(rows:&Value,skips:&Value,windows:u64,lanes:&[&str],baseline:bool)->R<Value> {
    let fronts=["legacy-fir512","boxcar32-rms50"]; let mut groups=BTreeMap::<(u64,String),BTreeSet<String>>::new(); let mut skipkeys=BTreeSet::new(); let mut unions=BTreeMap::<String,BTreeSet<String>>::new();
    for t in a(rows)? {
        let w=n(&t["window_index"])?; let f=s(&t["frontend"])?;require(w<windows&&fronts.contains(&f)&&t["window_start_seconds"].as_f64()==Some(w as f64*3.0),"invalid trial geometry")?;
        let lane=if baseline {s(&t["clock"])?} else if lanes.len()==1 {lanes[0]} else {s(&t["lane"])?}; require(lanes.contains(&lane),"unexpected lane")?;
        let key=if baseline {let h=if lane=="fixed-full160" {160} else {16};require(t["hypotheses"]==h,"baseline hypothesis count mismatch")?;lane.into()} else {
            let rank=n(&t["timing_rank"])?;let gain=t["gain"].as_f64().ok_or("missing gain")?;require(rank<16&&[0.75,1.0,1.5].contains(&gain),"unexpected transfer timing/gain")?;format!("{lane}/{rank}/{gain}")
        };
        require(groups.entry((w,f.into())).or_default().insert(key),"duplicate timing trial")?;
        unions.entry(lane.into()).or_default().extend(frames(&t["frame_with_fcs_hex"])?);
    }
    for skip in a(skips)? {let k=(n(&skip["window_index"])?,s(&skip["frontend"])?.to_owned());require(k.0<windows&&fronts.contains(&k.1.as_str())&&skip["reason"].as_str().is_some_and(|s|!s.is_empty())&&skipkeys.insert(k.clone())&&!groups.contains_key(&k),"invalid duplicate or overlapping skip")?;}
    for w in 0..windows {for f in fronts {let k=(w,f.into());if let Some(g)=groups.get(&k) {require(g.len()==if baseline {lanes.len()} else {48*lanes.len()},"incomplete timing/gain bank")?;} else {require(!baseline&&skipkeys.contains(&k),"unaccounted window/frontend")?;}}}
    Ok(json!({"executed_trials":a(rows)?.len(),"window_frontends_with_trials":groups.len(),"explicit_skipped_window_frontends":skipkeys.len(),"total_window_frontends":windows*2,"lane_union_full_frames":unions}))
}
fn innovation()->R<Value> {
    let root=Path::new(BASE); let run=root.join("work/decoder-readiness-20260911/repaired-innovation14967362-codec"); let result=load(run.join("result.json"))?;let plan=load(run.join("plan.json"))?;
    require(result["status"]=="complete" && plan["schema"]=="innovation-audio-plan-v2","incomplete or wrong innovation contract")?;
    let exe=bound(&plan["executable"])?;require(exe["sha256"]==INNOVATION,"wrong innovation executable")?;
    let source=bound(&result["source"])?;require(source["sha256"]==PCM&&plan["source"]==source&&plan["input"]==source,"source binding mismatch")?;
    require(plan["codec_input_binding"]==result["codec_input_binding"]&&plan["codec_sideinfo"]==true&&plan["pooled_codec"]==true,"codec contract mismatch")?;
    let binding=&result["codec_input_binding"]; require(binding["analysis_input"]==source&&binding["derived_pcm16"]["sha256"]==PCM&&binding["derived_pcm16"]["bytes"]==source["bytes"]&&binding["kind"]=="canonical_pcm16_wav_byte_identity"&&binding["same_numeric_input_verified"]==true,"invalid codec PCM binding")?;
    let ogg=bound(&binding["codec_source"])?; require(ogg["sha256"]==OGG,"wrong original OGG")?;
    // Replay the declared canonical conversion into a fresh directory: no receiver is run.
    let replay=tempfile::Builder::new().prefix("independent-codec-replay-").tempdir_in(root.join("work/canvas-reference-audit-20260911"))?;
    let wav=replay.path().join("canonical.wav"); let status=std::process::Command::new("/usr/bin/ffmpeg").args(["-nostdin","-v","error","-n","-i",s(&ogg["path"])?,"-map","0:a:0","-c:a","pcm_s16le","-flags:a","+bitexact","-fflags","+bitexact"]).arg(&wav).status()?;
    require(status.success()&&sha(&wav)?==PCM,"independent canonical conversion does not reproduce PCM")?;
    let samples=n(&result["samples"])?;require(result["sample_rate_hz"]==48000&&samples==32929856,"wrong input geometry")?;let windows=(samples-6*48000).div_ceil(3*48000)+1;
    let report=&result["report"];let base=&report["baseline"];require(base["window_count"]==windows&&windows==228,"incorrect physical window count")?;
    let baseline=trial_coverage(&base["baseline_window_trials"],&json!([]),windows,&["fixed-full160","gardner16"],true)?;
    let supplemental=trial_coverage(&base["supplemental_trials"],&base["supplemental_skips"],windows,&["supplemental"],false)?;
    let newer=trial_coverage(&report["trials"],&report["skips"],windows,&["matched-white","innovations"],false)?;
    let baseline_union=a(&base["baseline_window_trials"])?.iter().map(|t|frames(&t["frame_with_fcs_hex"])).collect::<R<Vec<_>>>()?.into_iter().flatten().collect::<BTreeSet<_>>();require(baseline_union==frames(&base["baseline_full_frames"])? ,"baseline union mismatch")?;
    let mut adaptive=baseline_union;for t in a(&base["supplemental_trials"])? {adaptive.extend(frames(&t["frame_with_fcs_hex"])?);}
    require(adaptive==frames(&base["union_full_frames"])?&&adaptive.len() as u64==n(&result["existing_adaptive_count"])? ,"adaptive union mismatch")?;
    let mut union=adaptive;for t in a(&report["trials"])? {union.extend(frames(&t["frame_with_fcs_hex"])?);}
    require(result["codec_fits_accepted"]==0&&result["pooled_fits_accepted"]==0&&frames(&report["lane_full_frames"]["codec-innovations"] )?.is_empty()&&frames(&report["lane_full_frames"]["pooled-codec-innovations"] )?.is_empty(),"unexpected codec fitting: audit extension needed")?;
    for lane in ["matched-white","innovations"] {require(frames(&newer["lane_union_full_frames"][lane])?==frames(&report["lane_full_frames"][lane])?,"innovation lane union mismatch")?;}
    require(union==frames(&report["union_full_frames"])?&&union.len()==4&&result["union_count"]==4,"innovation union mismatch")?;
    let old_path=root.join("work/innovation-benchmark-old20-20260910-v2/comparison/obs-14967362/arms/innovation_v2/attempt-Z9O3gi/decode/result.json");require(sha(&old_path)?=="0cf11736d09ad131bd431953f4f09113c38eb0c6c88db08f1714a83b422d8d20","old regression result changed")?;let old=load(&old_path)?;let old_set=frames(&old["report"]["union_full_frames"])?;require(old["status"]=="complete"&&union==old_set,"repaired and original packet sets differ")?;
    let checked=union.iter().map(|f|frame(f)).collect::<R<Vec<_>>>()?;
    Ok(json!({"schema":"independent-repaired-innovation-regression-audit-v1","status":"pass","result":identity(run.join("result.json"))?,"plan":identity(run.join("plan.json"))?,"actual_executable":exe,"numeric_input":source,"original_ogg":ogg,"canonical_pcm16_conversion_independently_reproduced":true,"canonical_replay_bytes":fs::metadata(&wav)?.len(),"canonical_replay_sha256":sha(&wav)?,"physical_windows":windows,"baseline_coverage":baseline,"supplemental_coverage":supplemental,"innovation_coverage":newer,"frames":checked,"old_result":identity(&old_path)?,"exact_full_frame_sets_equal":true,"added_to_old_result":[],"lost_from_old_result":[],"codec_fits_accepted":0,"pooled_fits_accepted":0,"limitations":["No new demodulation: regression result artifacts, reported trial coverage and independently recomputed CRC audited.","Prototype plan/result has no per-task commit journal or cryptographic execution attestation; complete reported trial bank is weaker than progressive canonical commits.","Current executable and input hashes match the plan; this does not independently attest which binary historically ran.","Zero accepted codec fits means this case does not validate positive codec fitting or establish codec-pooling gain.","Known development observation, not independent scientific holdout."],"publication_ready":false,"deployment_ready":false}))
}
fn main()->R<()> {
    let root=Path::new(BASE).join("work/canvas-reference-audit-20260911");
    let args=std::env::args().skip(1).collect::<Vec<_>>();
    let reports=if args==["--two-anchor-only"] {vec![("two-anchor-control-suite-independent-audit-v1.json",anchored(true)?)]}else if args.is_empty(){vec![("anchored-control-suite-independent-audit-v1.json",anchored(false)?),("repaired-innovation14967362-independent-audit-v1.json",innovation()?)]}else{return Err("usage: anchored_control_audit [--two-anchor-only]".into());};
    for (name,mut report) in reports {
        report["auditor_source_sha256"]=json!(hex::encode(Sha256::digest(include_bytes!("anchored_control_audit.rs"))));
        report["auditor_executable"]=identity(std::env::current_exe()?)?;
        write_new(&root.join(name),&report)?;println!("{}",json!({"receipt":root.join(name),"status":report["status"]}));
    } Ok(())
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn standard_crc(){assert_eq!(crc(b"123456789"),0x906e);}
    #[test] fn multi_sources_need_one_second_guard(){assert!(!separated(50,52));assert!(separated(50,53));assert!(separated(50,66));}
    #[test] fn one_source_is_not_a_multi_trial(){let d=json!({"method":"multi-anchor","window_index":55,"frontend":"legacy-fir512","anchor_ids":["window-50/legacy-fir512"]});assert!(verify_multi_trials(&[d],55,"early-multi",Path::new("/nonexistent"),"").is_err());}
    #[test] fn final_partial_window_is_counted(){assert_eq!((32929856u64-6*48000).div_ceil(3*48000)+1,228);}
    #[test] fn bad_task_hash_rejected(){assert!(task(br#"{"sha256":"bad","task":{}}"#).is_err());}
    #[test] fn exact_task_hash_required(){let inner=b"{\"stage\":\"nearest\"}";let hash=hex::encode(Sha256::digest(inner));let raw=format!("{{\"sha256\":\"{hash}\",\"task\":{}}}",std::str::from_utf8(inner).unwrap());assert_eq!(task(raw.as_bytes()).unwrap()["stage"],"nearest");assert!(task(raw.replace("nearest","other").as_bytes()).is_err());}
    #[test] fn duplicate_declared_frames_rejected(){assert!(frames(&json!(["aa","aa"])).is_err());}
    #[test] fn unaccounted_window_rejected(){assert!(trial_coverage(&json!([]),&json!([]),1,&["supplemental"],false).is_err());}
    #[test] fn explicit_two_frontend_skip_is_complete(){let skip=json!([{"window_index":0,"frontend":"legacy-fir512","reason":"no source"},{"window_index":0,"frontend":"boxcar32-rms50","reason":"no source"}]);assert!(trial_coverage(&json!([]),&skip,1,&["supplemental"],false).is_ok());}
}
