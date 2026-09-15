//! Read-only CANVAS reference diagnostics; never declares a packet from structure alone.
use std::{fs, path::Path, io::{Read, Write, BufReader, BufWriter}};
use telemetry_yield_rs::protocol;
use serde_json::json;
use sha2::{Digest, Sha256};
#[path = "../rust/mm_clock.rs"]
mod mm_clock;

// Separate bitwise implementation for auditing results, not the decoder's table.
fn independent_crc16_x25(bytes: &[u8]) -> u16 {
    let mut crc = 0xffffu16;
    for byte in bytes {
        crc ^= u16::from(*byte);
        for _ in 0..8 { crc = if crc & 1 != 0 { (crc >> 1) ^ 0x8408 } else { crc >> 1 }; }
    }
    !crc
}

fn file_sha256(path: impl AsRef<Path>) -> Result<String, Box<dyn std::error::Error>> {
    let mut input = fs::File::open(path)?;
    let mut hash = Sha256::new();
    let mut block = [0u8; 65536];
    loop { let n = input.read(&mut block)?; if n == 0 { break; } hash.update(&block[..n]); }
    Ok(hex::encode(hash.finalize()))
}

fn checked_task(bytes: &[u8]) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let wrapper: serde_json::Value = serde_json::from_slice(bytes)?;
    let expected = wrapper["sha256"].as_str().ok_or("missing task hash")?;
    let prefix = format!("{{\"sha256\":\"{expected}\",\"task\":");
    if !bytes.starts_with(prefix.as_bytes()) || !bytes.ends_with(b"}") { return Err("noncanonical task wrapper".into()); }
    // Preserve the original typed struct field order and exact f64 spellings.
    let inner = &bytes[prefix.len()..bytes.len()-1];
    if hex::encode(Sha256::digest(inner)) != expected { return Err("task commit hash mismatch".into()); }
    let task: serde_json::Value = serde_json::from_slice(inner)?;
    if task != wrapper["task"] { return Err("task serialization mismatch".into()); }
    Ok(task)
}

