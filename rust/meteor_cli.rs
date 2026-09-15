//! Experimental reproducible Meteor LRPT runner; no changes to qualified defaults.
use clap::{Parser, Subcommand, ValueEnum};
use num_complex::Complex64;
use rayon::prelude::*;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    time::Instant,
};
use telemetry_yield_rs::{
    dsp::DspConfig,
    generic::{Demodulator, Signal, Waveform},
    meteor::{self, Frame},
    psk::{MatchedFilter, PskConfig, PskDemodulator},
    space_packet_stream::AosPacketDemux,
};

#[derive(Parser)]
#[command(about = "Experimental Meteor LRPT receiver / matched CADU audit")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}
#[derive(Clone, Copy, Debug, ValueEnum)]
enum Kind {
    Iq,
    /// Headerless little-endian complex float32 (I then Q); requires --sample-rate.
    IqCf32,
    Soft,
}
#[derive(Clone, Copy, Debug, ValueEnum)]
enum Mission {
    MeteorM2,
    MeteorM2x,
}
impl Mission {
    fn profile(self) -> meteor::Profile {
        match self {
            Self::MeteorM2 => meteor::Profile::MeteorM2,
            Self::MeteorM2x => meteor::Profile::MeteorM2x,
        }
    }
}
#[derive(Subcommand)]
enum Command {
    Decode {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, value_enum, default_value = "iq")]
        kind: Kind,
        #[arg(long, value_enum, default_value = "meteor-m2x")]
        profile: Mission,
        #[arg(long)]
        sample_rate: Option<u32>,
        #[arg(long, default_value_t = 0.0)]
        start_seconds: f64,
        #[arg(long)]
        seconds: Option<f64>,
        #[arg(long, default_value_t = 4)]
        workers: usize,
        #[arg(long, default_value_t = 8)]
        sync_errors: u32,
    },
    Compare {
        #[arg(long)]
        baseline: PathBuf,
        #[arg(long)]
        candidate: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    Packets {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Explicit hybrid output, never labelled as standalone native reception.
    Augment {
        #[arg(long)]
        baseline: PathBuf,
        #[arg(long)]
        candidate: PathBuf,
        #[arg(long)]
        baseline_manifest: PathBuf,
        #[arg(long)]
        candidate_report: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
}
fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn hash_file(path: &Path) -> Result<String, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let mut h = Sha256::new();
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        let n = f.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        h.update(&buffer[..n]);
    }
    Ok(hex::encode(h.finalize()))
}
fn new_file(path: &Path, data: &[u8]) -> Result<(), String> {
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .and_then(|mut f| f.write_all(data))
        .map_err(|e| format!("{}: {e}", path.display()))
}
fn payload_hash(f: &Frame) -> String {
    digest(&f.cadu[4..896])
}
fn identity(f: &Frame) -> (u8, u8, u32) {
    (f.spacecraft_id, f.virtual_channel_id, f.frame_counter)
}

fn main() {
    let result = match Cli::parse().command {
        Command::Augment {
            baseline,
            candidate,
            baseline_manifest,
            candidate_report,
            output,
        } => augment(
            &baseline,
            &candidate,
            &baseline_manifest,
            &candidate_report,
            &output,
        ),
        Command::Packets { input, output } => packets(&input, &output),
        Command::Decode {
            input,
            output,
            kind,
            profile,
            sample_rate,
            start_seconds,
            seconds,
            workers,
            sync_errors,
        } => decode(
            &input,
            &output,
            DecodeOptions {
                kind,
                profile: profile.profile(),
                sample_rate,
                start: start_seconds,
                seconds,
                workers,
                sync_errors,
            },
        ),
        Command::Compare {
            baseline,
            candidate,
            output,
        } => compare(&baseline, &candidate, &output),
    };
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}

struct Window {
    index: usize,
    frames: Vec<(Frame, String)>,
    stats: serde_json::Value,
}

fn iq_extent(
    total: usize,
    rate: u32,
    start: f64,
    seconds: Option<f64>,
) -> Result<(usize, usize), String> {
    if !(144000..=9_216_000).contains(&rate)
        || !start.is_finite()
        || start < 0.0
        || seconds.is_some_and(|s| !s.is_finite() || s <= 0.0)
    {
        return Err("invalid IQ sample rate or time interval".into());
    }
    let first = (start * rate as f64).round() as usize;
    if first >= total {
        return Err("start beyond IQ input duration".into());
    }
    let count = seconds
        .map(|s| (s * rate as f64).round() as usize)
        .unwrap_or(total - first)
        .min(total - first);
    if count == 0 || count > 250_000_000 {
        return Err(
            "empty or oversized IQ allocation (limit 250M complex samples); select a time range"
                .into(),
        );
    }
    Ok((first, count))
}