fn audit_progressive(root: &Path, reference: &Path, output: &Path) -> Result<(), Box<dyn std::error::Error>> {
    use std::collections::{BTreeMap, BTreeSet};
    let manifest_bytes = fs::read(root.join("manifest.json"))?;
    let manifest: serde_json::Value = serde_json::from_slice(&manifest_bytes)?;
    let session = hex::encode(Sha256::digest(&manifest_bytes));
    let result_bytes = fs::read(root.join("result.json"))?;
    let result: serde_json::Value = serde_json::from_slice(&result_bytes)?;
    if result["session_sha256"] != session || result["status"] != "complete" || result["completed_tasks"] != result["total_tasks"] {
        return Err("incomplete or session-unbound progressive result".into());
    }
    let audio = manifest["audio"]["source"]["path"].as_str().ok_or("missing audio path")?;
    let audio_hash = file_sha256(audio)?;
    if manifest["audio"]["source"]["sha256"] != audio_hash || manifest["audio"]["wav"]["sha256"] != audio_hash {
        return Err("audio source hash mismatch".into());
    }
    let archived = fs::read(reference)?;
    let mut paths = fs::read_dir(root.join("tasks"))?.map(|v|v.map(|v|v.path())).collect::<Result<Vec<_>,_>>()?;
    paths.retain(|p|p.extension().is_some_and(|e|e=="json")); paths.sort();
    let mut union = BTreeSet::<String>::new();
    let mut task_keys = BTreeSet::new();
    let mut provenance = BTreeMap::<String, Vec<serde_json::Value>>::new();
    let mut anchor_details = Vec::new();
    for path in &paths {
        let raw = fs::read(path)?;
        let task = checked_task(&raw)?;
        if task["session_sha256"] != session { return Err("cross-session task".into()); }
        let stage = task["stage"].as_str().ok_or("missing task stage")?;
        let window = task["window"].as_u64().ok_or("missing task window")?;
        if !task_keys.insert((stage.to_string(),window)) { return Err("duplicate task key".into()); }
        for frame in task["frame_with_fcs_hex"].as_array().ok_or("missing task frame set")? {
            let frame = frame.as_str().ok_or("nonstring frame")?.to_string(); union.insert(frame.clone());
            let mut successful = Vec::new();
            if let Some(details) = task["detail"].as_array() {
                for d in details {
                    if d["frame_with_fcs_hex"].as_array().is_some_and(|v|v.iter().any(|f|f==&frame)) {
                        successful.push(json!({"anchor_id":d["anchor_id"],"frontend":d["frontend"],"gain":d["gain"],"timing_rank":d["timing_rank"],"timing":d["timing"]}));
                    }
                }
            }
            provenance.entry(frame).or_default().push(json!({"task":path,"task_file_sha256":hex::encode(Sha256::digest(&raw)),
                "stage":stage,"window":window,"start_seconds":window as f64*manifest["policy"]["hop_seconds"].as_f64().ok_or("missing hop")?,
                "duration_seconds":manifest["policy"]["window_seconds"],"successful_details":successful}));
        }
        if let Some(anchors) = task["detail"]["anchors"].as_array() { anchor_details.extend(anchors.iter().cloned()); }
    }
    let declared: BTreeSet<String> = result["frame_with_fcs_hex"].as_array().ok_or("missing result frames")?.iter()
        .map(|v|v.as_str().map(String::from).ok_or("nonstring result frame")).collect::<Result<_,_>>()?;
    if union != declared || paths.len() as u64 != result["total_tasks"].as_u64().ok_or("missing task total")? {
        return Err("task union/count differs from summary".into());
    }
    let mut frames = Vec::new();
    for frame_hex in &union {
        let full = hex::decode(frame_hex)?;
        if full.len() < 2 { return Err("short frame".into()); }
        let body = &full[..full.len()-2];
        let received = u16::from_le_bytes(full[full.len()-2..].try_into()?);
        let calculated = independent_crc16_x25(body);
        if received != calculated { return Err("independent FCS check failed".into()); }
        let ui = protocol::parse_ax25_ui(body).ok_or("strict AX25 UI check failed")?;
        let info = &ui.information;
        if info.len() < 6 { return Err("short CCSDS header".into()); }
        let first = u16::from_be_bytes([info[0],info[1]]);
        let second = u16::from_be_bytes([info[2],info[3]]);
        let length_field = u16::from_be_bytes([info[4],info[5]]);
        let ccsds_length_consistent = info.len() == usize::from(length_field) + 7;
        if first >> 13 != 0 || !ccsds_length_consistent { return Err("CCSDS primary header inconsistent".into()); }
        frames.push(json!({"frame_with_fcs_hex":frame_hex,"frame_with_fcs_bytes":full.len(),"pdu_bytes":body.len(),
            "frame_sha256":hex::encode(Sha256::digest(&full)),"pdu_sha256":hex::encode(Sha256::digest(body)),
            "received_fcs_le_hex":hex::encode(&full[full.len()-2..]),"independent_calculated_crc16_x25":format!("{calculated:04x}"),"received_fcs_valid":true,
            "exact_archived_pdu":body==archived,"byte_positions_differing_from_archive":body.iter().zip(&archived).filter(|(a,b)|a!=b).count()+body.len().abs_diff(archived.len()),
            "ax25_ui":structure(body),"ccsds":{"version":first>>13,"type":(first>>12)&1,"secondary_header_flag":(first>>11)&1,"apid":first&0x7ff,
                "sequence_flags":second>>14,"sequence_count":second&0x3fff,"data_length_field":length_field,"total_bytes":info.len(),"length_consistent":ccsds_length_consistent,
                "inner_checksum_verified":false,"interpretation":"Primary header structure only; no undocumented inner checksum assumed."},"provenance":provenance[frame_hex]}));
    }
    let report = json!({"schema":"canvas-progressive-independent-frame-audit-v1","result":root.join("result.json"),"result_sha256":hex::encode(Sha256::digest(&result_bytes)),
        "manifest_sha256":session,"manifest_session_binding_valid":true,"executable_sha256":manifest["executable_sha256"],"audio":manifest["audio"],"actual_audio_sha256":audio_hash,
        "archived_pdu":reference,"archived_pdu_sha256":hex::encode(Sha256::digest(&archived)),"verified_task_commit_count":paths.len(),"task_union_matches_summary":true,
        "frames":frames,"anchor_details":anchor_details,"audit_source_sha256":hex::encode(Sha256::digest(include_bytes!("canvas_reference_probe.rs"))),
        "scope":"Posthoc independent checksum/provenance audit only. No decoder invocation or packet-guided recovery. Input identity is explicit in audio metadata; this verifier does not by itself qualify an input as synthetic, lossless IQ-derived, original OGG, or held-out."});
    let mut out = fs::File::create_new(output)?; serde_json::to_writer_pretty(&mut out,&report)?; out.write_all(b"\n")?;
    println!("{}",json!({"audit":output,"verified_tasks":paths.len(),"frames":frames.iter().map(|f|json!({"pdu_sha256":f["pdu_sha256"],"received_fcs_le_hex":f["received_fcs_le_hex"],"exact_archived_pdu":f["exact_archived_pdu"],"sequence_count":f["ccsds"]["sequence_count"]})).collect::<Vec<_>>()}));
    Ok(())
}

fn structure(bytes: &[u8]) -> serde_json::Value {
    protocol::parse_ax25_ui(bytes).map(|p| json!({
        "destination":p.destination.callsign,"destination_ssid":p.destination.ssid,
        "source":p.source.callsign,"source_ssid":p.source.ssid,
        "pid":p.pid,"information_bytes":p.information.len(),
        "information_hex":hex::encode(p.information)
    })).unwrap_or(serde_json::Value::Null)
}