/// Read the original samples without quantization, resampling or clipping.
/// PCM8 is converted by hound from unsigned WAV storage to signed integers.
fn read_iq(
    input: &Path,
    kind: Kind,
    requested_rate: Option<u32>,
    start: f64,
    seconds: Option<f64>,
) -> Result<(Vec<Complex64>, u32), String> {
    match kind {
        Kind::Iq => {
            let mut reader = hound::WavReader::open(input).map_err(|e| e.to_string())?;
            let spec = reader.spec();
            if spec.channels != 2
                || ![8, 16].contains(&spec.bits_per_sample)
                || spec.sample_format != hound::SampleFormat::Int
            {
                return Err("IQ WAV must be stereo PCM8 or PCM16; I is left, Q is right".into());
            }
            let rate = spec.sample_rate;
            if requested_rate.is_some_and(|r| r != rate) {
                return Err("--sample-rate conflicts with the WAV header".into());
            }
            let (first, count) = iq_extent(reader.duration() as usize, rate, start, seconds)?;
            reader.seek(first as u32).map_err(|e| e.to_string())?;
            let scale = (1_u32 << (spec.bits_per_sample - 1)) as f64;
            let mut samples = reader.samples::<i16>();
            let mut iq = Vec::with_capacity(count);
            for _ in 0..count {
                let i = samples
                    .next()
                    .ok_or("truncated I sample")?
                    .map_err(|e| e.to_string())?;
                let q = samples
                    .next()
                    .ok_or("truncated Q sample")?
                    .map_err(|e| e.to_string())?;
                iq.push(Complex64::new(i as f64 / scale, q as f64 / scale));
            }
            Ok((iq, rate))
        }
        Kind::IqCf32 => {
            let rate = requested_rate.ok_or("headerless IQ requires --sample-rate")?;
            let mut file = File::open(input).map_err(|e| e.to_string())?;
            let length = file.metadata().map_err(|e| e.to_string())?.len();
            if length % 8 != 0 {
                return Err("partial CF32 complex sample".into());
            }
            let total = usize::try_from(length / 8).map_err(|e| e.to_string())?;
            let (first, count) = iq_extent(total, rate, start, seconds)?;
            file.seek(SeekFrom::Start(first as u64 * 8))
                .map_err(|e| e.to_string())?;
            let mut iq = Vec::with_capacity(count);
            let mut buffer = vec![0_u8; 1024 * 1024];
            while iq.len() < count {
                let n = (count - iq.len()).min(buffer.len() / 8) * 8;
                file.read_exact(&mut buffer[..n])
                    .map_err(|e| e.to_string())?;
                for chunk in buffer[..n].as_chunks::<8>().0 {
                    let i = f32::from_le_bytes(chunk[..4].try_into().unwrap());
                    let q = f32::from_le_bytes(chunk[4..].try_into().unwrap());
                    if !i.is_finite() || !q.is_finite() {
                        return Err("nonfinite CF32 sample".into());
                    }
                    iq.push(Complex64::new(i as f64, q as f64));
                }
            }
            Ok((iq, rate))
        }
        Kind::Soft => Err("soft input is not IQ".into()),
    }
}

struct DecodeOptions {
    kind: Kind,
    profile: meteor::Profile,
    sample_rate: Option<u32>,
    start: f64,
    seconds: Option<f64>,
    workers: usize,
    sync_errors: u32,
}

fn decode(input: &Path, output: &Path, options: DecodeOptions) -> Result<(), String> {
    let DecodeOptions {
        kind,
        profile,
        sample_rate,
        start,
        seconds,
        workers,
        sync_errors,
    } = options;
    if !start.is_finite()
        || start < 0.0
        || seconds.is_some_and(|s| !s.is_finite() || s <= 0.0)
        || !(1..=32).contains(&workers)
        || sync_errors > 10
    {
        return Err("invalid time, worker count or sync threshold".into());
    }
    if output.exists() {
        return Err("output directory must not exist (protect previous evidence)".into());
    }
    let begun = Instant::now();
    let binary_hash = hash_file(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let input_hash = hash_file(input)?;
    let before = fs::metadata(input).map_err(|e| e.to_string())?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers)
        .build()
        .map_err(|e| e.to_string())?;
    let (windows, rate, duration) = match kind {
        Kind::Iq | Kind::IqCf32 => {
            let (iq, rate) = read_iq(input, kind, sample_rate, start, seconds)?;
            let count = iq.len();
            let modulation = profile.modulation();
            let waveform = Waveform {
                hypothesis_id: format!("meteor-{modulation}-72000-v2"),
                demodulator_id: modulation.into(),
                dsp: DspConfig {
                    baud: 72000.0,
                    mode: modulation.into(),
                    rate_errors_ppm: vec![0.0],
                    phase_bins: 4,
                    top_timing: 4,
                    bank: "full".into(),
                },
                decimation: 1,
                cutoff_hz: None,
                carrier_hz: None,
                mark_hz: 1200.0,
                space_hz: 2200.0,
                psk: Some(PskConfig {
                    matched_filter: MatchedFilter::RootRaisedCosine {
                        rolloff: 0.5,
                        span_symbols: 8,
                    },
                    loop_bandwidth: 0.002,
                    ..Default::default()
                }),
            };
            let stride = (rate as f64 * 0.75).round() as usize;
            let starts: Vec<_> = (0..iq.len()).step_by(stride.max(1)).collect();
            let results:Result<Vec<_>,String>=pool.install(||starts.par_iter().enumerate().map(|(index,&offset)| {
                let end=(offset+rate as usize).min(iq.len());
                if end-offset < (rate as f64*0.13) as usize { return Ok(Window{index,frames:vec![],stats:json!({"index":index,"short_tail":true})}); }
                let mut frames=Vec::new(); let mut candidates=0; let mut attempts=0; let mut rs_rejections=0; let mut asm_rejections=0;
                let streams=PskDemodulator(modulation).visit_soft_symbols(Signal::Iq(&iq[offset..end]),rate,&waveform,&mut |soft,_,_,label| {
                    let scan=meteor::scan_soft_profile(soft,sync_errors,profile)?;
                    candidates+=scan.sync_candidates; attempts+=scan.viterbi_attempts; rs_rejections+=scan.rs_rejections; asm_rejections+=scan.asm_rejections;
                    frames.extend(scan.frames.into_iter().map(|f|(f,label.unwrap_or("unlabelled").to_string())));
                    Ok(())
                })?;
                Ok(Window{index,stats:json!({"index":index,"start_seconds":start+offset as f64/rate as f64,"streams":streams,"sync_candidates":candidates,"viterbi_attempts":attempts,"rs_rejections":rs_rejections,"asm_rejections":asm_rejections,"accepted_including_duplicates":frames.len()}),frames})
            }).collect());
            (results?, rate, count as f64 / rate as f64)
        }
        Kind::Soft => {
            if sample_rate.is_some_and(|r| r != 144000) {
                return Err("Meteor 72k soft input requires 144000 values per second".into());
            }
            let raw = fs::read(input).map_err(|e| e.to_string())?;
            let rate = 144000_u32;
            let first = (start * rate as f64).round() as usize;
            if first >= raw.len() {
                return Err("start beyond soft input duration".into());
            }
            let count = seconds
                .map(|s| (s * rate as f64).round() as usize)
                .unwrap_or(raw.len() - first)
                .min(raw.len() - first);
            let soft: Vec<_> = raw[first..first + count]
                .iter()
                .map(|x| (*x as i8) as f64)
                .collect();
            let starts: Vec<_> = (0..count).step_by(108000).collect();
            let results:Result<Vec<_>,String>=pool.install(||starts.par_iter().enumerate().map(|(index,&offset)|{
                let input=&soft[offset..(offset+144000).min(count)];
                let mut frames=Vec::new();let mut candidates=0;let mut attempts=0;let mut rejected=0;
                // Resolve QPSK quadrant and I/Q ambiguity before channel decoding.
                // scan_soft itself considers either convolutional bit alignment.
                for mapping in 0..8 {
                    let mut mapped=Vec::with_capacity(input.len());
                    for pair in input.as_chunks::<2>().0 {
                        let (i,q)=if mapping&4==0 {(pair[0],pair[1])} else {(pair[1],pair[0])};
                        mapped.push(i*if mapping&1==0 {1.0} else {-1.0});
                        mapped.push(q*if mapping&2==0 {1.0} else {-1.0});
                    }
                    let scan=meteor::scan_soft_profile(&mapped,sync_errors,profile)?;
                    candidates+=scan.sync_candidates;attempts+=scan.viterbi_attempts;rejected+=scan.rs_rejections;
                    frames.extend(scan.frames.into_iter().map(|f|(f,format!("external-soft-component-test-not-native-IQ:map={mapping}"))));
                }
                let stats=json!({"index":index,"sync_candidates":candidates,"viterbi_attempts":attempts,"rs_rejections":rejected,"accepted_including_duplicates":frames.len()});
                Ok(Window{index,stats,frames})
            }).collect());
            (results?, rate, count as f64 / rate as f64)
        }
    };
    let after = fs::metadata(input).map_err(|e| e.to_string())?;
    if before.len() != after.len()
        || before.modified().ok() != after.modified().ok()
        || hash_file(input)? != input_hash
    {
        return Err("input changed during decoding; refusing evidence output".into());
    }
    let mut unique = BTreeMap::<String, (usize, Frame, String)>::new();
    let mut window_stats = Vec::new();
    for w in windows {
        window_stats.push(w.stats);
        for (f, label) in w.frames {
            unique
                .entry(payload_hash(&f))
                .or_insert((w.index, f, label));
        }
    }
    let mut frames: Vec<_> = unique.into_iter().collect();
    frames.sort_by_key(|(_, (w, f, _))| (*w, f.coded_bit_offset));
    let mut identities = BTreeMap::<_, BTreeSet<_>>::new();
    for (hash, (_, f, _)) in &frames {
        identities
            .entry(identity(f))
            .or_default()
            .insert(hash.clone());
    }
    let conflicts: Vec<_> = identities
        .iter()
        .filter(|(_, h)| h.len() > 1)
        .map(|(id, h)| json!({"identity":id,"hashes":h}))
        .collect();
    let mut cadu = Vec::with_capacity(frames.len() * 1024);
    let mut manifest = Vec::new();
    for (hash, (window, f, label)) in &frames {
        cadu.extend_from_slice(&f.cadu);
        manifest.push(json!({"payload_sha256":hash,"window":window,"hypothesis":label,"frame":f}));
    }
    fs::create_dir_all(output.parent().ok_or("output has no parent")?)
        .map_err(|e| e.to_string())?;
    fs::create_dir(output).map_err(|e| e.to_string())?;
    new_file(&output.join("native.cadu"), &cadu)?;
    let report = json!({"schema":"meteor-lrpt-native-v2","experimental":true,"profile":profile,"modulation":profile.modulation(),"argv":std::env::args().collect::<Vec<_>>(),"input":input,"input_sha256":input_hash,"input_bytes":before.len(),"kind":format!("{kind:?}"),"rate":rate,"start_seconds":start,"analyzed_seconds":duration,"workers":workers,"sync_errors":sync_errors,"window_seconds":1.0,"stride_seconds":0.75,"wall_seconds":begun.elapsed().as_secs_f64(),"binary_sha256":binary_hash,"unique_rs_verified_frames":frames.len(),"independent_crc":false,"integrity":"all four conventional-basis RS(255,223) lanes verified; not independent CRC","identity_conflicts":conflicts,"frames":manifest,"windows":window_stats,"cadu_sha256":digest(&cadu)});
    new_file(
        &output.join("report.json"),
        &serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?,
    )?;
    println!(
        "{}",
        json!({"unique_rs_verified_frames":frames.len(),"wall_seconds":begun.elapsed().as_secs_f64(),"report":output.join("report.json")})
    );
    Ok(())
}