fn matched_slicer_ablation(root: &Path, output: &Path) -> Result<(), Box<dyn std::error::Error>> {
    use telemetry_yield_rs::{dsp, sequence};
    use std::collections::BTreeSet;
    let manifest_bytes = fs::read(root.join("manifest.json"))?;
    let manifest: serde_json::Value = serde_json::from_slice(&manifest_bytes)?;
    let session = hex::encode(Sha256::digest(&manifest_bytes));
    let source = manifest["audio"]["source"]["path"].as_str().ok_or("missing source")?;
    let source_sha = file_sha256(source)?;
    if manifest["audio"]["source"]["sha256"] != source_sha || manifest["audio"]["wav"]["sha256"] != source_sha { return Err("audio source mismatch".into()); }
    let mut reader = hound::WavReader::open(source)?;
    let spec = reader.spec();
    if spec.channels != 1 || spec.sample_rate != 48000 || spec.sample_format != hound::SampleFormat::Float || spec.bits_per_sample != 32 { return Err("matched case requires mono48k float32 WAV".into()); }
    let sample_start = 192 * 48000usize;
    let sample_count = 6 * 48000usize;
    reader.seek(sample_start as u32)?;
    let samples = reader.samples::<f32>().take(sample_count).map(|v|v.map(f64::from)).collect::<Result<Vec<_>,_>>()?;
    if samples.len()!=sample_count || samples.iter().any(|x|!x.is_finite()) { return Err("invalid target window".into()); }
    let config = dsp::DspConfig { baud: manifest["policy"]["baud"].as_f64().ok_or("missing baud")?, bank:"full".into(), ..Default::default() };
    let front = dsp::frontend(&samples,spec.sample_rate,&config)?;
    let mut tasks = Vec::new();
    let mut task_sources = Vec::new();
    for name in ["quick-000067.json","baseline-remainder-000067.json","early-nearest-000064.json","nearest-000064.json"] {
        let path = root.join("tasks").join(name);
        let bytes = fs::read(&path)?;
        let task = checked_task(&bytes)?;
        if task["session_sha256"] != session { return Err("task source session mismatch".into()); }
        task_sources.push(json!({"path":path,"sha256":hex::encode(Sha256::digest(&bytes))})); tasks.push(task);
    }
    let anchor = |task: &serde_json::Value| -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let candidates = task["detail"]["anchors"].as_array().ok_or("missing anchors")?;
        let selected:Vec<_> = candidates.iter().filter(|a|a["id"]=="window-67/legacy-fir512").collect();
        if selected.len()!=1 { return Err("ambiguous anchor generation".into()); } Ok(selected[0].clone())
    };
    let quick_anchor = anchor(&tasks[0])?;
    let remainder_anchor = anchor(&tasks[1])?;
    let q_mse = quick_anchor["model"]["normalized_mse"].as_f64().ok_or("missing model MSE")?;
    let r_mse = remainder_anchor["model"]["normalized_mse"].as_f64().ok_or("missing model MSE")?;
    if !q_mse.is_finite() || !r_mse.is_finite() || q_mse==r_mse { return Err("ambiguous full model selection; tie-breaking not inferred".into()); }
    let complete_anchor = if q_mse<r_mse { &quick_anchor } else { &remainder_anchor };
    let mut rows = Vec::new();
    let mut union_model_slicer = BTreeSet::new();
    let mut union_timing_slicer = BTreeSet::new();
    let mut union_mlse = BTreeSet::new();
    for (generation, selected_anchor, task_index) in [("quick",&quick_anchor,2usize),("complete-baseline",complete_anchor,3usize)] {
        if selected_anchor["window_start_seconds"]!=201.0 || selected_anchor["window_end_seconds"]!=207.0 { return Err("anchor/target separation mismatch".into()); }
        let m = &selected_anchor["model"];
        let f = |v:&serde_json::Value|v.as_f64().ok_or("missing finite parameter");
        let model = sequence::ChannelModel { taps:[f(&m["taps"][0])?,f(&m["taps"][1])?,f(&m["taps"][2])?],bias:f(&m["bias"])?,
            training_symbols:m["training_symbols"].as_u64().ok_or("missing training count")? as usize,normalized_mse:f(&m["normalized_mse"])? };
        let trials = tasks[task_index]["detail"].as_array().ok_or("missing trial list")?;
        if trials.len()!=48 { return Err("expected complete48-trial target bank".into()); }
        for (trial_index, trial) in trials.iter().enumerate() {
            if trial["frontend"]!="legacy-fir512" || trial["anchor_id"]!="window-67/legacy-fir512" || trial["window_index"]!=64 { return Err("unexpected matched-trial contract".into()); }
            let t = &trial["timing"];
            let timing = dsp::TimingHypothesis { rate_error_ppm:f(&t["rate_error_ppm"])?,phase_samples:f(&t["phase_samples"])?,step_samples:f(&t["step_samples"])?,
                threshold:f(&t["threshold"])?,score:f(&t["score"])?,symbol_count:t["symbol_count"].as_u64().ok_or("missing symbol count")? as usize };
            let gain = f(&trial["gain"])?;
            let soft = dsp::soft_symbols(&front,&timing)?;
            let mut soft_hash = Sha256::new(); for symbol in &soft { soft_hash.update(symbol.to_bits().to_le_bytes()); }
            let frames = |v:&[f64],threshold:f64| -> Result<BTreeSet<String>,String> {
                Ok(protocol::decode_ax25(v,threshold,&[false,true])?.into_iter().map(hex::encode).collect())
            };
            // The same sampled target vector and same learned aggregate model
            // are available to both arms. No target bytes are consulted here.
            let model_slicer = frames(&soft,gain*model.bias)?;
            let timing_slicer = frames(&soft,timing.threshold)?;
            let detected = sequence::detect_sequence(&soft,&model,gain)?;
            let mlse = frames(&detected,0.0)?;
            union_model_slicer.extend(model_slicer.iter().cloned());
            union_timing_slicer.extend(timing_slicer.iter().cloned());
            union_mlse.extend(mlse.iter().cloned());
            rows.push(json!({"generation":generation,"task_index":task_index,"trial_index":trial_index,"timing":t,"gain":gain,"model":m,
                "sampled_soft_f64le_sha256":hex::encode(soft_hash.finalize()),"symbols":soft.len(),"model_slicer_threshold":gain*model.bias,
                "model_slicer_frame_with_fcs_hex":model_slicer,"timing_slicer_frame_with_fcs_hex":timing_slicer,"mlse_frame_with_fcs_hex":mlse}));
        }
    }
    // Only after both complete arms finish do expected historical outputs enter
    // posthoc replay verification. They never affect search or frame acceptance.
    for row in &mut rows {
        let task_index = row["task_index"].as_u64().ok_or("invalid task index")? as usize;
        let trial_index = row["trial_index"].as_u64().ok_or("invalid trial index")? as usize;
        let expected = &tasks[task_index]["detail"][trial_index]["frame_with_fcs_hex"];
        if row["mlse_frame_with_fcs_hex"] != *expected { return Err("MLSE replay differs from frozen trial; matched frontend not established".into()); }
        row["exact_frozen_mlse_frame_set_reproduced"] = true.into();
    }
    if file_sha256(source)? != source_sha { return Err("source changed during matched experiment".into()); }
    let report = json!({"schema":"canvas-matched-clock-slicer-vs-mlse-v1","status":"complete","session_sha256":session,"source":source,"source_sha256":source_sha,
        "target_samples":[sample_start,sample_start+sample_count],"target_seconds":[192,198],"anchor_seconds":[201,207],"task_sources":task_sources,
        "completed_paired_trials":rows.len(),"all_frozen_mlse_trial_sets_reproduced":true,"rows":rows,
        "model_bias_slicer_union":union_model_slicer,"recorded_timing_threshold_slicer_union":union_timing_slicer,"mlse_union":union_mlse,
        "model_bias_slicer_count":union_model_slicer.len(),"recorded_timing_threshold_slicer_count":union_timing_slicer.len(),"mlse_count":union_mlse.len(),
        "diagnostic_source_sha256":hex::encode(Sha256::digest(include_bytes!("canvas_reference_probe.rs"))),"diagnostic_executable_sha256":file_sha256(std::env::current_exe()?)?,
        "expected_bytes_used_for_search":false,"bit_repair":false,
        "scope":"Posthoc selected-case matched-clock ablation. All48 original target clock/gain trials from each of two frozen anchor generations. Same soft symbols, gain/bias and protocol acceptance; only sequence memory differs from the model-bias slicer. Not independent test-set or equal-runtime population evidence."});
    let mut out = fs::File::create_new(output)?; serde_json::to_writer_pretty(&mut out,&report)?;out.write_all(b"\n")?;
    println!("{}",json!({"output":output,"paired_trials":rows.len(),"model_bias_slicer_count":union_model_slicer.len(),"timing_slicer_count":union_timing_slicer.len(),"mlse_count":union_mlse.len()}));
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("matched-slicer-ablation") {
        return matched_slicer_ablation(Path::new(args.get(2).ok_or("progressive directory required")?),Path::new(args.get(3).ok_or("output required")?));
    }
    if args.get(1).map(String::as_str) == Some("audit-progressive") {
        return audit_progressive(Path::new(args.get(2).ok_or("progressive directory required")?),
            Path::new(args.get(3).ok_or("archived reference required")?),Path::new(args.get(4).ok_or("output required")?));
    }
    if args.get(1).map(String::as_str) == Some("posthoc-bit-errors") {
        let mut reader=hound::WavReader::open(&args[2])?;
        let spec=reader.spec();
        if spec.sample_format!=hound::SampleFormat::Float || spec.channels!=1 {return Err("mono float WAV required".into());}
        let values=reader.samples::<f32>().map(|v|v.map(f64::from)).collect::<Result<Vec<_>,_>>()?;
        let c=mm_clock::Config{samples_per_symbol:f64::from(spec.sample_rate)/9600.0,initial_phase:0.0,
            gain_omega:std::f64::consts::TAU/100.0,gain_mu:0.0625,relative_limit:0.01,input_gain:1.0,
            interpolation:mm_clock::Interpolation::Linear};
        // Timing decisions run on the complete recording without reference access.
        let soft=mm_clock::recover(&values,c)?;
        let blind_frames=protocol::decode_ax25(&soft,0.0,&[false,true])?;
        let levels:Vec<_>=soft.iter().map(|v|u8::from(*v>=0.0)).collect();
        let mut nrzi=vec![u8::from(levels[0]==0)];nrzi.extend(levels.windows(2).map(|w|u8::from(w[0]==w[1])));
        let mut decoded=nrzi.clone();for i in 17..decoded.len(){decoded[i]^=nrzi[i-12]^nrzi[i-17];}
        // Only now load the known reference for a post-hoc mismatch diagnostic.
        let reference=fs::read(&args[3])?;
        let mut full=reference.clone();full.extend(protocol::crc16_x25(&reference).to_le_bytes());
        let mut expected=Vec::new();let mut ones=0;
        for byte in full {for bit_index in 0..8{let bit=(byte>>bit_index)&1;expected.push(bit);
            ones=if bit==1{ones+1}else{0};if ones==5{expected.push(0);ones=0;}}}
        let start=200*9600usize;let stop=(205*9600usize).min(decoded.len().saturating_sub(expected.len()));
        let mut best=(0usize,usize::MAX);
        for index in start..stop {
            let mut errors=0;
            for (a,b) in decoded[index..index+expected.len()].iter().zip(&expected){errors+=usize::from(a!=b);if errors>best.1{break;}}
            if errors<best.1{best=(index,errors);}
        }
        let mismatch_positions:Vec<_>=decoded[best.0..best.0+expected.len()].iter().zip(&expected).enumerate().filter_map(|(i,(a,b))|(a!=b).then_some(i)).collect();
        let report=json!({"input":args[2],"input_sha256":hex::encode(Sha256::digest(fs::read(&args[2])?)),"reference":args[3],
            "reference_sha256":hex::encode(Sha256::digest(&reference)),"clock":"fixed Linear, phase0, gain1; complete-recording recovery before reference loaded",
            "clock_source_sha256":hex::encode(Sha256::digest(include_bytes!("../rust/mm_clock.rs"))),
            "blind_frame_count":blind_frames.len(),"blind_exact_reference_count":blind_frames.iter().filter(|f|f.len()>=2&&f[..f.len()-2]==reference).count(),
            "symbol_count":levels.len(),"posthoc_search_nominal_seconds":[200,205],"best_stuffed_frame_start_symbol":best.0,
            "expected_stuffed_bits":expected.len(),"best_integer_alignment_mismatch_count":best.1,"mismatch_bit_positions":mismatch_positions,
            "policy":"Known-reference posthoc Hamming diagnostic ONLY; reference not used in timing, blind decoding, acceptance, error correction or claimed yield. Integer alignment does not model bit insertions/deletions."});
        let mut dst=fs::File::create_new(&args[4])?;serde_json::to_writer_pretty(&mut dst,&report)?;dst.write_all(b"\n")?;
        println!("{}",serde_json::to_string_pretty(&report)?);return Ok(());
    }
    if args.get(1).map(String::as_str) == Some("export-historical-ogg-branch") {
        let script=r#"
import importlib.util,sys,math
from pathlib import Path
spec=importlib.util.spec_from_file_location('pinned_satnogs',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
fir_factory=m.gr_filter.fir_filter_ccf
captured=[]
def fir(*a,**kw):
    block=fir_factory(*a,**kw);captured.append(block);return block
m.gr_filter.fir_filter_ccf=fir
graph=m.SatnogsTag15Offline(Path(sys.argv[2]),Path(sys.argv[3]))
assert len(captured)==2
discriminator=m.analog.quadrature_demod_cf(0.9)
resampler=m.gr_filter.pfb.arb_resampler_fff(2.5,[],32)
dc=m.gr_filter.dc_blocker_ff(1024,True)
sink=m.blocks.file_sink(m.gr.sizeof_float,sys.argv[4],False)
graph.connect(captured[1],discriminator,resampler,dc,sink)
graph.run(max_noutput_items=4096)
print('Historical tag1.5 audio branch exported losslessly, 48000 Hz, before Vorbis encoding')
"#;
        for arg in &args[4..6] { if Path::new(arg).exists() { return Err("output already exists".into()); } }
        let status=std::process::Command::new("/usr/bin/python3").arg("-c").arg(script).args(&args[2..6]).status()?;
        if !status.success() {return Err("historical OGG branch export failed".into());}
        return Ok(());
    }
    if args.get(1).map(String::as_str) == Some("correlate") {
        let mut a_reader=hound::WavReader::open(&args[2])?;
        let mut b_reader=hound::WavReader::open(&args[3])?;
        let spec=a_reader.spec();
        if spec.sample_format!=hound::SampleFormat::Float || spec.channels!=1 || b_reader.spec()!=spec {return Err("matching float mono WAV specs required".into());}
        let template_start=args[4].parse::<f64>()?;
        let template_seconds=args[5].parse::<f64>()?;
        let search_start=args[6].parse::<f64>()?;
        let search_seconds=args[7].parse::<f64>()?;
        a_reader.seek((template_start*f64::from(spec.sample_rate)).round() as u32)?;
        b_reader.seek((search_start*f64::from(spec.sample_rate)).round() as u32)?;
        let a=a_reader.samples::<f32>().take((template_seconds*f64::from(spec.sample_rate)).round() as usize).map(|v|v.map(f64::from)).collect::<Result<Vec<_>,_>>()?;
        let b=b_reader.samples::<f32>().take((search_seconds*f64::from(spec.sample_rate)).round() as usize).map(|v|v.map(f64::from)).collect::<Result<Vec<_>,_>>()?;
        if a.is_empty() || b.len()<a.len(){return Err("invalid correlation windows".into());}
        let mean=a.iter().sum::<f64>()/a.len() as f64;
        let energy=a.iter().map(|v|(v-mean).powi(2)).sum::<f64>();
        let n=(a.len()+b.len()-1).next_power_of_two();
        if n>8_388_608 {return Err("correlation FFT exceeds bounded allocation".into());}
        let mut af=vec![rustfft::num_complex::Complex64::new(0.0,0.0);n];
        let mut bf=af.clone();
        for (v,x) in af.iter_mut().zip(&a){v.re=x-mean;}
        for (v,x) in bf.iter_mut().zip(&b){v.re=*x;}
        let mut planner=rustfft::FftPlanner::<f64>::new();
        let fft=planner.plan_fft_forward(n);fft.process(&mut af);fft.process(&mut bf);
        for (v,a) in bf.iter_mut().zip(af){*v*=a.conj();}
        planner.plan_fft_inverse(n).process(&mut bf);
        let mut sum=b[..a.len()].iter().sum::<f64>();
        let mut sq=b[..a.len()].iter().map(|v|v*v).sum::<f64>();
        let mut best=(0usize,0.0f64,0.0f64);
        for i in 0..=b.len()-a.len(){
            let covariance=bf[i].re/n as f64;
            let denom=(energy*(sq-sum*sum/a.len() as f64)).max(0.0).sqrt();
            let corr=if denom>0.0 {covariance/denom}else{0.0};
            if corr.abs()>best.1.abs(){best=(i,corr,covariance/energy);}
            if i+a.len()<b.len(){sum+=b[i+a.len()]-b[i];sq+=b[i+a.len()].powi(2)-b[i].powi(2);}
        }
        let report=json!({"template":args[2],"search":args[3],"template_start_seconds":template_start,"template_seconds":template_seconds,
            "search_start_seconds":search_start,"search_seconds":search_seconds,"sample_rate":spec.sample_rate,
            "best_pearson_correlation":best.1,"best_gain_search_over_template":best.2,
            "best_search_seconds":search_start+best.0 as f64/f64::from(spec.sample_rate),
            "time_shift_search_minus_template_seconds":search_start+best.0 as f64/f64::from(spec.sample_rate)-template_start,
            "fft_length":n,"policy":"reference-guided waveform diagnosis, not independent decoder yield"});
        let mut dst=fs::File::create_new(&args[8])?;serde_json::to_writer_pretty(&mut dst,&report)?;dst.write_all(b"\n")?;
        println!("{}",serde_json::to_string_pretty(&report)?);return Ok(());
    }
    if args.get(1).map(String::as_str) == Some("spectrum") {
        let mut reader=hound::WavReader::open(&args[2])?;
        let spec=reader.spec();
        if spec.sample_format!=hound::SampleFormat::Float || spec.channels!=1 { return Err("mono float WAV required".into()); }
        let start=args[4].parse::<f64>()?;
        let duration=args[5].parse::<f64>()?;
        reader.seek((start*f64::from(spec.sample_rate)).round() as u32)?;
        let x=reader.samples::<f32>().take((duration*f64::from(spec.sample_rate)).round() as usize).collect::<Result<Vec<_>,_>>()?;
        let n=8192usize;
        if x.len()<n {return Err("at least 8192 samples required".into());}
        let fft=rustfft::FftPlanner::<f64>::new().plan_fft_forward(n);
        let mut power=vec![0.0;n/2+1];
        for j in 0..100usize {
            let base=(x.len()-n)*j/99;
            let mut block:Vec<_>=x[base..base+n].iter().enumerate().map(|(i,v)|rustfft::num_complex::Complex64::new(
                f64::from(*v)*(0.5-0.5*(2.0*std::f64::consts::PI*i as f64/(n-1) as f64).cos()),0.0)).collect();
            fft.process(&mut block);
            for (p,b) in power.iter_mut().zip(block) {*p+=b.norm_sqr();}
        }
        let total=power.iter().sum::<f64>();
        let mut bands=Vec::new();
        for low in (0..spec.sample_rate/2).step_by(1000) {
            let high=(low+1000).min(spec.sample_rate/2);
            let sum=power.iter().enumerate().filter(|(i,_)|(*i as f64*f64::from(spec.sample_rate)/n as f64)>=f64::from(low)&&(*i as f64*f64::from(spec.sample_rate)/n as f64)<f64::from(high)).map(|(_,p)|*p).sum::<f64>();
            bands.push(json!({"low_hz":low,"high_hz":high,"fraction":sum/total}));
        }
        let mut peaks:Vec<_>=power.iter().enumerate().collect();
        peaks.sort_by(|a,b|b.1.total_cmp(a.1));
        let report=json!({"input":args[2],"sample_rate":spec.sample_rate,"start_seconds":start,"duration_seconds":x.len() as f64/f64::from(spec.sample_rate),
            "method":"100 uniformly selected Hann8192 Welch-style windows; linear power fractions over nonnegative frequencies",
            "rms":(x.iter().map(|v|f64::from(*v).powi(2)).sum::<f64>()/x.len() as f64).sqrt(),
            "bands":bands,"peaks":peaks.iter().take(10).map(|(i,p)|json!({"hz":*i as f64*f64::from(spec.sample_rate)/n as f64,"fraction":*p/total})).collect::<Vec<_>>()});
        let mut dst=fs::File::create_new(&args[3])?;
        serde_json::to_writer_pretty(&mut dst,&report)?;dst.write_all(b"\n")?;
        println!("{}",serde_json::to_string_pretty(&report)?);
        return Ok(());
    }
    if args.get(1).map(String::as_str) == Some("analytic-export") {
        let mut reader=hound::WavReader::open(&args[2])?;
        let spec=reader.spec();
        if spec.sample_format!=hound::SampleFormat::Float || spec.channels!=1 { return Err("mono float WAV required".into()); }
        let start=args[3].parse::<f64>()?;
        let duration=args[4].parse::<f64>()?;
        let carrier=args[5].parse::<f64>()?;
        reader.seek((start*f64::from(spec.sample_rate)).round() as u32)?;
        let x=reader.samples::<f32>().take((duration*f64::from(spec.sample_rate)).round() as usize).map(|s|s.map(f64::from)).collect::<Result<Vec<_>,_>>()?;
        let iq=telemetry_yield_rs::dsp::analytic_audio(&x,spec.sample_rate,carrier)?;
        let mut dst=BufWriter::new(fs::File::create_new(&args[6])?);
        for v in &iq {dst.write_all(&(v.re as f32).to_le_bytes())?;dst.write_all(&(v.im as f32).to_le_bytes())?;}
        dst.flush()?;
        println!("{}",json!({"input":args[2],"output":args[6],"start_seconds":start,"samples":iq.len(),"sample_rate":spec.sample_rate,"carrier_hz":carrier,
            "contract":"analytic representation of retained real audio, not reconstruction of original RF IQ"}));
        return Ok(());
    }
    if args.get(1).map(String::as_str) == Some("audit-kiss") {
        let bytes=fs::read(&args[2])?;
        let reference=fs::read(&args[3])?;
        let parsed=telemetry_yield_rs::formats::parse_kiss(&bytes)?;
        let report=json!({"path":args[2],"sha256":hex::encode(Sha256::digest(&bytes)),"malformed_records":parsed.malformed_records,
            "timestamp_commands":parsed.timestamp_commands.len(),"other_commands":parsed.other_commands.len(),
            "pdus":parsed.data_frames.iter().map(|f|json!({"bytes":f.payload.len(),"sha256":hex::encode(Sha256::digest(&f.payload)),
                "strict_ui":structure(&f.payload),"exact_reference_match":f.payload==reference,
                "independently_received_fcs_verified":false})).collect::<Vec<_>>()});
        let mut dst=fs::File::create_new(&args[4])?;
        serde_json::to_writer_pretty(&mut dst,&report)?;
        dst.write_all(b"\n")?;
        println!("{}",serde_json::to_string_pretty(&report)?);
        return Ok(());
    }
    if args.get(1).map(String::as_str) == Some("grsat-cf32") {
        let program="/home/ubuntu/telemetry-yield/work/golden/env/bin/gr_satellites";
        if Path::new(&args[4]).exists() || Path::new(&args[5]).exists() { return Err("output already exists".into()); }
        let argv=vec![args[2].clone(),"--rawfile".into(),args[3].clone(),"--samp_rate".into(),"57600".into(),"--iq".into(),"--kiss_out".into(),args[4].clone(),"--hexdump".into()];
        let start=std::time::Instant::now();
        let output=std::process::Command::new("/usr/bin/timeout").arg("120").arg(program).args(&argv)
            .env("GR_SATELLITES_SUBMIT_TLM","0").env("OMP_NUM_THREADS","1").env("OPENBLAS_NUM_THREADS","1").output()?;
        let version=std::process::Command::new(program).arg("--version").output()?;
        let report=json!({"program":program,"args":argv,"version_stdout":String::from_utf8_lossy(&version.stdout),
            "program_sha256":hex::encode(Sha256::digest(fs::read(program)?)),
            "profile_sha256":hex::encode(Sha256::digest(fs::read(&args[2])?)),
            "input_sha256":hex::encode(Sha256::digest(fs::read(&args[3])?)),
            "input_format":"cf32_le interleaved IQ /32768 from source ci16_le; same numeric file as source-compatible comparator",
            "returncode":output.status.code(),"success":output.status.success(),"wall_seconds_concurrent":start.elapsed().as_secs_f64(),
            "stdout":String::from_utf8_lossy(&output.stdout),"stderr":String::from_utf8_lossy(&output.stderr),
            "kiss_bytes":fs::metadata(&args[4]).ok().map(|m|m.len()),
            "kiss_sha256":fs::read(&args[4]).ok().map(|b|hex::encode(Sha256::digest(b))),
            "no_telemetry_submission":true,"timeout_seconds":120});
        let mut dst=fs::File::create_new(&args[5])?;
        serde_json::to_writer_pretty(&mut dst,&report)?;
        dst.write_all(b"\n")?;
        println!("{}",serde_json::to_string_pretty(&report)?);
        if !output.status.success() { return Err("gr-satellites failed".into()); }
        return Ok(());
    }
    if args.get(1).map(String::as_str) == Some("export-satnogs-soft") {
        // External GNU Radio dependency, not a replacement receiver implementation.
        // Capture construction of two pinned blocks and attach observational sinks.
        let script = r#"
import importlib.util, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('pinned_satnogs',sys.argv[1])
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
captured={}
dc_factory=m.gr_filter.dc_blocker_ff
clock_factory=m.digital.clock_recovery_mm_ff
def dc(*a,**kw):
    block=dc_factory(*a,**kw)
    captured['dc']=block
    return block
def clock(*a,**kw):
    block=clock_factory(*a,**kw)
    captured['clock']=block
    return block
m.gr_filter.dc_blocker_ff=dc
m.digital.clock_recovery_mm_ff=clock
graph=m.SatnogsTag15Offline(Path(sys.argv[2]),Path(sys.argv[3]))
before=m.blocks.file_sink(m.gr.sizeof_float,sys.argv[4],False)
after=m.blocks.file_sink(m.gr.sizeof_float,sys.argv[5],False)
graph.connect(captured['dc'],before)
graph.connect(captured['clock'],after)
graph.run(max_noutput_items=4096)
print('Exported unchanged pinned discriminator/DC path at 19200 Hz and M&M output at nominal 9600 Hz')
"#;
        for arg in &args[4..7] { if Path::new(arg).exists() { return Err("output already exists".into()); } }
        let status = std::process::Command::new("/usr/bin/python3").arg("-c").arg(script).args(&args[2..7]).status()?;
        if !status.success() { return Err("GNU Radio export failed".into()); }
        return Ok(());
    }
    if args.get(1).map(String::as_str) == Some("decode-levels") {
        let levels = fs::read(&args[2])?;
        let reference = fs::read(&args[3])?;
        let mut results = Vec::new();
        for g3ruh in [false, true] {
            let frames = protocol::decode_ax25_levels(&levels,g3ruh)?;
            results.push(json!({"g3ruh":g3ruh,"count":frames.len(),"frames":frames.iter().map(|f|json!({
                "received_fcs_valid":protocol::valid_ax25_fcs(f),"received_fcs_le":hex::encode(&f[f.len()-2..]),
                "full_frame_hex":hex::encode(f),"payload_sha256":hex::encode(Sha256::digest(&f[..f.len()-2])),
                "exact_archival_match":f[..f.len()-2]==reference,"strict_ui":structure(&f[..f.len()-2])
            })).collect::<Vec<_>>() }));
        }
        // Locate a known, CRC-verified reference only for diagnostics. This must
        // never be presented as blind recovery or used to tune a held-out set.
        let mut nrzi = vec![u8::from(levels[0]==0)];
        nrzi.extend(levels.windows(2).map(|w|u8::from(w[0]==w[1])));
        let mut decoded=nrzi.clone();
        for i in 17..decoded.len() { decoded[i]^=nrzi[i-12]^nrzi[i-17]; }
        let mut full=reference.clone();
        full.extend(protocol::crc16_x25(&reference).to_le_bytes());
        let mut stuffed=Vec::new();
        let mut ones=0;
        for byte in full { for bit_index in 0..8 {
            let bit=(byte>>bit_index)&1; stuffed.push(bit);
            ones=if bit==1 {ones+1} else {0};
            if ones==5 {stuffed.push(0);ones=0;}
        }}
        let locations:Vec<_>=decoded.windows(stuffed.len()).enumerate().filter_map(|(i,w)|
            (w[..32]==stuffed[..32] && w==stuffed).then_some(json!({"sliced_symbol_index":i,
            "nominal_seconds_at_9600":i as f64/9600.0,"stuffed_frame_bits":stuffed.len()}))).collect();
        let report=json!({"input":args[2],"sliced_bits":levels.len(),"sliced_sha256":hex::encode(Sha256::digest(&levels)),"reference":args[3],"routes":results,
            "reference_diagnostic_locations":locations});
        let mut dst=fs::File::create_new(&args[4])?;
        serde_json::to_writer_pretty(&mut dst,&report)?;
        dst.write_all(b"\n")?;
        println!("{}",serde_json::to_string_pretty(&report)?);
        return Ok(());
    }
    if args.get(1).map(String::as_str) == Some("ci16-to-cf32") {
        let mut src = BufReader::new(fs::File::open(&args[2])?);
        let mut dst = BufWriter::new(fs::File::create_new(&args[3])?);
        let mut bytes = [0u8; 65536];
        let mut samples = 0usize;
        loop {
            let count = src.read(&mut bytes)?;
            if count == 0 { break; }
            if count % 4 != 0 { return Err("unaligned ci16 IQ".into()); }
            for b in bytes[..count].chunks_exact(2) {
                dst.write_all(&(f32::from(i16::from_le_bytes([b[0],b[1]]))/32768.0).to_le_bytes())?;
            }
            samples += count / 4;
        }
        dst.flush()?;
        println!("{}", json!({"input":args[2],"output":args[3],"complex_samples":samples,"scale":1.0/32768.0}));
        return Ok(());
    }
    let reference = Path::new(args.get(1).ok_or("reference path required")?);
    let bytes = fs::read(reference)?;
    let body_without_last_two = bytes.get(..bytes.len().saturating_sub(2)).ok_or("short")?;
    let report = json!({
        "schema":"canvas-reference-audit-v1",
        "path": reference,
        "bytes": bytes.len(),
        "sha256":hex::encode(Sha256::digest(&bytes)),
        "crc16_x25_all_bytes":format!("{:04x}",protocol::crc16_x25(&bytes)),
        "crc16_x25_excluding_last_two":format!("{:04x}",protocol::crc16_x25(body_without_last_two)),
        "last_two_le": bytes.get(bytes.len().saturating_sub(2)..).map(hex::encode),
        "received_fcs_valid_if_last_two_are_fcs":protocol::valid_ax25_fcs(&bytes),
        "strict_ax25_ui_full_artifact":structure(&bytes),
        "strict_ax25_ui_after_last_two_removed":structure(body_without_last_two),
        "policy":"Structure is not CRC proof. No FCS was appended or silently removed."
    });
    if let Some(out) = args.get(2) {
        let mut dst=fs::File::create_new(out)?;
        serde_json::to_writer_pretty(&mut dst,&report)?;
        dst.write_all(b"\n")?;
    }
    println!("{}",serde_json::to_string_pretty(&report)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn crc_known_vector() { assert_eq!(protocol::crc16_x25(b"123456789"),0x906e); }
    #[test]
    fn independent_crc_known_vector_and_empty() {
        assert_eq!(independent_crc16_x25(b"123456789"),0x906e);
        assert_eq!(independent_crc16_x25(b""),0);
    }
    #[test]
    fn task_hash_uses_exact_bytes_and_rejects_mutation() {
        let task = br#"{"schema":1,"value":0.12345678901234567}"#;
        let hash = hex::encode(Sha256::digest(task));
        let raw = format!("{{\"sha256\":\"{hash}\",\"task\":{}}}",std::str::from_utf8(task).unwrap());
        assert!(checked_task(raw.as_bytes()).is_ok());
        assert!(checked_task(raw.replace("schema\":1","schema\":2").as_bytes()).is_err());
    }
    #[test]
    fn correct_fcs_and_bit_error_are_distinguished() {
        let mut bytes=b"123456789".to_vec();
        bytes.extend(protocol::crc16_x25(&bytes).to_le_bytes());
        assert!(protocol::valid_ax25_fcs(&bytes));
        bytes[0]^=1;
        assert!(!protocol::valid_ax25_fcs(&bytes));
    }
}