fn load_cadu(path: &Path) -> Result<(BTreeMap<String, Frame>, usize, usize), String> {
    let bytes = fs::read(path).map_err(|e| e.to_string())?;
    if bytes.len() % 1024 != 0 {
        return Err(format!("{} has a partial CADU", path.display()));
    }
    let mut unique = BTreeMap::new();
    let mut rejected = 0;
    for cadu in bytes.as_chunks::<1024>().0 {
        match meteor::verify_cadu(cadu) {
            Ok(f) => {
                unique.insert(payload_hash(&f), f);
            }
            Err(_) => rejected += 1,
        }
    }
    Ok((unique, bytes.len() / 1024, rejected))
}
fn compare(baseline: &Path, candidate: &Path, output: &Path) -> Result<(), String> {
    let (base, base_raw, base_bad) = load_cadu(baseline)?;
    let (ours, ours_raw, ours_bad) = load_cadu(candidate)?;
    let gained: Vec<_> = ours
        .keys()
        .filter(|h| !base.contains_key(*h))
        .cloned()
        .collect();
    let lost: Vec<_> = base
        .keys()
        .filter(|h| !ours.contains_key(*h))
        .cloned()
        .collect();
    let mut by_channel = BTreeMap::new();
    let mut identities = BTreeMap::<_, BTreeSet<String>>::new();
    for (hash, frame) in base.iter().chain(ours.iter()) {
        identities
            .entry(identity(frame))
            .or_default()
            .insert(hash.clone());
    }
    let conflicts: Vec<_> = identities
        .iter()
        .filter(|(_, h)| h.len() > 1)
        .map(|(id, h)| json!({"identity":id,"hashes":h}))
        .collect();
    for channel in 0..64_u8 {
        let b = base
            .values()
            .filter(|f| f.virtual_channel_id == channel)
            .count();
        let o = ours
            .values()
            .filter(|f| f.virtual_channel_id == channel)
            .count();
        if b + o > 0 {
            by_channel.insert(channel,json!({"baseline":b,"native":o,"gained":gained.iter().filter(|h|ours[*h].virtual_channel_id==channel).count(),"lost":lost.iter().filter(|h|base[*h].virtual_channel_id==channel).count()}));
        }
    }
    let report = json!({"schema":"meteor-cadu-comparison-v1","baseline":baseline,"candidate":candidate,"baseline_sha256":hash_file(baseline)?,"candidate_sha256":hash_file(candidate)?,"baseline_raw":base_raw,"candidate_raw":ours_raw,"baseline_rejected_rs":base_bad,"candidate_rejected_rs":ours_bad,"baseline_unique":base.len(),"native_unique":ours.len(),"shared":base.len()-lost.len(),"native_only":gained.len(),"baseline_only":lost.len(),"cross_arm_identity_conflicts":conflicts,"by_virtual_channel":by_channel,"gained_payload_hashes":gained,"lost_payload_hashes":lost,"metric":"SHA256 of corrected 892-byte transfer-frame payload, excluding ASM and RS parity","independent_crc":false,"matched_input_must_be_checked_against_run_manifests":true});
    new_file(
        output,
        &serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?,
    )?;
    println!(
        "{}",
        json!({"baseline":base.len(),"native":ours.len(),"gained":gained.len(),"lost":lost.len(),"report":output})
    );
    Ok(())
}

fn packets(input: &Path, output: &Path) -> Result<(), String> {
    if output.exists() {
        return Err("packet output directory must not exist".into());
    }
    let raw = fs::read(input).map_err(|e| e.to_string())?;
    if raw.len() % 1024 != 0 {
        return Err("partial CADU in packet input".into());
    }
    let mut demux = AosPacketDemux::default();
    let mut seen_frames = BTreeSet::new();
    let mut seen_packets = BTreeSet::new();
    let mut archive = Vec::new();
    let mut entries = Vec::new();
    let mut by_apid = BTreeMap::<u16, usize>::new();
    let mut rejected_rs = 0;
    let mut rejected_layout = 0;
    for block in raw.as_chunks::<1024>().0 {
        let frame = match meteor::verify_cadu(block) {
            Ok(f) => f,
            Err(_) => {
                rejected_rs += 1;
                continue;
            }
        };
        if !seen_frames.insert(payload_hash(&frame)) || frame.virtual_channel_id != 5 {
            continue;
        }
        if frame.transfer_frame_version != 1 {
            rejected_layout += 1;
            continue;
        }
        // Managed Meteor layout: 6-byte AOS header, 2-byte insert zone,
        // 2-byte M_PDU header, 882-byte packet zone; no frame-level CRC.
        let fhp = u16::from_be_bytes([frame.cadu[12] & 7, frame.cadu[13]]);
        let decoded = match demux.push(
            frame.spacecraft_id,
            5,
            frame.frame_counter,
            fhp,
            &frame.cadu[14..896],
        ) {
            Ok(p) => p,
            Err(_) => {
                rejected_layout += 1;
                continue;
            }
        };
        for packet in decoded {
            let hash = digest(&packet.bytes);
            if !seen_packets.insert(hash.clone()) {
                continue;
            }
            let offset = archive.len();
            archive.extend_from_slice(&(packet.bytes.len() as u32).to_be_bytes());
            archive.extend_from_slice(&packet.bytes);
            *by_apid.entry(packet.apid).or_default() += 1;
            let payload = &packet.bytes[6..];
            let image_header = (64..=69).contains(&packet.apid)
                && payload.len() > 14
                && payload[9] == 0
                && payload[10] == 0
                && payload[11..13] == [0xff, 0xf0];
            let msu_mr = if image_header {
                json!({"channel":packet.apid-63,"mcu_number":payload[8],"day":u16::from_be_bytes([payload[0],payload[1]]),"milliseconds":u32::from_be_bytes([payload[2],payload[3],payload[4],payload[5]]),"microseconds":u16::from_be_bytes([payload[6],payload[7]]),"quantization_factor":payload[13],"header_only_not_entropy_decode":true})
            } else {
                serde_json::Value::Null
            };
            entries.push(json!({"sha256":hash,"apid":packet.apid,"sequence":packet.sequence,"bytes":packet.bytes.len(),"archive_offset":offset,"ending_frame_counter":frame.frame_counter,"msu_mr":msu_mr}));
        }
    }
    fs::create_dir_all(output.parent().ok_or("packet output has no parent")?)
        .map_err(|e| e.to_string())?;
    fs::create_dir(output).map_err(|e| e.to_string())?;
    new_file(&output.join("packets.bin"), &archive)?;
    let report = json!({"schema":"meteor-space-packets-v1","input":input,"input_sha256":digest(&raw),"archive_format":"repeated big-endian u32 length then complete CCSDS Space Packet bytes","archive_sha256":digest(&archive),"complete_unique_packets":entries.len(),"by_apid":by_apid,"rejected_rs":rejected_rs,"rejected_layout":rejected_layout,"discontinuities":demux.discontinuities,"incomplete_aborts":demux.incomplete_aborts,"invalid_headers":demux.invalid_headers,"pending_at_eof":demux.pending_packets(),"independent_crc":false,"packets":entries});
    new_file(
        &output.join("report.json"),
        &serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?,
    )?;
    println!(
        "{}",
        json!({"complete_unique_packets":entries.len(),"by_apid":by_apid,"report":output.join("report.json")})
    );
    Ok(())
}

fn augment(
    baseline: &Path,
    candidate: &Path,
    baseline_manifest: &Path,
    candidate_report: &Path,
    output: &Path,
) -> Result<(), String> {
    if output.exists() {
        return Err("hybrid output directory must not exist".into());
    }
    let b: serde_json::Value =
        serde_json::from_slice(&fs::read(baseline_manifest).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    let n: serde_json::Value =
        serde_json::from_slice(&fs::read(candidate_report).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    let input_hash = b["input_sha256"]
        .as_str()
        .ok_or("baseline has no input hash")?;
    if input_hash.len() != 64
        || n["input_sha256"].as_str() != Some(input_hash)
        || b["output_cadu_sha256"].as_str() != Some(hash_file(baseline)?.as_str())
        || n["cadu_sha256"].as_str() != Some(hash_file(candidate)?.as_str())
        || !matches!(n["kind"].as_str(), Some("Iq" | "IqCf32"))
        || n["start_seconds"].as_f64() != Some(0.0)
        || (b["input_seconds"]
            .as_f64()
            .ok_or("missing full reference duration")?
            - n["analyzed_seconds"]
                .as_f64()
                .ok_or("missing native duration")?)
        .abs()
            > 0.00001
        || b["exit_code"].as_i64() != Some(0)
    {
        return Err(
            "hybrid requires hash-bound full-length matched IQ inputs and successful baseline"
                .into(),
        );
    }
    let (base, _, base_bad) = load_cadu(baseline)?;
    let (native, _, native_bad) = load_cadu(candidate)?;
    if base_bad + native_bad > 0 {
        return Err("hybrid input contains invalid CADUs".into());
    }
    let gained: Vec<_> = native
        .keys()
        .filter(|h| !base.contains_key(*h))
        .cloned()
        .collect();
    let mut by_id = BTreeMap::<_, (String, Frame)>::new();
    for (hash, frame) in base.iter().chain(native.iter()) {
        let id = identity(frame);
        if by_id.get(&id).is_some_and(|(h, _)| h != hash) {
            return Err("conflicting payloads at the same frame identity; refusing hybrid".into());
        }
        by_id.entry(id).or_insert((hash.clone(), frame.clone()));
    }
    let mut frames: Vec<_> = by_id.into_values().collect();
    frames.sort_by_key(|(_, f)| (f.frame_counter, f.spacecraft_id, f.virtual_channel_id));
    if let (Some((_, first)), Some((_, last))) = (frames.first(), frames.last())
        && last.frame_counter - first.frame_counter >= 0x800000
    {
        return Err(
            "counter-wrap ordering requires a separate time alignment; refusing hybrid".into(),
        );
    }
    let mut cadu = Vec::new();
    for (_, f) in &frames {
        cadu.extend_from_slice(&f.cadu);
    }
    fs::create_dir_all(output.parent().ok_or("hybrid output has no parent")?)
        .map_err(|e| e.to_string())?;
    fs::create_dir(output).map_err(|e| e.to_string())?;
    new_file(&output.join("hybrid.cadu"), &cadu)?;
    let report = json!({"schema":"meteor-explicit-hybrid-v1","label":"SatDump + native recovery (not standalone native)","input_sha256":input_hash,"baseline_manifest_sha256":hash_file(baseline_manifest)?,"native_report_sha256":hash_file(candidate_report)?,"baseline_unique":base.len(),"native_unique":native.len(),"hybrid_unique":frames.len(),"native_added":gained.len(),"added_payload_hashes":gained,"cadu_sha256":digest(&cadu),"independent_crc":false,"identity_conflicts":0});
    new_file(
        &output.join("report.json"),
        &serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?,
    )?;
    println!(
        "{}",
        json!({"hybrid_unique":frames.len(),"native_added":gained.len(),"report":output.join("report.json")})
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pcm8_pcm16_iq_reading_and_rate_validation() {
        let dir = tempfile::tempdir().unwrap();
        for bits in [8, 16] {
            let path = dir.path().join(format!("iq-{bits}.wav"));
            let spec = hound::WavSpec {
                channels: 2,
                sample_rate: 144000,
                bits_per_sample: bits,
                sample_format: hound::SampleFormat::Int,
            };
            let mut writer = hound::WavWriter::create(&path, spec).unwrap();
            let scale = 1_i32 << (bits - 1);
            for value in [-scale, 0, scale / 2, -scale / 2] {
                writer.write_sample(value).unwrap();
            }
            writer.finalize().unwrap();
            let (iq, rate) = read_iq(&path, Kind::Iq, None, 0.0, None).unwrap();
            assert_eq!(rate, 144000);
            assert_eq!(
                iq,
                vec![Complex64::new(-1.0, 0.0), Complex64::new(0.5, -0.5)]
            );
            assert!(read_iq(&path, Kind::Iq, Some(150000), 0.0, None).is_err());
            let (part, _) = read_iq(&path, Kind::Iq, None, 1.0 / 144000.0, None).unwrap();
            assert_eq!(part, iq[1..]);
        }
    }

    #[test]
    fn cf32_preserves_values_and_rejects_invalid_extent() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("iq.cf32");
        let values = [0.12345679_f32, -2.25, 3.125, -0.25];
        let bytes: Vec<_> = values.into_iter().flat_map(f32::to_le_bytes).collect();
        fs::write(&path, &bytes).unwrap();
        let (iq, rate) = read_iq(&path, Kind::IqCf32, Some(144000), 0.0, None).unwrap();
        assert_eq!(rate, 144000);
        assert_eq!(iq[0], Complex64::new(values[0] as f64, values[1] as f64));
        assert_eq!(iq[1], Complex64::new(values[2] as f64, values[3] as f64));
        assert!(read_iq(&path, Kind::IqCf32, None, 0.0, None).is_err());
        fs::write(&path, &bytes[..15]).unwrap();
        assert!(read_iq(&path, Kind::IqCf32, Some(144000), 0.0, None).is_err());
        fs::write(
            &path,
            [f32::NAN.to_le_bytes(), 0_f32.to_le_bytes()].concat(),
        )
        .unwrap();
        assert!(read_iq(&path, Kind::IqCf32, Some(144000), 0.0, None).is_err());
        assert!(iq_extent(250_000_001, 144000, 0.0, None).is_err());
        assert!(iq_extent(100, 144000, 0.0, Some(f64::INFINITY)).is_err());
    }
}
